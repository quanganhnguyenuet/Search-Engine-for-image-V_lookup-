"""Local page for a person to review B1 duplicate-entity candidates.

The queue is ``pending_pairs`` from scripts/audit_entity_review.py (name similarity, shared
images, entity_dense), strongest agreement first. Each decision is written to the identity
review the API loads (SAME_AS / VARIANT_OF / DISTINCT, with reviewer and time), after
EntityReview.load accepts the result, and appended to eval/identity-review/decisions.jsonl.

    python scripts/audit_entity_review.py            # refresh the queue
    python scripts/pair_review_server.py --reviewer <name>   # http://127.0.0.1:8011

After reviewing: python scripts/build_short_aliases.py, then restart the API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.entity_review import EntityReview  # noqa: E402

CORPUS = ROOT / "data/processed/products-v1"
ARTIFACTS = CORPUS / "search"
QUEUE = CORPUS / "eval/identity-review-v1.json"
PACKAGED_REVIEW = (
    ROOT / "services/api/src/visualseek_api/application/products-v1-identity-review.json"
)
DECISIONS = ROOT / "eval/identity-review/decisions.jsonl"
PAGE = Path(__file__).resolve().parent / "pair_review" / "index.html"
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")
IMAGES_PER_ENTITY = 8
TITLES_PER_ENTITY = 5


class Decision(BaseModel):
    src: str = Field(max_length=64)
    dst: str = Field(max_length=64)
    decision: Literal["SAME_AS", "VARIANT_OF", "DISTINCT", "SKIP"]
    keep: str | None = Field(default=None, max_length=64)
    note: str = Field(default="", max_length=500)


def review_path() -> Path:
    """Same resolution as the API: an artifact-dir review wins over the packaged one."""
    local = ARTIFACTS / "identity_review.json"
    return local if local.exists() else PACKAGED_REVIEW


def pair_key(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))


def apply_decision(
    data: dict, decision: Decision, reviewer: str, at: str, shared_assets: list[str]
) -> dict:
    """The review with this pair's decision replaced; SKIP leaves the review unchanged."""
    if decision.decision == "SKIP":
        return data
    key = pair_key(decision.src, decision.dst)
    pairs = [p for p in data["entity_pairs"] if pair_key(p["src"], p["dst"]) != key]
    if decision.decision == "SAME_AS":
        if decision.keep not in key:
            raise ValueError("SAME_AS needs the entity to keep, one of the pair")
        # src is merged into dst: dst stays the canonical entity.
        src, dst = (key[1], key[0]) if decision.keep == key[0] else key
    else:
        src, dst = decision.src, decision.dst
    evidence = [{"text": decision.note.strip() or "Người duyệt xác nhận trên trang duyệt cặp."}]
    evidence += [{"asset_id": a, "text": "Ảnh dùng chung của hai nhãn."} for a in shared_assets[:3]]
    pairs.append(
        {
            "src": src,
            "dst": dst,
            "decision": decision.decision,
            "evidence": evidence,
            "reviewer": reviewer,
            "reviewed_at": at,
        }
    )
    return {**data, "entity_pairs": pairs}


def entity_cards(graph: dict, review: EntityReview, wanted: set[str], doc_rows: list[dict]):
    """Name, sample images and page titles per entity, from the indexed corpus."""
    names = {e["id"]: e["name"] for e in graph["entities"]}
    images: dict[str, list[str]] = defaultdict(list)
    titles: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    paths: dict[str, str] = {}
    for row in doc_rows:
        for entity_id in set(row["entity_ids"]) & wanted:
            if entity_id in review.rejected_labels.get(row["asset_id"], set()):
                continue
            counts[entity_id] += 1
            images[entity_id].append(row["asset_id"])
            paths[row["asset_id"]] = row["image_path"]
            title = (row.get("page_title") or "").strip()
            if title and title not in titles[entity_id]:
                titles[entity_id].append(title)
    cards = {
        entity_id: {
            "id": entity_id,
            "name": names[entity_id],
            "images": images[entity_id],
            "image_count": counts[entity_id],
            "titles": titles[entity_id][:TITLES_PER_ENTITY],
        }
        for entity_id in wanted
    }
    return cards, paths


def create_app(reviewer: str):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    graph = json.loads((ARTIFACTS / "graph.json").read_text(encoding="utf-8"))
    queue_file = json.loads(QUEUE.read_text(encoding="utf-8"))
    if queue_file["corpus_fingerprint"] != graph["corpus_fingerprint"]:
        raise SystemExit("The queue belongs to another corpus; run audit_entity_review.py")
    queue = queue_file["pending_pairs"]
    if not queue:
        raise SystemExit("No pending pairs; run scripts/audit_entity_review.py")
    target = review_path()
    review = EntityReview.load(graph, target.read_bytes())
    wanted = {eid for pair in queue for eid in (pair["src"], pair["dst"])}
    doc_rows = [
        json.loads(line)
        for line in (ARTIFACTS / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    cards, paths = entity_cards(graph, review, wanted, doc_rows)
    image_root = Path(json.loads((CORPUS / "manifest.json").read_text("utf-8"))["image_root"])
    shared = {pair_key(p["src"], p["dst"]): p.get("shared_assets", []) for p in queue}
    names = {e["id"]: e["name"] for e in graph["entities"]}
    lock = threading.Lock()
    app = FastAPI(title="B1 pair review")

    def state() -> dict:
        data = json.loads(target.read_text(encoding="utf-8"))
        current = EntityReview.load(graph, target.read_bytes())
        decided = {pair_key(p["src"], p["dst"]): p for p in data["entity_pairs"]}
        skipped = {}
        if DECISIONS.exists():
            for line in DECISIONS.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row["decision"] == "SKIP":
                    skipped[pair_key(row["src"], row["dst"])] = row["at"]
                else:
                    skipped.pop(pair_key(row["src"], row["dst"]), None)
        pairs = []
        for pair in queue:
            key = pair_key(pair["src"], pair["dst"])
            done = decided.get(key)
            pairs.append(
                {
                    **{k: pair[k] for k in ("src", "dst", "found_by", "name_similarity")},
                    "dense_cosine": pair.get("dense_cosine"),
                    "shared_assets": pair.get("shared_assets", []),
                    "decision": done
                    and {k: done.get(k) for k in ("src", "dst", "decision", "reviewer")},
                    "note": done and done["evidence"][0].get("text", ""),
                    "skipped": key in skipped and not done,
                    # An entity already merged elsewhere cannot be merged a second time.
                    "merged_into": {
                        eid: names[current.canonical(eid)]
                        for eid in key
                        if current.canonical(eid) != eid
                    },
                }
            )
        return {"reviewer": reviewer, "review_file": str(target), "pairs": pairs, "cards": cards}

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/state")
    def get_state() -> dict:
        return state()

    @app.get("/img/{asset_id}")
    def image(asset_id: str):
        if not ASSET_ID.match(asset_id) or asset_id not in paths:
            raise HTTPException(404)
        path = image_root / paths[asset_id]
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/decisions")
    def save(decision: Decision) -> dict:
        key = pair_key(decision.src, decision.dst)
        if key not in shared:
            raise HTTPException(422, "pair is not in the review queue")
        at = datetime.now(UTC).isoformat(timespec="seconds")
        with lock:
            data = json.loads(target.read_text(encoding="utf-8"))
            try:
                updated = apply_decision(data, decision, reviewer, at, shared[key])
                raw = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                EntityReview.load(graph, raw)  # rejects cycles and double merges
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            if updated is not data:
                tmp = target.with_suffix(".json.tmp")
                tmp.write_bytes(raw)
                os.replace(tmp, target)
            DECISIONS.parent.mkdir(parents=True, exist_ok=True)
            with DECISIONS.open("a", encoding="utf-8", newline="\n") as handle:
                row = {**decision.model_dump(), "reviewer": reviewer, "at": at}
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True, "at": at}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--reviewer", default="human-1", help="stored with each decision")
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.reviewer), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
