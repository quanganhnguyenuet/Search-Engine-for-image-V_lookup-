"""Tune S4 visual fusion (``visualseek_retrieval.fusion``) on eval/descriptive-v1.

  collect  encode every query once (bge-m3 + SigLIP2), fetch the top-400 text candidates
           (dense + sparse RRF at depth 400; the grid takes prefixes, so N=200 is not the
           pool's ``rerank`` request, which fused two depth-200 lists) and the
           production pipeline plan; written to a cache so the grid runs offline.
  grid     dev only: img top-K x alpha (attribute queries) x alpha (plain queries), nDCG@10
           against the human judgments; prints the chosen config. ``--test`` additionally
           scores that one config on test with paired bootstrap against the pooled systems.

Unjudged images count as grade 0, so a config that leaves the pool is penalised, never
rewarded; judged@10 is reported so the size of that penalty is visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

ARTIFACTS = ROOT / "data/processed/products-v1/search"
QUERIES = ROOT / "eval/descriptive-v1/queries.jsonl"
JUDGMENTS = ROOT / "eval/descriptive-v1/judgments.jsonl"
POOL_DIR = ROOT / "data/processed/products-v1/eval/descriptive-v1"
CACHE_ROWS = POOL_DIR / "fusion_candidates.jsonl"
CACHE_VECTORS = POOL_DIR / "fusion_query_vectors.npz"
REPORT = ROOT / "data/processed/products-v1/eval/runs/fusion-descriptive-v1.json"
POOL_SYSTEMS = ("text", "img", "rerank")
IMG_K = (0, 25, 50, 100)
TEXT_N = (50, 100, 200, 400)
ALPHAS = tuple(round(a * 0.1, 1) for a in range(11))
SEED = 20261123


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


# ---- collect -----------------------------------------------------------------------------
def collect(args: argparse.Namespace) -> None:
    load_env(ROOT / ".env")
    from qdrant_client import models

    from visualseek_retrieval import lexical
    from visualseek_retrieval.encoder import TextEncoder
    from visualseek_retrieval.image_text import ImageTextEncoder
    from visualseek_retrieval.pipeline import SearchPipeline
    from visualseek_retrieval.qdrant_store import COLLECTION, DENSE, SPARSE, connect

    graph = json.loads((ARTIFACTS / "graph.json").read_text(encoding="utf-8"))
    queries = read_jsonl(QUERIES)
    encoder = TextEncoder(device=args.device)
    image_encoder = ImageTextEncoder(device=args.device)
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    pipeline = SearchPipeline(
        graph, np.load(ARTIFACTS / "entity_dense.npy"), encoder, client, COLLECTION
    )
    texts = [q["query"] for q in queries]
    text_vectors = encoder.encode(texts)
    img_vectors = image_encoder.encode(texts)
    rows = []
    for query, dense in zip(queries, text_vectors, strict=True):
        sparse = lexical.query_vector(query["query"])
        hits = client.query_points(
            COLLECTION,
            prefetch=[
                models.Prefetch(query=dense.tolist(), using=DENSE, limit=args.candidates),
                models.Prefetch(
                    query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                    using=SPARSE,
                    limit=args.candidates,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=args.candidates,
            with_payload=["asset_id"],
        ).points
        result = pipeline.search(query["query"])
        plan = result["plan"]
        rows.append(
            {
                "qid": query["qid"],
                "text_ids": [hit.payload["asset_id"] for hit in hits],
                "pipeline_top": [hit["asset_id"] for hit in result["results"]],
                "out_of_scope": result["out_of_scope"],
                "plan": {
                    "seeds": plan["seed_names"],
                    "hops": plan["hops"],
                    "dense_seed": plan["dense_seed"],
                    "matches": [m.get("text") for m in plan["matches"]],
                },
            }
        )
        print(f"{query['qid']} {len(hits):3d} seeds={plan['seed_names']}", flush=True)
    with CACHE_ROWS.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    np.savez(
        CACHE_VECTORS,
        qids=np.array([q["qid"] for q in queries]),
        text=text_vectors.astype(np.float32),
        img=img_vectors.astype(np.float32),
    )
    print(f"wrote {CACHE_ROWS} and {CACHE_VECTORS}")


# ---- scoring -----------------------------------------------------------------------------
def gain(grade: int) -> float:
    return float(2**grade - 1)


def ndcg_at_10(ranking: list[str], grades: dict[str, int]) -> float | None:
    ideal = sorted(grades.values(), reverse=True)[:10]
    idcg = sum(gain(g) / math.log2(i + 2) for i, g in enumerate(ideal))
    if idcg == 0:
        return None
    dcg = sum(gain(grades.get(a, 0)) / math.log2(i + 2) for i, a in enumerate(ranking[:10]))
    return dcg / idcg


def judged_at_10(ranking: list[str], grades: dict[str, int]) -> float:
    top = ranking[:10]
    return sum(a in grades for a in top) / max(1, len(top))


def paired_bootstrap(diffs: list[float], seed: int = SEED, n: int = 10_000) -> list[float]:
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(n))
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n)], 4)]


def load_eval() -> tuple[list[dict], dict[str, dict]]:
    """Queries that are judged, not flagged and have at least one relevant image."""
    judgments: dict[str, dict] = {}
    for row in read_jsonl(JUDGMENTS):
        judgments[row["qid"]] = row  # the judge page appends; the last write wins
    pool = {row["qid"]: row for row in read_jsonl(POOL_DIR / "pool.jsonl")}
    pool_sha = hashlib.sha256((POOL_DIR / "pool.jsonl").read_bytes()).hexdigest()
    keep = []
    for query in read_jsonl(QUERIES):
        j = judgments.get(query["qid"])
        if j is None or j["flag"]:
            continue
        if j["pool_sha256"] != pool_sha:
            raise SystemExit(f"{query['qid']} was judged on another pool")
        if not any(g > 0 for g in j["grades"].values()):
            continue  # no relevant image anywhere: a no-answer query, not a ranking query
        ranks: dict[str, list[str]] = {s: [] for s in POOL_SYSTEMS}
        for system in POOL_SYSTEMS:
            pairs = [(c["sources"][system], c["asset_id"]) for c in pool[query["qid"]]["candidates"]
                     if system in c["sources"]]
            ranks[system] = [a for _, a in sorted(pairs)]
        keep.append({**query, "grades": j["grades"], "pool_runs": ranks})
    return keep, judgments


def grid(args: argparse.Namespace) -> None:
    from visualseek_retrieval.fusion import FusionSettings, VisualFusion, fuse, has_visual_attribute

    queries, _ = load_eval()
    cache = {row["qid"]: row for row in read_jsonl(CACHE_ROWS)}
    vectors = np.load(CACHE_VECTORS)
    vec_row = {qid: i for i, qid in enumerate(vectors["qids"].tolist())}
    asset_ids = [
        json.loads(line)["asset_id"]
        for line in (ARTIFACTS / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    fusion = VisualFusion(
        asset_ids, np.load(ARTIFACTS / "doc_dense.npy"), np.load(ARTIFACTS / "img_siglip2.npy")
    )

    # Precompute, per query and K, the candidate list and both raw scores.
    prepared: dict[tuple[str, int], tuple[list[str], np.ndarray, np.ndarray]] = {}
    for q in queries:
        i = vec_row[q["qid"]]
        tq, iq = vectors["text"][i], vectors["img"][i]
        q["attribute"] = has_visual_attribute(q["query"])
        if len(cache[q["qid"]]["text_ids"]) < max(TEXT_N):
            raise SystemExit(f"cache has fewer than {max(TEXT_N)} text candidates; re-run collect")
        for n in TEXT_N:
            for k in IMG_K:
                cands = fusion.candidates(cache[q["qid"]]["text_ids"][:n], iq, k)
                prepared[(q["qid"], n, k)] = (cands, *fusion.scores(cands, tq, iq))

    def ranking(q: dict, nk: tuple[int, int], alpha: float) -> list[str]:
        cands, ts, im = prepared[(q["qid"], *nk)]
        order = np.argsort(-fuse(ts, im, alpha), kind="stable")[:10]
        return [cands[j] for j in order]

    def mean_ndcg(rows: list[dict], nk: tuple[int, int], alpha: float) -> float:
        vals = [ndcg_at_10(ranking(q, nk, alpha), q["grades"]) for q in rows]
        return float(np.mean(vals)) if vals else 0.0

    dev = [q for q in queries if q["split"] == "dev"]
    groups = {"attribute": [q for q in dev if q["attribute"]],
              "plain": [q for q in dev if not q["attribute"]]}
    table: dict[tuple[int, int], dict[str, dict[float, float]]] = {}
    best = None
    for k in ((n, k) for n in TEXT_N for k in IMG_K):
        table[k] = {g: {a: mean_ndcg(rows, k, a) for a in ALPHAS} for g, rows in groups.items()}
        # Ties go to the larger alpha: more weight on the channel that decides the object.
        choice = {g: max(ALPHAS, key=lambda a, g=g: (round(table[k][g][a], 4), a)) for g in groups}
        total = sum(table[k][g][choice[g]] * len(groups[g]) for g in groups) / len(dev)
        if best is None or round(total, 4) > round(best[0], 4):
            best = (total, k, choice)
    total, k_best, choice = best
    settings = FusionSettings(
        text_candidates=k_best[0],
        img_candidates=k_best[1],
        alpha_attribute=choice["attribute"],
        alpha_plain=choice["plain"],
    )

    print(f"dev: {len(dev)} queries, attribute={len(groups['attribute'])} "
          f"plain={len(groups['plain'])}")
    print("attribute detector vs query kind (dev+test):")
    for kind in sorted({q["kind"] for q in queries}):
        rows = [q for q in queries if q["kind"] == kind]
        print(f"  {kind:10s} {sum(q['attribute'] for q in rows):3d}/{len(rows)} flagged attribute")
    for g in groups:
        print(f"\n{g}: nDCG@10 by (text_n, img_k) rows x alpha cols")
        print("   n,  k  " + " ".join(f"{a:5.1f}" for a in ALPHAS))
        for nk in table:
            cells = " ".join(f"{table[nk][g][a]:5.3f}" for a in ALPHAS)
            print(f"  {nk[0]:3d},{nk[1]:3d}  {cells}")
    print(f"\nchosen on dev: (text_n, img_k)={k_best} alpha_attribute={choice['attribute']} "
          f"alpha_plain={choice['plain']} dev nDCG@10={total:.3f}")

    def evaluate(rows: list[dict]) -> dict:
        out: dict = {"n": len(rows), "systems": {}, "paired_vs_fusion": {}}
        fused = {}
        for q in rows:
            alpha = settings.alpha_attribute if q["attribute"] else settings.alpha_plain
            fused[q["qid"]] = ranking(q, k_best, alpha)
        runs = {"fusion": fused}
        for system in POOL_SYSTEMS:
            runs[system] = {q["qid"]: q["pool_runs"][system] for q in rows}
        per = {}
        for name, run in runs.items():
            nd = [ndcg_at_10(run[q["qid"]], q["grades"]) for q in rows]
            per[name] = nd
            out["systems"][name] = {
                "ndcg@10": round(float(np.mean(nd)), 4),
                "p@10_grade2": round(float(np.mean(
                    [sum(q["grades"].get(a, 0) == 2 for a in run[q["qid"]][:10]) / 10
                     for q in rows])), 4),
                "judged@10": round(float(np.mean(
                    [judged_at_10(run[q["qid"]], q["grades"]) for q in rows])), 4),
            }
            for kind in sorted({q["kind"] for q in rows}):
                vals = [v for v, q in zip(nd, rows, strict=True) if q["kind"] == kind]
                out["systems"][name][f"ndcg@10_{kind}"] = round(float(np.mean(vals)), 4)
        for name in POOL_SYSTEMS:
            diffs = [a - b for a, b in zip(per["fusion"], per[name], strict=True)]
            out["paired_vs_fusion"][name] = {
                "delta": round(float(np.mean(diffs)), 4),
                "ci95": paired_bootstrap(diffs),
                "wins": sum(d > 1e-9 for d in diffs),
                "losses": sum(d < -1e-9 for d in diffs),
            }
        out["per_query"] = {
            q["qid"]: {name: round(per[name][i], 3) for name in per} for i, q in enumerate(rows)
        }
        return out

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "settings": settings.__dict__,
        "grid": {f"{n},{k}": table[(n, k)] for n, k in table},
        "dev": evaluate(dev),
    }
    print_eval("dev", report["dev"])
    if args.test:
        report["test"] = evaluate([q for q in queries if q["split"] == "test"])
        print_eval("test", report["test"])
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {REPORT}")


def print_eval(split: str, result: dict) -> None:
    print(f"\n[{split}] n={result['n']}")
    for name, m in result["systems"].items():
        print(f"  {name:7s} " + " ".join(f"{k}={v}" for k, v in m.items()))
    for name, m in result["paired_vs_fusion"].items():
        print(f"  fusion-{name}: {m['delta']:+.4f} CI95 {m['ci95']} "
              f"wins {m['wins']} losses {m['losses']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("--device", default="cuda")
    p_collect.add_argument("--candidates", type=int, default=400)
    p_grid = sub.add_parser("grid")
    p_grid.add_argument("--test", action="store_true", help="score the chosen config on test")
    args = parser.parse_args()
    collect(args) if args.cmd == "collect" else grid(args)


if __name__ == "__main__":
    main()
