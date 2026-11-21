"""Leave-one-out retrieval quality on the mirrored product images, served by Qdrant.

Each image queries the collection (itself excluded); the other images of the same entity are the
relevant set R. Recall@10 is reported both raw (hits / |R|, capped at 10/|R| by construction) and
R-capped (hits / min(|R|, 10)) so the two are never confused. Qdrant results are compared with an
exact NumPy ranking to show what approximate search costs, if anything.
"""

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from product_index import make_client, point_id
from qdrant_client import models

K = 10


def average_precision(relevant: np.ndarray) -> float:
    hits = np.flatnonzero(relevant)
    return float(np.mean((np.arange(len(hits)) + 1) / (hits + 1))) if len(hits) else 0.0


def fit_text(encoder, text: str) -> str:
    words = text.split()
    while words and len(encoder.processor.tokenizer(" ".join(words))["input_ids"]) > 64:
        words.pop()
    return " ".join(words)


def entity_rank(client, collection, query, entity_id, exclude=None) -> int | None:
    flt = models.Filter(must_not=[models.HasIdCondition(has_id=[exclude])]) if exclude else None
    groups = client.query_points_groups(
        collection, query=query, using="image", group_by="entity_id",
        limit=K, group_size=1, query_filter=flt).groups
    ids = [str(g.id) for g in groups]
    return ids.index(entity_id) + 1 if entity_id in ids else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/product-images"))
    parser.add_argument("--collection", default="product_images_poc")
    parser.add_argument("--no-text", action="store_true", help="skip the text-to-image check")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-queries", type=int, default=0,
                        help="random subset of query images (seeded); 0 means every image")
    args = parser.parse_args()

    records = [json.loads(line) for line in
               (args.data / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    records = [r for r in records if r["ok"]]
    ids = json.loads((args.data / "vector_ids.json").read_text())
    assert ids == [r["image_id"] for r in records], "manifest changed since indexing"
    vectors = np.load(args.data / "vectors.npy")
    entities = np.array([r["entity_id"] for r in records])
    sizes = defaultdict(int)
    for entity in entities:
        sizes[entity] += 1
    client = make_client()
    client.get_collection(args.collection)  # fail early if the collection is missing

    queries = [i for i, e in enumerate(entities) if sizes[e] > 1]
    singletons = len(records) - len(queries)
    if args.max_queries and len(queries) > args.max_queries:
        queries = sorted(np.random.default_rng(0).choice(queries, args.max_queries, replace=False))
    by_id = {r["image_id"]: r["entity_id"] for r in records}

    def run(i: int):
        pid = point_id(records[i]["image_id"])
        response = client.query_points(
            args.collection, query=vectors[i].tolist(), using="image", limit=K + 1,
            with_payload=["image_id"])
        qdrant_top = [p.payload["image_id"] for p in response.points
                      if p.payload["image_id"] != records[i]["image_id"]][:K]
        group_rank = entity_rank(client, args.collection, vectors[i].tolist(),
                                 str(entities[i]), exclude=pid)
        return i, qdrant_top, group_rank

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(run, queries))

    rows = []
    for i, qdrant_top, group_rank in results:
        relevant_all = entities == entities[i]
        relevant_all[i] = False
        scores = vectors @ vectors[i]
        scores[i] = -np.inf
        order = np.argsort(-scores, kind="stable")[:-1]  # drop the query itself (last, -inf)
        relevant_ranked = relevant_all[order]
        r_size = int(relevant_all.sum())
        qdrant_hits = sum(by_id[x] == entities[i] for x in qdrant_top)
        exact_top = [records[j]["image_id"] for j in order[:K]]
        hits10 = int(relevant_ranked[:K].sum())
        rows.append({
            "entity_id": str(entities[i]), "entity_name": records[i]["entity_name"], "R": r_size,
            "hit1": float(relevant_ranked[0]), "hit10": float(hits10 > 0),
            "p10": hits10 / K, "recall10_raw": hits10 / r_size,
            "recall10_capped": hits10 / min(r_size, K), "ap": average_precision(relevant_ranked),
            "qdrant_p10": qdrant_hits / K, "overlap10": len(set(exact_top) & set(qdrant_top)) / K,
            "entity_hit1": float(group_rank == 1), "entity_hit5": float(group_rank is not None and group_rank <= 5),
            "entity_hit10": float(group_rank is not None),
        })

    def mean(key):
        return round(float(np.mean([r[key] for r in rows])), 4)

    summary = {
        "images": len(records), "entities": len(sizes), "queries": len(rows),
        "excluded_singletons": singletons,
        "mean_R": round(float(np.mean([r["R"] for r in rows])), 2),
        **{key: mean(key) for key in (
            "hit1", "hit10", "p10", "recall10_raw", "recall10_capped", "ap", "qdrant_p10",
            "overlap10", "entity_hit1", "entity_hit5", "entity_hit10")},
    }
    per_entity = defaultdict(list)
    for r in rows:
        per_entity[(r["entity_id"], r["entity_name"])].append(r["recall10_capped"])
    worst = sorted(((float(np.mean(v)), k[1], len(v)) for k, v in per_entity.items()))[:12]
    summary["worst_entities"] = [{"name": n, "recall10_capped": round(s, 3), "images": c}
                                 for s, n, c in worst]

    if not args.no_text:
        from visualseek_embedding.siglip2 import Siglip2Encoder
        encoder = Siglip2Encoder(device="auto")
        names = {}
        for r in records:
            names.setdefault(r["entity_id"], r["entity_name"])
        for label, template in (("name", "{}"), ("photo_of_name", "a photo of {}")):
            ranks = []
            for entity_id, name in names.items():
                text = fit_text(encoder, template.format(name.lower()))
                vec = encoder.encode_texts([text])[0].tolist()
                ranks.append(entity_rank(client, args.collection, vec, entity_id))
            summary[f"text_{label}"] = {
                "queries": len(ranks),
                "entity_hit1": round(float(np.mean([r == 1 for r in ranks])), 4),
                "entity_hit5": round(float(np.mean([r is not None and r <= 5 for r in ranks])), 4),
                "entity_hit10": round(float(np.mean([r is not None for r in ranks])), 4)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "rows": rows},
                                       ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
