"""Local page for a person to review B2 category labels (scripts/graph_classify.py).

Labels are shown grouped by L2 category; a page is approved in one action, after the
reviewer has fixed only the cards that are wrong (category, product type, market tier).
Every decision is appended to eval/graph-review/category_decisions.jsonl; the last
decision per entity wins. Low-confidence and knowledge-only labels come first.

    python scripts/category_review_server.py --labels pilot --reviewer <name>
    # http://127.0.0.1:8012
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/processed/products-v1"
GRAPH_DIR = CORPUS / "graph"
TAXONOMY = ROOT / "graph/taxonomy-v1.json"
DECISIONS = ROOT / "eval/graph-review/category_decisions.jsonl"
PAGE = Path(__file__).resolve().parent / "category_review" / "index.html"
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")
THUMBS = 3
TIERS = ("luxury", "premium", "mass", "unknown", "not_applicable")
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class Item(BaseModel):
    entity_id: str = Field(max_length=64)
    verdict: Literal["ok", "fix"]
    category: str = Field(max_length=64)
    product_type: str = Field(max_length=80)
    market_tier: Literal["luxury", "premium", "mass", "unknown", "not_applicable"]


class PageDecision(BaseModel):
    items: list[Item] = Field(max_length=500)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def latest(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        out[row["entity_id"]] = row
    return out


def validate(items: list[Item], labels: dict[str, dict], categories: set[str]) -> str | None:
    for item in items:
        if item.entity_id not in labels:
            return f"unknown entity {item.entity_id}"
        if item.category not in categories:
            return f"unknown category {item.category}"
        if not item.product_type.strip():
            return "product_type is empty"
        if item.category.split(".")[0] != "fashion" and item.market_tier != "not_applicable":
            return f"market_tier only applies to fashion ({item.entity_id})"
    return None


def create_app(labels_name: str, reviewer: str):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    categories = {
        c["id"]: {"name": c["name"], "l1": l1["name"]}
        for l1 in taxonomy["categories"]
        for c in l1["children"]
    }
    labels = {r["entity_id"]: r for r in read_jsonl(GRAPH_DIR / f"labels-{labels_name}.jsonl")}
    if not labels:
        raise SystemExit(f"labels-{labels_name}.jsonl is missing; run graph_classify.py run")
    packs = {p["entity_id"]: p for p in read_jsonl(GRAPH_DIR / "evidence.jsonl")}
    members = {eid: set(p["members"]) for eid, p in packs.items() if eid in labels}
    owner_of = {m: eid for eid, ms in members.items() for m in ms}
    thumbs: dict[str, list[str]] = defaultdict(list)
    paths: dict[str, str] = {}
    for row in read_jsonl(CORPUS / "search/doc_texts.jsonl"):
        for member in row["entity_ids"]:
            owner = owner_of.get(member)
            if owner and len(thumbs[owner]) < THUMBS and row["asset_id"] not in thumbs[owner]:
                thumbs[owner].append(row["asset_id"])
                paths[row["asset_id"]] = row["image_path"]
    image_root = Path(json.loads((CORPUS / "manifest.json").read_text("utf-8"))["image_root"])
    lock = threading.Lock()
    app = FastAPI(title="B2 category review")

    def cards() -> list[dict]:
        decided = latest(read_jsonl(DECISIONS))
        out = []
        for eid, label in labels.items():
            snippets = {s["id"]: s["text"] for s in packs[eid]["snippets"]}
            out.append(
                {
                    "entity_id": eid,
                    "name": label["name"],
                    "label": {k: label[k] for k in ("category", "product_type", "market_tier")},
                    "basis": label["basis"],
                    "confidence": label["confidence"],
                    "evidence": [
                        {"id": e, "text": snippets[e]} for e in label["evidence"] if e in snippets
                    ][:3],
                    "images": thumbs[eid],
                    "decision": decided.get(eid),
                }
            )
        out.sort(
            key=lambda c: (
                CONFIDENCE_ORDER[c["confidence"]],
                c["basis"] != "knowledge",
                c["name"],
            )
        )
        return out

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/state")
    def state() -> dict:
        return {
            "reviewer": reviewer,
            "labels": labels_name,
            "categories": categories,
            "tiers": TIERS,
            "cards": cards(),
        }

    @app.get("/img/{asset_id}")
    def image(asset_id: str):
        if not ASSET_ID.match(asset_id) or asset_id not in paths:
            raise HTTPException(404)
        path = image_root / paths[asset_id]
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/decisions")
    def save(decision: PageDecision) -> dict:
        error = validate(decision.items, labels, set(categories))
        if error:
            raise HTTPException(422, error)
        at = datetime.now(UTC).isoformat(timespec="seconds")
        with lock:
            DECISIONS.parent.mkdir(parents=True, exist_ok=True)
            with DECISIONS.open("a", encoding="utf-8", newline="\n") as handle:
                for item in decision.items:
                    row = {
                        **item.model_dump(),
                        "model_label": labels[item.entity_id]["category"],
                        "reviewer": reviewer,
                        "at": at,
                    }
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True, "at": at, "saved": len(decision.items)}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default="pilot", help="pilot or all")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--reviewer", default="human-1")
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.labels, args.reviewer), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
