"""Create ``products_v1_images`` and ingest doc_texts + dense vectors idempotently (P4, MVP).

Point id = uuid5(asset_id), so re-running overwrites the same points. Reads QDRANT_URL and
QDRANT_API_KEY from the environment or .env. Never touches other collections.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from qdrant_client import models  # noqa: E402

from visualseek_retrieval import lexical  # noqa: E402
from visualseek_retrieval.qdrant_store import (  # noqa: E402
    COLLECTION,
    DENSE,
    SPARSE,
    connect,
    ensure_collection,
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
    parser.add_argument(
        "--artifacts", type=Path, default=ROOT / "data/processed/products-v1/search"
    )
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--recreate", action="store_true", help="drop this collection first")
    args = parser.parse_args()
    load_env(ROOT / ".env")

    rows = [
        json.loads(line)
        for line in (args.artifacts / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    dense = np.load(args.artifacts / "doc_dense.npy")
    manifest = json.loads((args.artifacts / "texts_manifest.json").read_text(encoding="utf-8"))
    if dense.shape[0] != len(rows) or manifest["assets"] != len(rows):
        raise SystemExit("doc_dense.npy and doc_texts.jsonl disagree; rebuild the texts first")
    avg_length = float(np.mean([len(lexical.tokens(row["doc_text"])) for row in rows]))

    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    if args.recreate and client.collection_exists(args.collection):
        client.delete_collection(args.collection)
    ensure_collection(client, args.collection)

    started = time.perf_counter()
    for start in range(0, len(rows), args.batch_size):
        points = []
        for offset, row in enumerate(rows[start : start + args.batch_size]):
            sparse = lexical.document_vector(row["doc_text"], avg_length)
            points.append(
                models.PointStruct(
                    id=point_id(row["asset_id"]),
                    vector={
                        DENSE: dense[start + offset].tolist(),
                        SPARSE: models.SparseVector(indices=sparse.indices, values=sparse.values),
                    },
                    payload={
                        key: row[key]
                        for key in (
                            "asset_id",
                            "entity_ids",
                            "brand_ids",
                            "image_path",
                            "domain",
                            "page_url",
                            "page_title",
                        )
                    }
                    | {
                        "graph_fingerprint": manifest["graph_fingerprint"],
                        "doc_texts_sha256": manifest["doc_texts_sha256"],
                    },
                )
            )
        for attempt in range(4):
            try:
                client.upsert(args.collection, points=points, wait=True)
                break
            except Exception:  # transient network errors on the free cluster
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        if (start // args.batch_size) % 20 == 0:
            print(
                f"{start + len(points)}/{len(rows)} {time.perf_counter() - started:.0f}s",
                flush=True,
            )

    count = client.count(args.collection, exact=True).count
    info = {
        "collection": args.collection,
        "points": count,
        "expected": len(rows),
        "avg_doc_tokens": round(avg_length, 2),
        "graph_fingerprint": manifest["graph_fingerprint"],
        "doc_texts_sha256": manifest["doc_texts_sha256"],
        "corpus_fingerprint": manifest["corpus_fingerprint"],
        "seconds": round(time.perf_counter() - started, 1),
    }
    (args.artifacts / "index_manifest.json").write_text(
        json.dumps(info, indent=1), encoding="utf-8"
    )
    print(json.dumps(info))
    if count != len(rows):
        raise SystemExit("point count mismatch")


if __name__ == "__main__":
    main()
