"""Derive silver-v1.1 from silver-v1 under the reviewed entity identities.

silver-v1 was built before the B1 review, so it still treats merged entities as different
products. This keeps every query, split and query_id, and recomputes Rel only:

  * each intent covers its canonical entities, i.e. the images of every merged member;
  * images whose entity label was rejected in the review are removed from Rel;
  * multi_hop targets merged into the anchor itself are no longer targets;
  * a query left with an empty intent is dropped and listed with its reason.

Output: data/processed/products-v1/eval/silver-v1.1/{dev,test,warmup}.jsonl + manifest.json.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.entity_review import EntityReview  # noqa: E402

CORPUS = ROOT / "data/processed/products-v1"
ARTIFACTS = CORPUS / "search"
SOURCE = CORPUS / "eval/silver-v1"
OUT = CORPUS / "eval/silver-v1.1"
PACKAGED_REVIEW = (
    ROOT / "services/api/src/visualseek_api/application/products-v1-identity-review.json"
)


def review_path() -> Path:
    local = ARTIFACTS / "identity_review.json"
    return local if local.exists() else PACKAGED_REVIEW


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def intent_entities(row: dict, assets_of: dict[str, set[str]]) -> list[list[str]]:
    """Entities per intent; silver-v1 stores only their union, Rel keeps the intent split."""
    if len(row["rel_by_intent"]) == 1:
        return [row["target_entities"]]
    return [
        [e for e in row["target_entities"] if assets_of[e] & set(rel)]
        for rel in row["rel_by_intent"]
    ]


def canonical_row(
    row: dict,
    review: EntityReview,
    assets_of: dict[str, set[str]],
    kept_assets_of: dict[str, set[str]],
    group: dict[str, list[str]],
) -> tuple[dict | None, str | None]:
    if row["type"] == "no_answer":
        return row, None
    anchors = {review.canonical(e) for e in row.get("anchor_entities") or []}
    intents, rel_by_intent = [], []
    for entities in intent_entities(row, assets_of):
        canon = sorted({review.canonical(e) for e in entities} - anchors)
        rel = sorted({a for c in canon for m in group[c] for a in kept_assets_of[m]})
        if not rel:
            return None, "an intent has no target left (merged into the anchor or rejected)"
        intents.append(canon)
        rel_by_intent.append(rel)
    out = {
        **row,
        "rel_by_intent": rel_by_intent,
        "target_entities": sorted({c for canon in intents for c in canon}),
        "anchor_entities": sorted(anchors),
        "silver_v1_target_entities": row["target_entities"],
    }
    out["changed"] = rel_by_intent != row["rel_by_intent"]
    return out, None


def main() -> None:
    graph = json.loads((ARTIFACTS / "graph.json").read_text(encoding="utf-8"))
    review = EntityReview.load(graph, review_path().read_bytes())
    assets_of: dict[str, set[str]] = defaultdict(set)
    kept_assets_of: dict[str, set[str]] = defaultdict(set)
    for row in read_jsonl(ARTIFACTS / "doc_texts.jsonl"):
        rejected = review.rejected_labels.get(row["asset_id"], set())
        for entity_id in row["entity_ids"]:
            assets_of[entity_id].add(row["asset_id"])
            if entity_id not in rejected:
                kept_assets_of[entity_id].add(row["asset_id"])
    group: dict[str, list[str]] = defaultdict(list)
    for entity in graph["entities"]:
        group[review.canonical(entity["id"])].append(entity["id"])

    OUT.mkdir(parents=True, exist_ok=True)
    dropped, counts, changed = [], Counter(), Counter()
    side: dict[str, set[str]] = defaultdict(set)
    digest = hashlib.sha256()
    for split in ("dev", "test"):
        rows = []
        for row in read_jsonl(SOURCE / f"{split}.jsonl"):
            out, reason = canonical_row(row, review, assets_of, kept_assets_of, group)
            if out is None:
                dropped.append(
                    {"query_id": row["query_id"], "query": row["query"], "reason": reason}
                )
                continue
            rows.append(out)
            counts[f"{out['type']}/{split}"] += 1
            changed[out["type"]] += bool(out.get("changed"))
            for c in out["target_entities"] + out["anchor_entities"]:
                side[c].add(split)
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        (OUT / f"{split}.jsonl").write_text(body, encoding="utf-8", newline="\n")
        digest.update(body.encode("utf-8"))
    (OUT / "warmup.jsonl").write_bytes((SOURCE / "warmup.jsonl").read_bytes())
    source_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    leaked = sorted(c for c, splits in side.items() if len(splits) > 1)
    manifest = {
        **source_manifest,
        "version": "silver-v1.1",
        "derived_from": {
            "version": source_manifest["version"],
            "queries_sha256": source_manifest["queries_sha256"],
        },
        "identity_review_fingerprint": review.fingerprint,
        "queries_sha256": digest.hexdigest(),
        "counts": dict(sorted(counts.items())),
        "rel_changed": dict(sorted(changed.items())),
        "dropped": dropped,
        # A merge can join entities that silver-v1 put in different splits.
        "entities_in_both_splits": [{"entity_id": c, "name": graph_name(graph, c)} for c in leaked],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: manifest[k] for k in ("counts", "rel_changed", "dropped")}
            | {"entities_in_both_splits": len(leaked)},
            ensure_ascii=False,
        )
    )


def graph_name(graph: dict, entity_id: str) -> str:
    return next(e["name"] for e in graph["entities"] if e["id"] == entity_id)


if __name__ == "__main__":
    main()
