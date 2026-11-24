"""Bring products_v1_images up to a rebuilt artifact dir by touching only what changed.

A graph review changes the brand of a few products, so a few hundred doc_texts (the entity
card holds the brand) and their ``brand_ids``. Re-ingesting 30k points with qdrant_setup.py
would also drop the ``img`` vectors and the category payload, which other steps add. Here:

  * the changed points get their ``doc`` and ``sparse`` vectors (update_vectors) and their
    doc_texts payload fields (set_payload); ``img`` and the category payload are untouched;
  * every point gets the new ``graph_fingerprint`` / ``doc_texts_sha256`` payload;
  * ``<new>/index_manifest.json`` is written, so the API accepts the new graph.json.

Sparse vectors use the BM25 average length of the existing index, as every unchanged point
does. Dry run by default.

  python scripts/qdrant_sync_changes.py --new data/processed/products-v1/search-next
  python scripts/qdrant_sync_changes.py --new data/processed/products-v1/search-next --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
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
    point_id,
)

PAYLOAD = ("entity_ids", "brand_ids", "image_path", "domain", "page_url", "page_title")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def changes(old: list[dict], new: list[dict]) -> list[int]:
    """Row indexes whose doc_text or payload fields differ; the asset order must be equal."""
    if [r["asset_id"] for r in old] != [r["asset_id"] for r in new]:
        raise SystemExit("the two doc_texts.jsonl list different assets")
    return [
        i
        for i, (a, b) in enumerate(zip(old, new, strict=True))
        if a["doc_text"] != b["doc_text"] or any(a[k] != b[k] for k in PAYLOAD)
    ]


def retry(call) -> None:
    for attempt in range(4):
        try:
            call()
            return
        except Exception:  # transient network errors on the free cluster
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, default=ROOT / "data/processed/products-v1/search")
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    old_rows, new_rows = (
        read_rows(args.old / "doc_texts.jsonl"),
        read_rows(args.new / "doc_texts.jsonl"),
    )
    index = json.loads((args.old / "index_manifest.json").read_text(encoding="utf-8"))
    texts = json.loads((args.new / "texts_manifest.json").read_text(encoding="utf-8"))
    graph = json.loads((args.new / "graph.json").read_text(encoding="utf-8"))
    if texts["graph_fingerprint"] != graph["fingerprint"]:
        raise SystemExit("texts_manifest.json was built from another graph.json")
    dense = np.load(args.new / "doc_dense.npy")
    if dense.shape[0] != len(new_rows):
        raise SystemExit("doc_dense.npy and doc_texts.jsonl disagree")
    changed = changes(old_rows, new_rows)
    avg_length = index["avg_doc_tokens"]
    print(
        json.dumps(
            {
                "collection": args.collection,
                "points": len(new_rows),
                "changed": len(changed),
                "graph_fingerprint": [index["graph_fingerprint"], graph["fingerprint"]],
                "avg_doc_tokens": avg_length,
                "sample": [
                    {
                        "asset_id": new_rows[i]["asset_id"][:12],
                        "brand_ids": [old_rows[i]["brand_ids"], new_rows[i]["brand_ids"]],
                    }
                    for i in changed[:5]
                ],
            },
            ensure_ascii=False,
        )
    )
    if not args.write:
        print("dry run; pass --write to update Qdrant")
        return

    load_env(ROOT / ".env")
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    started = time.perf_counter()
    for start in range(0, len(changed), args.batch_size):
        batch = changed[start : start + args.batch_size]
        vectors = []
        for i in batch:
            sparse = lexical.document_vector(new_rows[i]["doc_text"], avg_length)
            vectors.append(
                models.PointVectors(
                    id=point_id(new_rows[i]["asset_id"]),
                    vector={
                        DENSE: dense[i].tolist(),
                        SPARSE: models.SparseVector(indices=sparse.indices, values=sparse.values),
                    },
                )
            )
        retry(lambda v=vectors: client.update_vectors(args.collection, points=v, wait=True))
    grouped: dict[str, list[str]] = defaultdict(list)
    for i in changed:
        payload = {k: new_rows[i][k] for k in PAYLOAD}
        grouped[json.dumps(payload, ensure_ascii=False, sort_keys=True)].append(
            point_id(new_rows[i]["asset_id"])
        )
    ops = [
        models.SetPayloadOperation(set_payload=models.SetPayload(payload=json.loads(p), points=ids))
        for p, ids in grouped.items()
    ]
    for start in range(0, len(ops), 50):
        retry(
            lambda o=ops[start : start + 50]: client.batch_update_points(
                args.collection, o, wait=True
            )
        )
    stamp = {
        "graph_fingerprint": graph["fingerprint"],
        "doc_texts_sha256": texts["doc_texts_sha256"],
    }
    retry(
        lambda: client.set_payload(
            args.collection,
            payload=stamp,
            points=models.FilterSelector(filter=models.Filter(must=[])),
            wait=True,
        )
    )

    # Read back: every changed point carries its new brands and vector, every point the stamp.
    back = client.retrieve(
        args.collection,
        [point_id(new_rows[i]["asset_id"]) for i in changed],
        with_payload=True,
        with_vectors=[DENSE],
    )
    by_id = {str(p.id): p for p in back}
    bad = 0
    for i in changed:
        point = by_id.get(point_id(new_rows[i]["asset_id"]))
        cos = float(np.dot(point.vector[DENSE], dense[i])) if point else 0.0
        if not point or point.payload["brand_ids"] != new_rows[i]["brand_ids"] or cos < 0.999:
            bad += 1
    # The cluster runs in strict mode: a payload filter needs an index, so scroll the stamp.
    stamped, offset = 0, None
    while True:
        page, offset = client.scroll(
            args.collection,
            limit=1000,
            offset=offset,
            with_payload=["graph_fingerprint"],
            with_vectors=False,
        )
        stamped += sum(p.payload.get("graph_fingerprint") == graph["fingerprint"] for p in page)
        if offset is None:
            break
    total = client.count(args.collection, exact=True).count
    if bad or stamped != total or total != len(new_rows):
        raise SystemExit(f"read-back failed: {bad} changed points wrong, {stamped}/{total} stamped")
    manifest = {
        **index,
        "points": total,
        "graph_fingerprint": graph["fingerprint"],
        "doc_texts_sha256": texts["doc_texts_sha256"],
        "synced": {
            "from_graph_fingerprint": index["graph_fingerprint"],
            "changed_points": len(changed),
            "seconds": round(time.perf_counter() - started, 1),
        },
    }
    (args.new / "index_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
