"""Download the six-domain Kaggle corpus and build image-level search manifests."""

import argparse
import hashlib
import json
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from threading import Event, Lock
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zipfile import ZipFile

DATASET = "blackcubo/vietnamese-culture-vqa-dataset"
VERSION = 1
PREFIX = "Vietnamese Culture VQA Dataset/"
ANNOTATIONS = "annotations/vietnamese_vqa_dataset.json"
CATEGORIES = {
    "am_thuc": "Ẩm thực",
    "kien_truc": "Kiến trúc",
    "phong_canh": "Phong cảnh",
    "le_hoi": "Lễ hội",
    "trang_phuc": "Trang phục",
    "thu_cong_my_nghe": "Thủ công mỹ nghệ",
}


class RateLimitError(OSError):
    """Kaggle rejected requests; stop this run and preserve the cache."""


class RequestGate:
    def __init__(self, interval: float):
        self.interval = interval
        self.stopped = Event()
        self.lock = Lock()
        self.last_request = 0.0

    def wait(self):
        with self.lock:
            delay = max(0, self.last_request + self.interval - time.monotonic())
            if self.stopped.wait(delay):
                raise RateLimitError("Download stopped after Kaggle HTTP 429; retry later")
            self.last_request = time.monotonic()


def download_archive(target: Path) -> None:
    """Download the official versioned ZIP, retaining partial bytes for resumption."""
    if target.is_file():
        with ZipFile(target) as archive:
            archive.getinfo(PREFIX + ANNOTATIONS)
        print(f"Using cached archive: {target}", flush=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".part")
    url = (f"https://www.kaggle.com/api/v1/datasets/download/{DATASET}"
           f"?datasetVersionNumber={VERSION}")
    for attempt in range(4):
        offset = temporary.stat().st_size if temporary.exists() else 0
        request = Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
        try:
            with urlopen(request, timeout=90) as response:
                if "text/html" in response.headers.get("Content-Type", ""):
                    raise ValueError("Kaggle returned HTML instead of a ZIP archive")
                resumed = response.status == 206 and offset > 0
                if resumed:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise ValueError("Unexpected Content-Range; refusing unsafe resume")
                else:
                    offset = 0  # Server ignored Range; replace the incomplete transfer.
                expected = response.headers.get("Content-Length")
                total = offset + int(expected) if expected else None
                size = offset
                started = last_progress = time.monotonic()
                print(f"Downloading archive: {size:,}/{total or '?'} bytes", flush=True)
                with temporary.open("ab" if resumed else "wb") as output:
                    while block := response.read(8 * 1024 * 1024):
                        output.write(block)
                        size += len(block)
                        now = time.monotonic()
                        if now - last_progress >= 10:
                            speed = (size - offset) / max(now - started, 0.001) / 1024**2
                            print(f"Archive: {size:,}/{total or '?'} bytes; "
                                  f"{speed:.1f} MiB/s", flush=True)
                            last_progress = now
                if total is not None and size != total:
                    raise OSError(f"Incomplete ZIP: {size}/{total} bytes")
            with ZipFile(temporary) as archive:
                archive.getinfo(PREFIX + ANNOTATIONS)
            temporary.replace(target)
            print(f"Archive complete: {target} ({size:,} bytes)", flush=True)
            return
        except HTTPError as exc:
            if exc.code == 429:
                raise RateLimitError("Kaggle HTTP 429; partial ZIP preserved; retry later") from exc
            if exc.code < 500 or attempt == 3:
                raise
        except OSError:
            if attempt == 3:
                raise
        print("Archive transfer interrupted; resuming cached bytes", flush=True)
        time.sleep(2**attempt)


def image_path(record: dict) -> str:
    """Normalize annotation paths; never allow paths outside the corpus."""
    value = record["image_path"]
    if value.startswith("data/"):
        value = value[5:]
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"Unsafe image path: {value}")
    if len(path.parts) < 4 or path.parts[0] != "images":
        raise ValueError(f"Unexpected image path: {value}")
    if path.parts[1] != record["category"]:
        raise ValueError(f"Category/path mismatch: {value}")
    return str(path)


def download(remote: str, target: Path, gate: RequestGate | None = None) -> None:
    """Cache completed files; publish only complete downloads, with bounded retry."""
    if target.is_file() and target.stat().st_size:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    url = (
        f"https://www.kaggle.com/api/v1/datasets/download/{DATASET}/"
        f"{quote(PREFIX + remote, safe='')}?datasetVersionNumber={VERSION}"
    )
    temporary = target.with_name(target.name + ".part")
    for attempt in range(4):
        try:
            if gate:
                gate.wait()
            with urlopen(url, timeout=90) as response, temporary.open("wb") as output:
                if "text/html" in response.headers.get("Content-Type", ""):
                    raise ValueError("Kaggle returned HTML instead of a dataset file")
                size = 0
                while block := response.read(1024 * 1024):
                    output.write(block)
                    size += len(block)
                expected = response.headers.get("Content-Length")
                if not size or (expected and size != int(expected)):
                    raise ValueError("Incomplete download")
            temporary.replace(target)
            return
        except HTTPError as exc:
            temporary.unlink(missing_ok=True)
            if exc.code == 429:
                if gate:
                    gate.stopped.set()
                raise RateLimitError("Kaggle HTTP 429; retry later or use --archive") from exc
            if exc.code < 500 or attempt == 3:
                raise
            time.sleep(2**attempt)
        except RateLimitError:
            raise
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def select_records(records: list[dict], limit: int | None = None) -> list[dict]:
    # Sort by path hash so a small sample covers different subjects reproducibly.
    selected = {}
    for record in records:
        if record.get("category") in CATEGORIES:
            selected.setdefault(image_path(record), record)
    counts = Counter()
    result = []
    for path in sorted(selected, key=lambda p: hashlib.sha256(p.encode()).hexdigest()):
        record = selected[path]
        category = record["category"]
        if limit is None or counts[category] < limit:
            result.append(record)
            counts[category] += 1
    return result


def reconcile_records(records: list[dict], paths: list[str]) -> tuple[list[dict], list[dict]]:
    """Match source annotations to real names; include images without annotations."""
    def canonical(value: str) -> str:
        return unicodedata.normalize("NFC", value).replace("'", "").replace("’", "")

    originals = {image_path(row): row for row in select_records(records)}
    candidates: dict[str, list[str]] = {}
    for name in originals:
        candidates.setdefault(canonical(name), []).append(name)
    reconciled = []
    aliases = []
    matched = set()
    for name in sorted(set(paths)):
        parts = PurePosixPath(name).parts
        extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        if (len(parts) < 4 or parts[0] != "images" or parts[1] not in CATEGORIES
                or PurePosixPath(name).suffix.lower() not in extensions):
            continue
        image_path({"image_path": name, "category": parts[1]})
        source = name if name in originals else None
        alternatives = candidates.get(canonical(name), [])
        if source is None and len(alternatives) == 1:
            source = alternatives[0]
        if source is not None:
            row = dict(originals[source])
            row["annotation_image_path"] = row["image_path"]
            row["image_path"] = "data/" + name
            matched.add(source)
            if source != name:
                aliases.append({"annotation_path": source, "image_path": name,
                                "rule": "unicode-nfc-and-apostrophe-normalization"})
        else:
            subject = parts[-2].replace("_", " ")
            row = {"image_path": "data/" + name, "category": parts[1],
                   "keyword": subject, "image_id": PurePosixPath(name).stem,
                   "metadata": {"annotation_status": "missing", "title_source": "folder"}}
        reconciled.append(row)
    # Keep genuinely missing annotated files visible as failures rather than hiding them.
    reconciled.extend(row for name, row in originals.items() if name not in matched)
    return reconciled, aliases


def make_asset(record: dict, root: Path) -> dict:
    path = image_path(record)
    content = (root / path).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    # A byte-identical image always belongs to the same split, across categories.
    bucket = int(digest[:8], 16) % 100
    analysis = record.get("image_analysis") or {}
    category = record["category"]
    has_annotation = (record.get("metadata") or {}).get("annotation_status") != "missing"
    return {
        "asset_id": digest,
        "sha256": digest,
        "image_path": path,
        "category": category,
        "category_label": CATEGORIES[category],
        "keyword": record.get("keyword", ""),
        "description": analysis.get("overall_description", ""),
        "tags": analysis.get("main_objects", []),
        "split": "train" if bucket < 80 else "validation" if bucket < 90 else "test",
        "source": {
            "dataset": DATASET,
            "version": VERSION,
            "file": PREFIX + path,
            "annotation_image_id": record.get("image_id") if has_annotation else None,
            "declared_license": "Apache-2.0",
            "annotation_image_path": (
                record.get("annotation_image_path", record["image_path"])
                if has_annotation else None
            ),
            "annotation_status": (record.get("metadata") or {}).get(
                "annotation_status", "available"
            ),
        },
        "cultural_context": record.get("cultural_context", {}),
        "questions": record.get("questions", []),
        "annotation_metadata": record.get("metadata", {}),
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_name(path.name + ".part")
    with temporary.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/raw/vietnamese-culture-vqa"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/culture-search"))
    parser.add_argument("--limit-per-category", type=int)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--request-interval", type=float, default=1.0,
                        help="Minimum seconds between network requests across workers")
    parser.add_argument(
        "--archive", type=Path, help="Extract selected files from a local Kaggle ZIP"
    )
    parser.add_argument(
        "--download-archive", type=Path, metavar="ZIP",
        help="Download the full official versioned ZIP to this cache, then extract selected files",
    )
    parser.add_argument("--offline", action="store_true", help="Use downloaded files only")
    args = parser.parse_args()
    if args.workers < 1 or (args.limit_per_category is not None and args.limit_per_category < 1):
        parser.error("workers and limit-per-category must be positive")
    if args.request_interval < 0:
        parser.error("request-interval must be nonnegative")
    if sum(bool(value) for value in (args.archive, args.download_archive, args.offline)) > 1:
        parser.error("choose --archive, --download-archive or --offline")
    if args.download_archive:
        download_archive(args.download_archive)
        args.archive = args.download_archive
    annotation_file = args.root / "vietnamese_vqa_dataset.json"
    if args.archive:
        args.root.mkdir(parents=True, exist_ok=True)
        with ZipFile(args.archive) as archive:
            annotation_file.write_bytes(archive.read(PREFIX + ANNOTATIONS))
    elif not args.offline:
        download(ANNOTATIONS, annotation_file)
    records = json.loads(annotation_file.read_text(encoding="utf-8"))
    inventory = None
    if args.archive:
        with ZipFile(args.archive) as archive:
            inventory = [name[len(PREFIX):] for name in archive.namelist()
                         if name.startswith(PREFIX + "images/") and not name.endswith("/")]
    elif args.offline:
        inventory = [str(path.relative_to(args.root))
                     for category in CATEGORIES
                     for path in (args.root / "images" / category).rglob("*") if path.is_file()]
    reconciled, aliases = (
        reconcile_records(records, inventory) if inventory is not None else (records, [])
    )
    selected = select_records(reconciled, args.limit_per_category)
    selected_paths = {image_path(row) for row in selected}
    aliases = [row for row in aliases if row["image_path"] in selected_paths]
    unannotated = [{"image_path": image_path(row), "title_source": "folder"}
                   for row in selected
                   if (row.get("metadata") or {}).get("annotation_status") == "missing"]
    print(f"Selected {len(selected)} images: {dict(Counter(r['category'] for r in selected))}",
          flush=True)
    if not selected:
        raise ValueError("No records found for the selected categories")
    args.output.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output / "selected_annotations.jsonl", selected)
    write_jsonl(args.output / "path_aliases.jsonl", aliases)
    write_jsonl(args.output / "unannotated_images.jsonl", unannotated)
    failures = []
    if args.archive:
        with ZipFile(args.archive) as archive:
            for index, record in enumerate(selected, 1):
                path = image_path(record)
                target = args.root / path
                if index % 1000 == 0 or index == len(selected):
                    print(f"Extracted/cached {index}/{len(selected)}; "
                          f"failures={len(failures)}", flush=True)
                if target.is_file() and target.stat().st_size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    content = archive.read(PREFIX + path)
                    temporary = target.with_name(target.name + ".part")
                    temporary.write_bytes(content)
                    temporary.replace(target)
                except (KeyError, OSError) as exc:
                    failures.append({"image_path": path, "error": str(exc)})
    elif not args.offline:
        gate = RequestGate(args.request_interval)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs = {pool.submit(download, image_path(r), args.root / image_path(r), gate): r
                    for r in selected}
            for index, future in enumerate(as_completed(jobs), 1):
                try:
                    future.result()
                except Exception as exc:
                    failures.append({"image_path": image_path(jobs[future]), "error": str(exc)})
                    if len(failures) <= 3:
                        print(f"Download error: {exc}", flush=True)
                if index % 100 == 0 or index == len(jobs):
                    print(f"Downloaded/cached {index}/{len(jobs)}; failures={len(failures)}",
                          flush=True)
    assets = {}
    duplicates = []
    failed_paths = {r["image_path"] for r in failures}
    for record in selected:
        path = image_path(record)
        if path in failed_paths:
            continue
        try:
            asset = make_asset(record, args.root)
        except OSError as exc:
            failures.append({"image_path": path, "error": str(exc)})
            continue
        key = asset["asset_id"]
        if key in assets:
            duplicates.append({"image_path": path, "asset_id": key,
                               "category": record["category"]})
        else:
            assets[key] = asset
    args.output.mkdir(parents=True, exist_ok=True)
    rows = sorted(assets.values(), key=lambda r: r["asset_id"])
    write_jsonl(args.output / "assets.jsonl", rows)
    write_jsonl(args.output / "failures.jsonl", failures)
    write_jsonl(args.output / "duplicates.jsonl", duplicates)
    report = {
        "dataset": DATASET, "version": VERSION,
        "image_root": str(args.root.resolve()),
        "annotation_records": len(records), "selected_images": len(selected),
        "unique_assets": len(rows), "duplicates": len(duplicates), "failures": len(failures),
        "available_images": len(rows) + len(duplicates),
        "complete": not failures,
        "selected_categories": dict(Counter(r["category"] for r in selected)),
        "path_aliases": len(aliases), "unannotated_images": len(unannotated),
        "categories": dict(Counter(r["category"] for r in rows)),
        "splits": dict(Counter(r["split"] for r in rows)),
        "split_policy": "sha256-mod100: train 80%, validation 10%, test 10%; custom splits",
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
