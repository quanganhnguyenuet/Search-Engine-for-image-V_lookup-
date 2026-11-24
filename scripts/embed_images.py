"""Embed every corpus image with SigLIP2 (image tower) for the ``img`` named vector.

Row order = ``doc_texts.jsonl`` (sorted asset_id), the same order the Qdrant ingest uses.
Images that fail to decode are listed in the manifest and get a zero vector, never a
placeholder picture, so they cannot match anything by accident.

Outputs in ``<artifacts>``:
  img_siglip2.npy        float16 [assets, 768], L2-normalised
  img_manifest.json      model, revision, counts, failures, asset order fingerprint
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "data/processed/products-v1/search"
MODEL_ID = "google/siglip2-base-patch16-224"
MODEL_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import torch
    from PIL import Image
    from transformers import AutoModel, AutoProcessor

    rows = [
        json.loads(line)
        for line in (args.artifacts / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    manifest_path = args.artifacts.parent / "manifest.json"
    image_root = Path(json.loads(manifest_path.read_text(encoding="utf-8"))["image_root"])
    device = args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu"
    dtype = torch.float16 if device.startswith("cuda") else torch.float32
    model = AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION, dtype=dtype)
    model = model.to(device).eval()
    processor = AutoProcessor.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
    dim = model.config.vision_config.hidden_size

    failures: list[dict] = []

    def load(row: dict):
        try:
            with Image.open(image_root / row["image_path"]) as image:
                image.seek(0)  # first frame of animated GIF/WebP
                return image.convert("RGB")
        except Exception as exc:  # corrupt or unsupported file
            failures.append({"asset_id": row["asset_id"], "error": f"{type(exc).__name__}: {exc}"})
            return None

    vectors = np.zeros((len(rows), dim), np.float16)
    started = time.perf_counter()
    with ThreadPoolExecutor(args.workers) as pool:
        for start in range(0, len(rows), args.batch_size):
            images = list(pool.map(load, rows[start : start + args.batch_size]))
            keep = [i for i, image in enumerate(images) if image is not None]
            if not keep:
                continue
            inputs = processor(images=[images[i] for i in keep], return_tensors="pt")
            pixels = inputs["pixel_values"].to(device, dtype)
            with torch.inference_mode():
                features = model.get_image_features(pixel_values=pixels).pooler_output
            features = torch.nn.functional.normalize(features.float(), dim=-1).cpu().numpy()
            for offset, i in enumerate(keep):
                vectors[start + i] = features[offset].astype(np.float16)
            if (start // args.batch_size) % 50 == 0:
                print(
                    f"{start + len(images)}/{len(rows)} {time.perf_counter() - started:.0f}s",
                    flush=True,
                )
    elapsed = time.perf_counter() - started

    np.save(args.artifacts / "img_siglip2.npy", vectors)
    order = hashlib.sha256("\n".join(row["asset_id"] for row in rows).encode()).hexdigest()
    texts = json.loads((args.artifacts / "texts_manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "dim": dim,
        "dtype": "float16",
        "normalized": True,
        "assets": len(rows),
        "asset_order_sha256": order,
        "corpus_fingerprint": texts["corpus_fingerprint"],
        "failures": sorted(failures, key=lambda item: item["asset_id"]),
        "device": device,
        "embed_seconds": round(elapsed, 1),
    }
    (args.artifacts / "img_manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in manifest.items() if k != "failures"} | {"failures": len(failures)}
        )
    )


if __name__ == "__main__":
    main()
