#!/usr/bin/env python3
"""OCR the corpus images that the labelling pipeline never OCR'd (plan phase P0).

Reads `assets.jsonl` and runs RapidOCR on every asset whose `ocr` is null. Settings match the
labelling pipeline (`LabelPortable/watermark_check.py`: longest side 960 px, no angle classifier,
text cut at 80 characters), so the backfilled results read like the cached ones.

Results go to `<output>/<asset_id>.json`, never into the Handoff. A failed image writes no file and
is listed at the end; rerunning skips files that already exist. Apply them with
`build_corpus_manifest.py --ocr-dir`. Needs numpy, Pillow and rapidocr_onnxruntime:

  python scripts/backfill_ocr.py --corpus data/processed/products-v1 \
      --output data/processed/products-v1/ocr
"""

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
MAX_SIDE, MAX_TEXT = 960, 80
Image.MAX_IMAGE_PIXELS = None
_engine = None


def init_engine() -> None:
    global _engine
    logging.disable(logging.WARNING)
    from rapidocr_onnxruntime import RapidOCR

    _engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)


def ocr_file(path: str) -> list[dict]:
    """[{'box': [x, y, w, h] in 0..1, 'text', 'score'}], same shape as the pipeline's OCR files."""
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    width, height = image.size
    result, _ = _engine(np.asarray(image)[:, :, ::-1], use_cls=False)
    boxes = []
    for quad, text, score in result or []:
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        boxes.append({"box": [round(min(xs) / width, 3), round(min(ys) / height, 3),
                              round((max(xs) - min(xs)) / width, 3),
                              round((max(ys) - min(ys)) / height, 3)],
                      "text": str(text)[:MAX_TEXT], "score": round(float(score), 2)})
    return boxes


def job(task: tuple[str, str, str]) -> tuple[str, str]:
    asset_id, path, output = task
    try:
        boxes = ocr_file(path)
    except Exception as error:
        return asset_id, f"{type(error).__name__}: {error}"[:200]
    record = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "ocr": boxes,
              "engine": {"name": "rapidocr_onnxruntime",
                         "version": version("rapidocr_onnxruntime"),
                         "onnxruntime": version("onnxruntime"), "max_side": MAX_SIDE,
                         "use_cls": False}}
    target = Path(output) / f"{asset_id}.json"
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(json.dumps(record, ensure_ascii=False).encode("utf-8"))
    temporary.replace(target)
    return asset_id, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/processed/products-v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2 - 1))
    parser.add_argument("--limit", type=int, help="only the first N pending assets (timing runs)")
    args = parser.parse_args()

    manifest = json.loads((args.corpus / "manifest.json").read_text(encoding="utf-8"))
    image_root = Path(manifest["image_root"])
    args.output.mkdir(parents=True, exist_ok=True)
    pending = []
    with (args.corpus / "assets.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            asset = json.loads(line)
            if asset["ocr"] is None and not (args.output / f"{asset['asset_id']}.json").exists():
                pending.append((asset["asset_id"], str(image_root / asset["image_path"]),
                                str(args.output)))
    pending.sort()
    if args.limit is not None:
        pending = pending[:args.limit]
    print(f"OCR {len(pending)} images with {args.workers} workers", flush=True)
    failed = {}
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_engine) as pool:
        for number, (asset_id, error) in enumerate(pool.map(job, pending, chunksize=4), 1):
            if error:
                failed[asset_id] = error
            if number % 500 == 0:
                print(f"  {number}/{len(pending)} failed={len(failed)}", flush=True)
    print(json.dumps({"done": len(pending) - len(failed), "failed": failed}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
