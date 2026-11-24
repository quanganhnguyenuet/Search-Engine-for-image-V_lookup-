"""Build the judging pool for the descriptive query set (``eval/descriptive-v1``).

The pool of a query is the union of the top-10 of three systems, so every variant compared
later is judged on the images it actually returns:

  text    the production pipeline (S1-S3), no image channel
  img     SigLIP2 text vector -> ANN over the ``img`` vector only
  rerank  text candidates (doc + sparse RRF, top ``--candidates``) re-scored by ``img``

Candidates are shown in a seeded shuffle; which system found them is stored for analysis but
never shown to the judge.

Outputs in ``data/processed/products-v1/eval/descriptive-v1``:
  pool.jsonl            one row per query: candidates with source systems and ranks
  pool_manifest.json    models, revisions, graph fingerprint, settings
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
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

ARTIFACTS = ROOT / "data/processed/products-v1/search"
QUERIES = ROOT / "eval/descriptive-v1/queries.jsonl"
OUT = ROOT / "data/processed/products-v1/eval/descriptive-v1"
SYSTEMS = ("text", "img", "rerank")
POOL_SEED = "descriptive-v1"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def merge_pool(qid: str, runs: dict[str, list[str]], seed: str = POOL_SEED) -> list[dict]:
    """Union of the system rankings in a deterministic order unrelated to any system."""
    found: dict[str, dict] = {}
    for system, ranking in runs.items():
        for rank, asset_id in enumerate(ranking, 1):
            entry = found.setdefault(asset_id, {"asset_id": asset_id, "sources": {}})
            entry["sources"].setdefault(system, rank)

    def key(asset_id: str) -> str:
        return hashlib.sha256(f"{seed}|{qid}|{asset_id}".encode()).hexdigest()

    return [found[asset_id] for asset_id in sorted(found, key=key)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=QUERIES)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--device", default="cuda", help="cpu when the API holds the GPU")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--candidates", type=int, default=200)
    args = parser.parse_args()
    load_env(ROOT / ".env")

    from qdrant_client import models

    from visualseek_retrieval import image_text, lexical
    from visualseek_retrieval.encoder import MODEL_REVISION as TEXT_REVISION
    from visualseek_retrieval.encoder import TextEncoder
    from visualseek_retrieval.image_text import ImageTextEncoder
    from visualseek_retrieval.pipeline import SearchPipeline
    from visualseek_retrieval.qdrant_store import COLLECTION, DENSE, IMAGE, SPARSE, connect

    graph = json.loads((ARTIFACTS / "graph.json").read_text(encoding="utf-8"))
    index = json.loads((ARTIFACTS / "index_manifest.json").read_text(encoding="utf-8"))
    img_manifest = json.loads((ARTIFACTS / "img_manifest.json").read_text(encoding="utf-8"))
    if index["graph_fingerprint"] != graph["fingerprint"]:
        raise SystemExit("Qdrant collection was built from a different graph snapshot")
    if img_manifest["revision"] != image_text.MODEL_REVISION:
        raise SystemExit("img vectors come from another SigLIP2 revision than image_text.py")

    queries = [
        json.loads(line)
        for line in args.queries.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    encoder = TextEncoder(device=args.device)
    image_encoder = ImageTextEncoder(device=args.device)
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    pipeline = SearchPipeline(
        graph, np.load(ARTIFACTS / "entity_dense.npy"), encoder, client, COLLECTION
    )
    pipeline.settings.top_k = args.top

    started = time.perf_counter()
    rows = []
    for query in queries:
        text = query["query"]
        result = pipeline.search(text)
        dense = encoder.encode([text])[0].tolist()
        sparse = lexical.query_vector(text)
        visual = image_encoder.encode([text])[0].tolist()
        img_hits = client.query_points(
            COLLECTION, query=visual, using=IMAGE, limit=args.top, with_payload=["asset_id"]
        ).points
        text_candidates = models.Prefetch(
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, limit=args.candidates),
                models.Prefetch(
                    query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                    using=SPARSE,
                    limit=args.candidates,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=args.candidates,
        )
        rerank_hits = client.query_points(
            COLLECTION,
            prefetch=[text_candidates],
            query=visual,
            using=IMAGE,
            limit=args.top,
            with_payload=["asset_id"],
        ).points
        runs = {
            "text": [hit["asset_id"] for hit in result["results"]],
            "img": [hit.payload["asset_id"] for hit in img_hits],
            "rerank": [hit.payload["asset_id"] for hit in rerank_hits],
        }
        candidates = merge_pool(query["qid"], runs)
        rows.append(
            {
                "qid": query["qid"],
                "query": text,
                "text_out_of_scope": result["out_of_scope"],
                "text_clarification": result["clarification"] is not None,
                "candidates": candidates,
            }
        )
        print(f"{query['qid']} {len(candidates):3d} {text}", flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "pool.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    sizes = [len(row["candidates"]) for row in rows]
    manifest = {
        "queries": len(rows),
        "queries_sha256": hashlib.sha256(args.queries.read_bytes()).hexdigest(),
        "systems": list(SYSTEMS),
        "top": args.top,
        "rerank_candidates": args.candidates,
        "pool_seed": POOL_SEED,
        "pool_size_mean": round(float(np.mean(sizes)), 1),
        "pool_size_total": int(sum(sizes)),
        "text_model_revision": TEXT_REVISION,
        "image_text_model": image_text.MODEL_ID,
        "image_text_revision": image_text.MODEL_REVISION,
        "graph_fingerprint": graph["fingerprint"],
        "corpus_fingerprint": graph["corpus_fingerprint"],
        "pipeline_settings": pipeline.settings.__dict__,
        "device": args.device,
        "seconds": round(time.perf_counter() - started, 1),
    }
    (args.out / "pool_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("queries", "pool_size_mean", "pool_size_total")}))


if __name__ == "__main__":
    main()
