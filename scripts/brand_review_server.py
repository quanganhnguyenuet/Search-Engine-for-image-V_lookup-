"""Local page for a person to review the MADE_BY edges of the graph (product -> brand).

graph-lite builds every MADE_BY edge from free sources (JSON-LD brand, or a capitalised word
that starts >= 2 names) and no edge has been reviewed. Wrong edges send brand queries to
unrelated products ("điện thoại Samsung" -> the SYM Galaxy motorbike, through "Galaxy").

The queue is one page per brand. Brands whose products fall in another top-level category
than most of the brand's products, or whose edges rest on the name alone, come first. A page
is approved in one action after the reviewer marks only the wrong edges: remove the edge, or
move the product to another (or a new) brand. Every decision is appended to
eval/graph-review/brand_decisions.jsonl; the last decision per entity wins.

The decisions do not change the search yet: graph.json is the snapshot the Qdrant collection
was built from (the API checks its fingerprint), so applying them is a rebuild step.

    python scripts/brand_review_server.py --reviewer <name>      # http://127.0.0.1:8014
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/processed/products-v1"
SEARCH = CORPUS / "search"
DECISIONS = ROOT / "eval/graph-review/brand_decisions.jsonl"
PAGE = Path(__file__).resolve().parent / "brand_review" / "index.html"
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")
THUMBS = 3


class Item(BaseModel):
    entity_id: str = Field(max_length=64)
    verdict: Literal["ok", "reject", "reassign"]
    to_brand: str = Field(default="", max_length=80)


class PageDecision(BaseModel):
    brand_id: str = Field(max_length=120)
    items: list[Item] = Field(max_length=500)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def latest(rows: list[dict]) -> dict[str, dict]:
    return {row["entity_id"]: row for row in rows}


def resolve_brand(text: str, brands: dict[str, dict]) -> str | None:
    """A brand id from an id or a name typed by the reviewer; ``new:<name>`` for a new one."""
    text = text.strip()
    if not text:
        return None
    if text in brands:
        return text
    folded = text.casefold()
    for brand_id, brand in brands.items():
        if folded == brand["name"].casefold() or folded in brand.get("aliases", ()):
            return brand_id
    return f"new:{text}"


def validate(decision: PageDecision, members: dict[str, list[str]]) -> str | None:
    page = set(members.get(decision.brand_id, ()))
    if not page:
        return f"unknown brand {decision.brand_id}"
    for item in decision.items:
        if item.entity_id not in page:
            return f"{item.entity_id} is not a product of {decision.brand_id}"
        if item.verdict == "reassign" and not item.to_brand.strip():
            return f"{item.entity_id}: name the brand to move it to"
    return None


def create_app(reviewer: str):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    graph = json.loads((SEARCH / "graph.json").read_text(encoding="utf-8"))
    labels = json.loads((SEARCH / "entity_categories.json").read_text(encoding="utf-8"))
    category_names = labels["category_names"]
    info = labels["entities"]
    entities = {e["id"]: e for e in graph["entities"]}
    brands = {b["id"]: b for b in graph["brands"]}
    members: dict[str, list[str]] = defaultdict(list)
    edges: dict[str, dict] = {}
    for edge in graph["edges"]:
        if edge["rel"] == "MADE_BY":
            members[edge["dst"]].append(edge["src"])
            edges[edge["src"]] = edge
    thumbs: dict[str, list[str]] = defaultdict(list)
    paths: dict[str, str] = {}
    for row in read_jsonl(SEARCH / "doc_texts.jsonl"):
        for entity_id in row["entity_ids"]:
            if entity_id in edges and len(thumbs[entity_id]) < THUMBS:
                thumbs[entity_id].append(row["asset_id"])
                paths[row["asset_id"]] = row["image_path"]
    image_root = Path(json.loads((CORPUS / "manifest.json").read_text("utf-8"))["image_root"])
    lock = threading.Lock()
    app = FastAPI(title="MADE_BY review")

    def card(entity_id: str, majority: str | None) -> dict:
        label = info.get(entity_id) or {}
        category = label.get("category")
        edge = edges[entity_id]
        return {
            "entity_id": entity_id,
            "name": entities[entity_id]["name"],
            "product_type": label.get("product_type"),
            "category": category_names.get(category, category),
            "source": edge["source"],
            "confidence": edge["confidence"],
            # Another top-level category than most of the brand's products: worth a look.
            "outlier": bool(majority and category and category.split(".")[0] != majority),
            "images": thumbs[entity_id],
        }

    def pages() -> list[dict]:
        decided = latest(read_jsonl(DECISIONS))
        out = []
        for brand_id, ids in members.items():
            l1 = Counter(
                (info.get(e) or {}).get("category", "").split(".")[0]
                for e in ids
                if (info.get(e) or {}).get("category")
            )
            top = l1.most_common(1)
            majority = top[0][0] if len(ids) >= 2 and top else None
            cards = [{**card(e, majority), "decision": decided.get(e)} for e in ids]
            cards.sort(key=lambda c: (not c["outlier"], c["confidence"], c["name"]))
            out.append(
                {
                    "id": brand_id,
                    "name": brands[brand_id]["name"],
                    "aliases": brands[brand_id].get("aliases", []),
                    "outliers": sum(c["outlier"] for c in cards),
                    "name_only": sum(c["source"] == "name" for c in cards),
                    "cards": cards,
                }
            )
        out.sort(
            key=lambda p: (
                all(c["decision"] for c in p["cards"]),
                -p["outliers"],
                -p["name_only"],
                -len(p["cards"]),
                p["name"].casefold(),
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
            "brands": pages(),
            "options": sorted(
                ({"id": b["id"], "name": b["name"]} for b in brands.values()),
                key=lambda b: b["name"].casefold(),
            ),
            "unbranded": sum(1 for e in graph["entities"] if not e.get("brand_id")),
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
        error = validate(decision, members)
        if error:
            raise HTTPException(422, error)
        at = datetime.now(UTC).isoformat(timespec="seconds")
        with lock:
            DECISIONS.parent.mkdir(parents=True, exist_ok=True)
            with DECISIONS.open("a", encoding="utf-8", newline="\n") as handle:
                for item in decision.items:
                    edge = edges[item.entity_id]
                    row = {
                        "entity_id": item.entity_id,
                        "name": entities[item.entity_id]["name"],
                        "from_brand": decision.brand_id,
                        "verdict": item.verdict,
                        "to_brand": (
                            resolve_brand(item.to_brand, brands)
                            if item.verdict == "reassign"
                            else None
                        ),
                        "source": edge["source"],
                        "confidence": edge["confidence"],
                        "graph_fingerprint": graph["fingerprint"],
                        "reviewer": reviewer,
                        "at": at,
                    }
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True, "at": at, "saved": len(decision.items)}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8014)
    parser.add_argument("--reviewer", default="human-1")
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.reviewer), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
