"""Make every corpus thumbnail ahead of time, so no visitor waits for a resize.

Uses the API's own function and cache (services/api/.../thumbnails.py). Safe to stop and run
again: finished files are skipped. The cache can be deleted at any time.

  python scripts/build_thumbnails.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from visualseek_api.application.thumbnails import thumbnail  # noqa: E402
from visualseek_api.core.config import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    settings = get_settings()
    search = ROOT / settings.search_artifact_dir
    image_root = Path(
        settings.products_image_root
        or json.loads((search.parent / "manifest.json").read_text(encoding="utf-8"))["image_root"]
    )
    rows = [
        json.loads(line)
        for line in (search / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    cache = ROOT / settings.thumbs_dir

    def build(row: dict) -> str:
        source = image_root / row["image_path"]
        if not source.exists():
            return "missing"
        result = thumbnail(source, row["asset_id"], cache)
        return "kept_original" if result == source else "thumbnail"

    counts: dict[str, int] = {}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for done, outcome in enumerate(pool.map(build, rows), 1):
            counts[outcome] = counts.get(outcome, 0) + 1
            if done % 2000 == 0 or done == len(rows):
                print(f"{done}/{len(rows)} {counts} {time.perf_counter() - started:.0f}s", flush=True)
    size = sum(p.stat().st_size for p in cache.rglob("*.webp"))
    print(f"cache {size / 1024**2:.0f} MB in {cache}")


if __name__ == "__main__":
    main()
