#!/usr/bin/env python3
"""Build the products-v1 corpus manifest from the labelling handoff (plan phase P0).

Reads the `approve = yes` rows of the candidate CSVs and resolves each one to an image file
through `CodexReview/review.sqlite3`, then attaches the OCR and page context that the labelling
pipeline already cached. Nothing in the handoff is modified or copied; images stay where they are
and the manifest records their path relative to `image_root`.

An asset is one unique image content (`asset_id` = sha256 of the file bytes, recomputed here).
One asset can depict several entities, so `entity_ids` is a list and every originating CSV row is
kept in `sources`. Rows that cannot become an asset are listed in `missing.jsonl` with a reason.

Output (default `data/processed/products-v1/`):
  assets.jsonl    one asset per line, sorted by asset_id
  entities.jsonl  one entity per line, with row/image counts
  missing.jsonl   yes rows without a usable image, with the reason
  manifest.json   counts, per-file checksums, source fingerprints and the P0 gate checks

Standard library only:
  uv run python scripts/build_corpus_manifest.py \
      --handoff-root ~/Downloads/Handoff_2026-11-17/LabelPortable \
      --csv ~/Downloads/Handoff_2026-11-17/candidate_images_part_01_sorted_approved.csv \
      --csv ~/Downloads/Handoff_2026-11-17/candidate_images_part_04.csv
"""

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

VERSION = "products-v1-corpus-1"
ROOT = Path(__file__).resolve().parents[1]
CSV_COLUMNS = {
    "entity_id": "Entity_ID",
    "entity_name": "Tên entity",
    "image_url": "URL ảnh",
    "width": "Chiều rộng (px)",
    "height": "Chiều cao (px)",
    "page_url": "URL trang nguồn",
    "page_title": "Tiêu đề nguồn",
    "domain": "Tên miền",
    "approve": "approve",
}
RASTER_FORMATS = {"jpeg", "png", "webp", "gif", "avif/heic"}
MAX_PRODUCTS = 5


def sniff_format(head: bytes) -> str:
    """Identify the image container from magic bytes; the file extension is not trusted."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[4:12] in (b"ftypavif", b"ftypheic", b"ftypmif1"):
        return "avif/heic"
    if head.lstrip()[:1] == b"<":
        return "html/svg"
    return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def to_int(value: str) -> int | None:
    return int(value) if value.strip().isdigit() else None


def read_yes_rows(path: Path) -> tuple[list[dict], int]:
    """Return unique `approve = yes` rows and the number of duplicate (entity, url) rows."""
    rows, seen, duplicates = [], set(), 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        absent = [name for name in CSV_COLUMNS.values() if name not in (reader.fieldnames or [])]
        if absent:
            raise SystemExit(f"{path.name}: missing columns {absent}")
        for line, raw in enumerate(reader, start=2):
            if (raw["approve"] or "").strip().lower() != "yes":
                continue
            row = {key: (raw[column] or "").strip() for key, column in CSV_COLUMNS.items()}
            key = (row["entity_id"], row["image_url"])
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            row.update(width=to_int(row["width"]), height=to_int(row["height"]),
                       source_csv=path.name, csv_line=line)
            rows.append(row)
    return rows, duplicates


class ReviewIndex:
    """Read-only view of review.sqlite3: (entity, image URL) -> item id, file and sha256."""

    def __init__(self, db_path: Path):
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.by_pair: dict[tuple[str, str], tuple[str, str, str]] = {}
        self.by_url: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        self.items = 0
        for item_id, data, evidence in db.execute("select id, data, evidence from items"):
            fields, proof = json.loads(data), json.loads(evidence)
            record = (item_id, (proof.get("asset_path") or "").replace("\\", "/"),
                      proof.get("image_sha256") or "")
            self.by_pair.setdefault((fields["entity_id"], fields["image_url"]), record)
            self.by_url[fields["image_url"]].append(record)
            self.items += 1
        db.close()

    def resolve(self, row: dict) -> tuple[tuple[str, str, str] | None, str]:
        record = self.by_pair.get((row["entity_id"], row["image_url"]))
        if record is not None:
            return record, "entity+url"
        # Same image URL reviewed under another entity: the file content is identical.
        shared = sorted(r for r in self.by_url.get(row["image_url"], []) if r[1])
        return (shared[0], "url_only") if shared else (None, "")


def index_ocr(review_dir: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = defaultdict(list)
    for path in sorted((review_dir / "agy").glob("*/ocr/*.json")):
        found[path.stem].append(path)
    return found


def load_ocr(paths: list[Path]) -> list[dict] | None:
    """Best OCR result for an item: prefer one with text, then the most recent run."""
    best = None
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        texts = [{"text": str(box["text"]).strip(), "score": box.get("score")}
                 for box in data.get("ocr") or []
                 if isinstance(box, dict) and str(box.get("text", "")).strip()]
        rank = (bool(texts), str(data.get("at", "")))
        if best is None or rank > best[0]:
            best = (rank, texts)
    return None if best is None else best[1]


class PageContext:
    """Cached page analysis (CodexReview/pages), keyed like the labelling pipeline."""

    def __init__(self, pages_dir: Path):
        self.pages_dir = pages_dir
        self.cache: dict[str, dict | None] = {}

    def load(self, page_url: str) -> dict | None:
        if page_url not in self.cache:
            name = hashlib.sha1(page_url.encode("utf-8")).hexdigest()[:24] + ".json"
            try:
                data = json.loads((self.pages_dir / name).read_text(encoding="utf-8"))
                self.cache[page_url] = {key: data.get(key) for key in (
                    "status", "title", "h1", "og_title", "description", "products")}
            except (OSError, ValueError):
                self.cache[page_url] = None
        return self.cache[page_url]

    def summary(self, page_url: str, image_url: str) -> dict | None:
        page = self.load(page_url) if page_url else None
        if page is None:
            return None
        products = []
        for item in page.get("products") or []:
            if isinstance(item, dict):
                products.append({
                    "type": item.get("type") or "",
                    "name": str(item.get("name") or "")[:200],
                    "brand": str(item.get("brand") or "")[:100],
                    "model": str(item.get("model") or "")[:100],
                    "lists_this_image": image_url in (item.get("images") or []),
                })
        products.sort(key=lambda product: not product["lists_this_image"])
        return {
            "status": page.get("status") or "",
            "title": page.get("title") or "",
            "h1": [str(x) for x in (page.get("h1") or [])][:3],
            "og_title": page.get("og_title") or "",
            "description": str(page.get("description") or "")[:500],
            "products": products[:MAX_PRODUCTS],
        }


def inspect_file(path: Path) -> tuple[str | None, str | None, int]:
    try:
        sha = sha256_file(path)
        with path.open("rb") as stream:
            kind = sniff_format(stream.read(16))
        return sha, kind, path.stat().st_size
    except OSError:
        return None, None, 0


def write_jsonl(path: Path, records: list[dict]) -> str:
    text = "".join(dump(record) + "\n" for record in records)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(handoff_root: Path, csv_paths: list[Path], output: Path, *, workers: int) -> dict:
    review_dir = handoff_root / "CodexReview"
    review = ReviewIndex(review_dir / "review.sqlite3")
    pages = PageContext(review_dir / "pages")
    ocr_files = index_ocr(review_dir)

    rows, duplicate_rows, sources = [], 0, []
    for path in csv_paths:
        part, duplicates = read_yes_rows(path)
        rows.extend(part)
        duplicate_rows += duplicates
        sources.append({"file": path.name, "bytes": path.stat().st_size,
                        "sha256": sha256_file(path), "yes_rows": len(part)})
    names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        names[row["entity_id"]].add(row["entity_name"])
    conflicts = {key: sorted(value) for key, value in names.items() if len(value) > 1}
    if conflicts:
        raise SystemExit(f"Entity ids with conflicting names: {dict(list(conflicts.items())[:3])}")

    resolved = [(row, *review.resolve(row)) for row in rows]
    paths = {handoff_root / record[1] for _, record, _ in resolved if record and record[1]}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        inspected = dict(zip(paths, pool.map(inspect_file, paths), strict=True))

    assets: dict[str, dict] = {}
    missing: list[dict] = []
    hash_mismatch = 0
    for row, record, match in resolved:
        item_id, asset_path, expected = record if record else ("", "", "")
        reason = None
        if record is None:
            reason = "not_in_review_db"
        elif not asset_path:
            reason = "no_asset_path"
        else:
            sha, kind, size = inspected[handoff_root / asset_path]
            if sha is None:
                reason = "file_missing"
            elif kind not in RASTER_FORMATS:
                reason = "not_raster_image"
        if reason:
            missing.append({"entity_id": row["entity_id"], "entity_name": row["entity_name"],
                            "image_url": row["image_url"], "page_url": row["page_url"],
                            "source_csv": row["source_csv"], "item_id": item_id or None,
                            "asset_path": asset_path or None, "reason": reason})
            continue
        hash_mismatch += bool(expected) and sha != expected
        asset = assets.setdefault(sha, {
            "asset_id": sha, "format": kind, "bytes": size, "entity_ids": set(),
            "sources": [], "_items": {}})
        asset["entity_ids"].add(row["entity_id"])
        asset["_items"][item_id] = asset_path
        asset["sources"].append({
            "entity_id": row["entity_id"], "image_url": row["image_url"],
            "page_url": row["page_url"], "page_title": row["page_title"],
            "domain": row["domain"], "width": row["width"], "height": row["height"],
            "source_csv": row["source_csv"], "item_id": item_id, "match": match,
            "page": pages.summary(row["page_url"], row["image_url"]),
        })

    asset_records = []
    for sha in sorted(assets):
        asset = assets.pop(sha)
        items = asset.pop("_items")
        first = min(items)  # deterministic when one content has several review items
        asset["image_path"] = items[first]
        asset["entity_ids"] = sorted(asset["entity_ids"])
        asset["sources"].sort(key=lambda s: (s["entity_id"], s["image_url"]))
        ocr = None
        for item_id in sorted(items):
            texts = load_ocr(ocr_files.get(item_id, []))
            if texts is not None and (ocr is None or (texts and not ocr)):
                ocr = texts
        asset["ocr"] = ocr  # None: never OCR'd; []: OCR'd, no text found
        asset_records.append(asset)

    per_entity = defaultdict(lambda: {"yes_rows": 0, "usable_rows": 0, "missing_rows": 0})
    for row in rows:
        per_entity[row["entity_id"]]["yes_rows"] += 1
    for asset in asset_records:
        for source in asset["sources"]:
            per_entity[source["entity_id"]]["usable_rows"] += 1
    for record in missing:
        per_entity[record["entity_id"]]["missing_rows"] += 1
    images = Counter(eid for asset in asset_records for eid in asset["entity_ids"])
    entity_records = [{"entity_id": eid, "name": next(iter(names[eid])),
                       "assets": images.get(eid, 0), **counts}
                      for eid, counts in sorted(per_entity.items())]
    missing.sort(key=lambda r: (r["entity_id"], r["image_url"]))

    accounted = all(c["yes_rows"] == c["usable_rows"] + c["missing_rows"]
                    for c in per_entity.values())
    usable_rows = sum(len(asset["sources"]) for asset in asset_records)
    checks = {
        "every_yes_row_is_asset_source_or_missing": accounted
        and len(rows) == usable_rows + len(missing),
        "entity_count_matches_csv": len(entity_records) == len(names),
        "asset_ids_are_sha256": all(len(a["asset_id"]) == 64 for a in asset_records),
        "no_sha256_disagreement_with_review_db": hash_mismatch == 0,
    }

    output.mkdir(parents=True, exist_ok=True)
    checksums = {name: write_jsonl(output / name, records) for name, records in (
        ("assets.jsonl", asset_records), ("entities.jsonl", entity_records),
        ("missing.jsonl", missing))}
    without_images = [e for e in entity_records if not e["assets"]]
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "image_root": str(handoff_root),
        "image_path_base": "image_root; asset.image_path is relative to it",
        "counts": {
            "yes_rows": len(rows),
            "duplicate_yes_rows_dropped": duplicate_rows,
            "entities": len(entity_records),
            "assets": len(asset_records),
            "asset_source_rows": usable_rows,
            "assets_shared_by_multiple_entities": sum(len(a["entity_ids"]) > 1
                                                      for a in asset_records),
            "missing_rows": len(missing),
            "missing_by_reason": dict(Counter(r["reason"] for r in missing)),
            "entities_without_assets": len(without_images),
            "assets_with_ocr_text": sum(bool(a["ocr"]) for a in asset_records),
            "assets_with_ocr_file": sum(a["ocr"] is not None for a in asset_records),
            "source_rows_with_page_context": sum(
                s["page"] is not None for a in asset_records for s in a["sources"]),
            "formats": dict(Counter(a["format"] for a in asset_records)),
            "total_bytes": sum(a["bytes"] for a in asset_records),
            "assets_per_entity": _spread(sorted(images.values())),
        },
        "checks": checks,
        "files": checksums,
        "sources": sources,
        "review_db": {"items": review.items},
    }
    temporary = output / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(output / "manifest.json")
    return manifest


def _spread(values: list[int]) -> dict | None:
    return ({"min": values[0], "median": values[len(values) // 2], "max": values[-1]}
            if values else None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--handoff-root", type=Path, required=True,
                        help="the LabelPortable directory (contains CodexReview/)")
    parser.add_argument("--csv", type=Path, action="append", required=True, dest="csvs",
                        help="candidate CSV; repeat for each part")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/products-v1")
    parser.add_argument("--workers", type=int, default=min(16, (os.cpu_count() or 4) * 2))
    args = parser.parse_args()
    manifest = build(args.handoff_root.resolve(), [p.resolve() for p in args.csvs],
                     args.output, workers=args.workers)
    print(json.dumps({"counts": manifest["counts"], "checks": manifest["checks"]},
                     ensure_ascii=False, indent=2))
    if not all(manifest["checks"].values()):
        print("P0 gate FAILED", file=sys.stderr)
        return 1
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
