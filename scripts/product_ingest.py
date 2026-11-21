"""Select approved product-entity rows, mirror the images locally and write a manifest.

Rows are taken whole-entity in CSV order until `--rows` approved rows are reached, so
leave-one-out evaluation never sees a half-sampled entity. Re-running is safe: images that
already exist on disk are not downloaded again.
"""

import argparse
import csv
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image, ImageOps

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) product-image-ingest",
    "Accept": "image/webp,image/png,image/jpeg,*/*",
}
MAX_SIDE = 1024
MIN_SIDE = 64


def select_rows(csv_path: Path, rows: int | None) -> list[dict]:
    """Approved rows, URL-deduplicated, grouped by entity, whole entities only."""
    by_entity: dict[str, list[dict]] = {}
    seen_urls: set[str] = set()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["approve"].strip().lower() != "yes":
                continue
            url = row["URL ảnh"].strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            by_entity.setdefault(row["Entity_ID"], []).append(row)
    selected: list[dict] = []
    for entity_rows in by_entity.values():
        if rows is not None and len(selected) >= rows:
            break
        selected.extend(entity_rows)
    return selected


def mirror(row: dict, images_dir: Path) -> dict:
    url = row["URL ảnh"].strip()
    image_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    record = {
        "image_id": image_id,
        "entity_id": row["Entity_ID"],
        "entity_name": row["Tên entity"].strip(),
        "url": url,
        "source_url": row["URL trang nguồn"].strip(),
        "source_title": row["Tiêu đề nguồn"].strip(),
        "domain": row["Tên miền"].strip(),
        "path": f"images/{image_id}.jpg",
    }
    target = images_dir / f"{image_id}.jpg"
    try:
        if not target.exists():
            with httpx.Client(timeout=20, follow_redirects=True, headers=HEADERS) as client:
                response = client.get(url)
            if response.status_code != 200:
                return {**record, "ok": False, "error": f"http_{response.status_code}"}
            with Image.open(io.BytesIO(response.content)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
            if min(image.size) < MIN_SIDE:
                return {**record, "ok": False, "error": "too_small"}
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            image.save(target, quality=90)
        with Image.open(target) as saved:
            width, height = saved.size
        record["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        return {**record, "ok": True, "width": width, "height": height}
    except Exception as exc:  # noqa: BLE001 - one bad URL must not stop the batch
        return {**record, "ok": False, "error": type(exc).__name__}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path("candidate_images_part_01_sorted_approved.csv"))
    parser.add_argument("--out", type=Path, default=Path("data/product-images"))
    parser.add_argument("--rows", type=int, default=500, help="approved rows to take; 0 means all")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    images_dir = args.out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    rows = select_rows(args.csv, args.rows or None)
    with ThreadPoolExecutor(args.workers) as pool:
        records = []
        for done, record in enumerate(pool.map(lambda row: mirror(row, images_dir), rows), 1):
            records.append(record)
            if done % 1000 == 0:
                print(f"[mirror] {done}/{len(rows)}", flush=True)

    # Same bytes twice inside one entity add nothing to retrieval or leave-one-out.
    seen: set[tuple[str, str]] = set()
    for record in records:
        if record["ok"]:
            key = (record["entity_id"], record["sha256"])
            if key in seen:
                record.update(ok=False, error="duplicate_in_entity")
            seen.add(key)
    manifest = args.out / "manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )

    ok = [r for r in records if r["ok"]]
    errors: dict[str, int] = {}
    for record in records:
        if not record["ok"]:
            errors[record["error"]] = errors.get(record["error"], 0) + 1
    print(json.dumps({
        "selected_rows": len(records),
        "entities": len({r["entity_id"] for r in records}),
        "ok": len(ok),
        "ok_entities": len({r["entity_id"] for r in ok}),
        "failures": errors,
        "manifest": str(manifest),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
