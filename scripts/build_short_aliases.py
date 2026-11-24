"""Build the S1 alias adjustments, both collision-checked against the whole corpus.

  * suppressed: derived graph aliases that other entities also use ("nhat" from
    "Dao thái thịt Tojiro Nhật"), dropped from the S1 dictionary;
  * aliases: short names ("Colflox" for "Colflox 0.3% Pharmedic"), added to it.

Rules and rejection reasons: packages/retrieval/src/visualseek_retrieval/short_aliases.py.
Output: ``<artifacts>/short_aliases.json`` with both lists, every rejection, and the graph /
corpus / identity-review fingerprints it was built against. The API skips it when any of
those changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval import short_aliases  # noqa: E402
from visualseek_retrieval.entity_review import EntityReview  # noqa: E402
from visualseek_retrieval.spotting import EntitySpotter  # noqa: E402
from visualseek_retrieval.text import normalize  # noqa: E402

ARTIFACTS = ROOT / "data/processed/products-v1/search"
PACKAGED_REVIEW = (
    ROOT / "services/api/src/visualseek_api/application/products-v1-identity-review.json"
)


def review_path(artifacts: Path) -> Path:
    """Same resolution as the API: an artifact-dir review wins over the packaged one."""
    local = artifacts / "identity_review.json"
    return local if local.exists() else PACKAGED_REVIEW


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    args = parser.parse_args()
    graph = json.loads((args.artifacts / "graph.json").read_text(encoding="utf-8"))
    raw_review = review_path(args.artifacts).read_bytes()
    review = EntityReview.load(graph, raw_review)

    texts: dict[str, list[str]] = defaultdict(list)
    for entity in graph["entities"]:
        owner = review.canonical(entity["id"])
        texts[owner] += [entity["name"], entity["core"], *entity["aliases"]]
        texts[owner] += review.aliases.get(entity["id"], [])
    for line in (args.artifacts / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        for owner in review.result_ids(row["asset_id"], row["entity_ids"]):
            texts[owner].append(row["doc_text"])
    entity_texts = {owner: normalize(" ".join(parts)) for owner, parts in texts.items()}

    # Suppress shared derived aliases first, so short names are checked against what S1 keeps.
    blocked = short_aliases.suppressed(graph, review.canonical, entity_texts)
    spotter = EntitySpotter(
        graph, np.zeros((len(graph["entities"]), 1)), review, None, blocked["aliases"]
    )
    existing = {
        " ".join(key): {entry.id for entry in entries} for key, entries in spotter.table.items()
    }
    result = short_aliases.build(graph, review.canonical, existing, entity_texts)
    reasons = Counter(row["reason"] for row in result["rejected"])
    fingerprint = hashlib.sha256(
        json.dumps(
            {"aliases": result["aliases"], "suppressed": blocked["aliases"]},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    out = {
        "version": 2,
        "graph_fingerprint": graph["fingerprint"],
        "corpus_fingerprint": graph["corpus_fingerprint"],
        "identity_review_fingerprint": review.fingerprint,
        "fingerprint": fingerprint,
        "counts": {
            "entities_with_alias": len(result["aliases"]),
            "aliases": sum(len(v) for v in result["aliases"].values()),
            "rejected": dict(sorted(reasons.items())),
            "suppressed_graph_aliases": len(blocked["details"]),
        },
        "aliases": result["aliases"],
        "rejected": result["rejected"],
        "suppressed": blocked,
    }
    path = args.artifacts / "short_aliases.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(path), **out["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
