"""Build the B1 review queue and check S1 on the locked single_named set."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

import numpy as np

from visualseek_retrieval.entity_review import EntityReview
from visualseek_retrieval.spotting import EntitySpotter
from visualseek_retrieval.text import normalize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW = (
    ROOT / "services/api/src/visualseek_api/application/products-v1-identity-review.json"
)


def read_rows(path: Path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def pair_candidates(entities: dict[str, dict]) -> dict[tuple[str, str], float]:
    names = {eid: normalize(node["core"]).replace(" ", "") for eid, node in entities.items()}
    blocks = defaultdict(set)
    for eid, name in names.items():
        blocks["exact", name].add(eid)
        for i in range(len(name) - 3):
            blocks["substring", name[i : i + 4]].add(eid)
    possible = {
        pair
        for (kind, _), members in blocks.items()
        if kind == "exact" or len(members) <= 80
        for pair in combinations(sorted(members), 2)
    }
    scored = {}
    for src, dst in possible:
        match = SequenceMatcher(None, names[src], names[dst])
        if match.real_quick_ratio() >= 0.85 and (score := match.ratio()) >= 0.85:
            scored[src, dst] = round(score, 3)
    return scored


# entity_dense separates duplicates poorly: reviewed DISTINCT pairs reach 0.97 and pairs that
# share images have a median of 0.76. It is a recall source for the queue, never evidence;
# 0.80 keeps the queue reviewable (131 pairs on products-v1-corpus-2).
DENSE_MIN = 0.80


def dense_candidates(
    entity_ids: list[str], dense: np.ndarray, minimum: float = DENSE_MIN
) -> dict[tuple[str, str], float]:
    scores = np.asarray(dense, dtype=np.float32) @ np.asarray(dense, dtype=np.float32).T
    rows, cols = np.nonzero(np.triu(scores, 1) >= minimum)
    return {
        tuple(sorted((entity_ids[i], entity_ids[j]))): round(float(scores[i, j]), 3)
        for i, j in zip(rows.tolist(), cols.tolist(), strict=True)
    }


def queue(
    name_pairs: dict[tuple[str, str], float],
    shared_pairs: dict[tuple[str, str], list[str]],
    dense_pairs: dict[tuple[str, str], float],
    reviewed: set[tuple[str, str]],
    canonical,
) -> list[dict]:
    """Unreviewed pairs from all three sources, strongest agreement first."""
    pending = []
    for pair in sorted(set(name_pairs) | set(shared_pairs) | set(dense_pairs)):
        src, dst = pair
        if pair in reviewed or canonical(src) == canonical(dst):
            continue
        signals = {
            "name_similarity": name_pairs.get(pair),
            "shared_assets": shared_pairs.get(pair, []),
            "dense_cosine": dense_pairs.get(pair),
        }
        found_by = [
            name
            for name, present in (
                ("name", signals["name_similarity"] is not None),
                ("shared_images", bool(signals["shared_assets"])),
                ("dense", signals["dense_cosine"] is not None),
            )
            if present
        ]
        pending.append({"src": src, "dst": dst, "found_by": found_by, **signals})
    pending.sort(
        key=lambda row: (
            -len(row["found_by"]),
            -len(row["shared_assets"]),
            -(row["name_similarity"] or 0),
            -(row["dense_cosine"] or 0),
            row["src"],
            row["dst"],
        )
    )
    return pending


def check_queries(
    corpus: Path, spotter: EntitySpotter, review: EntityReview, corrections: dict
) -> dict:
    result = {}
    used = set()
    for split in ("dev", "test"):
        records = []
        for row in read_rows(corpus / "eval" / "silver-v1" / f"{split}.jsonl"):
            if row["type"] != "single_named":
                continue
            query_id, query = row["query_id"], row["query"]
            correction = corrections.get(query_id)
            expected = sorted({review.canonical(eid) for eid in row["target_entities"]})
            original = spotter.spot(query, None)
            current = original
            if correction:
                if split != "dev" or query != correction["original"]:
                    raise ValueError(f"Invalid single_named dev wording correction: {query_id}")
                used.add(query_id)
                current = spotter.spot(correction["query"], None)
            records.append(
                {
                    "query_id": query_id,
                    "original_query": query,
                    "query": correction["query"] if correction else query,
                    "expected": expected,
                    "original_found": original.entities,
                    "original_ambiguity": [match.ids for match in original.ambiguous],
                    "found": current.entities,
                    "original_correct": sorted(original.entities) == expected
                    and not original.ambiguous,
                    "correct": sorted(current.entities) == expected and not current.ambiguous,
                }
            )
        result[split] = {
            "total": len(records),
            "original_correct": sum(record["original_correct"] for record in records),
            "correct": sum(record["correct"] for record in records),
            "records": records,
        }
    if used != corrections.keys():
        raise ValueError("An approved wording correction is missing from the eval set")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/processed/products-v1")
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    corpus = args.corpus
    out = args.out or corpus / "eval" / "identity-review-v1.json"
    graph = json.loads((corpus / "search" / "graph.json").read_text(encoding="utf-8"))
    raw_review = args.review.read_bytes()
    review = EntityReview.load(graph, raw_review)
    data = json.loads(raw_review)
    entities = {node["id"]: node for node in graph["entities"]}
    reviewed_pairs = {tuple(sorted((pair["src"], pair["dst"]))) for pair in data["entity_pairs"]}
    evidence_assets = {
        evidence["asset_id"]
        for pair in data["entity_pairs"]
        for evidence in pair["evidence"]
        if "asset_id" in evidence
    }
    seen = set()
    shared = []
    shared_by_pair = defaultdict(list)
    for asset in read_rows(corpus / "assets.jsonl"):
        asset_id = asset["asset_id"]
        seen.add(asset_id)
        original = asset["entity_ids"]
        remaining = review.result_ids(asset_id, original)
        if len(remaining) > 1:
            shared.append(
                {"asset_id": asset_id, "image_path": asset["image_path"], "entities": remaining}
            )
            for pair in combinations(sorted(remaining), 2):
                shared_by_pair[pair].append(asset_id)
        rejected = review.rejected_labels.get(asset_id)
        if rejected and (
            not rejected <= set(original) or (not remaining) != (asset_id in review.blocked_assets)
        ):
            raise ValueError(f"Reviewed links for {asset_id} do not match the locked corpus")
    if not (evidence_assets | review.rejected_labels.keys()) <= seen:
        raise ValueError("A review references an image missing from the locked corpus")

    dense = np.load(corpus / "search" / "entity_dense.npy", mmap_mode="r")
    # entity_dense rows follow graph.json entity order (scripts/build_doc_texts.py).
    pending = queue(
        pair_candidates(entities),
        dict(shared_by_pair),
        dense_candidates([node["id"] for node in graph["entities"]], dense),
        reviewed_pairs,
        review.canonical,
    )
    for row in pending:
        row["names"] = [entities[eid]["name"] for eid in (row["src"], row["dst"])]
    by_source = {
        source: sum(source in row["found_by"] for row in pending)
        for source in ("name", "shared_images", "dense")
    }
    # Same S1 dictionary as the API: short aliases added, shared derived aliases suppressed.
    short_path = corpus / "search" / "short_aliases.json"
    short = json.loads(short_path.read_text(encoding="utf-8")) if short_path.exists() else None
    if short and short["identity_review_fingerprint"] != review.fingerprint:
        raise SystemExit("short_aliases.json is stale; run scripts/build_short_aliases.py")
    spotter = EntitySpotter(
        graph,
        dense,
        review,
        short["aliases"] if short else None,
        short["suppressed"]["aliases"] if short else None,
    )
    scorecard = check_queries(corpus, spotter, review, data["eval_query_corrections"])
    report = {
        "corpus_fingerprint": graph["corpus_fingerprint"],
        "review_fingerprint": review.fingerprint,
        "short_aliases_fingerprint": short["fingerprint"] if short else None,
        "entities_scanned": len(entities),
        "reviewed": data,
        "dense_min": DENSE_MIN,
        "pending_pairs_by_source": by_source,
        "pending_pairs": pending,
        "pending_shared_images": shared,
        "single_named": scorecard,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "entities": len(entities),
                "reviewed_pairs": len(reviewed_pairs),
                "rejected_depictions": len(data["rejected_depictions"]),
                "pending_pairs": len(pending),
                "pending_pairs_by_source": by_source,
                "pending_shared_images": len(shared),
                "single_named": {
                    split: {k: v for k, v in totals.items() if k != "records"}
                    for split, totals in scorecard.items()
                },
            },
            ensure_ascii=False,
        )
    )
    if any(totals["correct"] != totals["total"] for totals in scorecard.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
