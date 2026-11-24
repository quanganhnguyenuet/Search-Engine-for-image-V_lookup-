"""Store the SigLIP2 image vectors in Qdrant as the ``img`` named vector.

Adds the vector to the existing collection (``create_vector_name``) and writes it to the
existing points with ``update_vectors``: ``doc``, ``sparse`` and payloads are untouched,
and re-running overwrites the same vectors. Failed images (zero vectors) are skipped.
Afterwards it checks the point count with ``img`` and compares exact Qdrant search with a
NumPy oracle on a few queries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "data/processed/products-v1/search"
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from qdrant_client import models  # noqa: E402

from visualseek_retrieval.qdrant_store import (  # noqa: E402
    COLLECTION,
    IMAGE,
    IMAGE_DIM,
    connect,
    ensure_image_vector,
    point_id,
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--parity-queries", type=int, default=20)
    args = parser.parse_args()
    load_env(ROOT / ".env")

    rows = [
        json.loads(line)
        for line in (args.artifacts / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    vectors = np.load(args.artifacts / "img_siglip2.npy")
    manifest = json.loads((args.artifacts / "img_manifest.json").read_text(encoding="utf-8"))
    order = hashlib.sha256("\n".join(r["asset_id"] for r in rows).encode()).hexdigest()
    if order != manifest["asset_order_sha256"] or vectors.shape != (len(rows), IMAGE_DIM):
        raise SystemExit("img_siglip2.npy does not match doc_texts.jsonl; re-run embed_images.py")
    failed = {item["asset_id"] for item in manifest["failures"]}

    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    created = ensure_image_vector(client, args.collection)
    print(f"{IMAGE} vector {'created' if created else 'already present'} on {args.collection}")

    started = time.perf_counter()
    written = 0
    for start in range(0, len(rows), args.batch_size):
        batch = [
            models.PointVectors(
                id=point_id(row["asset_id"]),
                vector={IMAGE: vectors[start + offset].astype(np.float32).tolist()},
            )
            for offset, row in enumerate(rows[start : start + args.batch_size])
            if row["asset_id"] not in failed
        ]
        for attempt in range(4):
            try:
                client.update_vectors(args.collection, points=batch, wait=True)
                break
            except Exception:  # transient network errors on the free cluster
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        written += len(batch)
        if (start // args.batch_size) % 20 == 0:
            print(f"{written}/{len(rows)} {time.perf_counter() - started:.0f}s", flush=True)

    with_img = client.count(
        args.collection,
        count_filter=models.Filter(must=[models.HasVectorCondition(has_vector=IMAGE)]),
        exact=True,
    ).count

    # Parity: exact Qdrant search over img must return the NumPy top-10 for the same vectors.
    rng = np.random.default_rng(0)
    matrix = vectors.astype(np.float32)
    agree = []
    for index in rng.choice(len(rows), size=args.parity_queries, replace=False):
        query = matrix[index]
        expected = [rows[i]["asset_id"] for i in np.argsort(-(matrix @ query))[:10]]
        hits = client.query_points(
            args.collection,
            query=query.tolist(),
            using=IMAGE,
            limit=10,
            with_payload=["asset_id"],
            search_params=models.SearchParams(exact=True),
        ).points
        got = [hit.payload["asset_id"] for hit in hits]
        agree.append(len(set(got) & set(expected)) / 10)

    info = {
        "collection": args.collection,
        "vector": IMAGE,
        "model": manifest["model"],
        "revision": manifest["revision"],
        "points_written": written,
        "points_with_img": with_img,
        "skipped_failed_images": len(failed),
        "parity_top10_overlap_mean": round(float(np.mean(agree)), 4),
        "parity_top10_overlap_min": round(float(np.min(agree)), 4),
        "seconds": round(time.perf_counter() - started, 1),
    }
    index_manifest = args.artifacts / "index_manifest.json"
    data = json.loads(index_manifest.read_text(encoding="utf-8"))
    data["img"] = info
    index_manifest.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(json.dumps(info))
    if with_img != len(rows) - len(failed):
        raise SystemExit("point count with img does not match the embedded assets")


if __name__ == "__main__":
    main()
