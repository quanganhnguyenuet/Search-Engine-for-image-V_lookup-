"""Local judging page for the descriptive query set (``eval/descriptive-v1``).

Serves the pool from ``build_eval_descriptive.py`` as an image grid; each image gets a grade
0 (not relevant), 1 (right object, attribute wrong or not visible), 2 (object and attribute).
Judgments are appended to ``eval/descriptive-v1/judgments.jsonl`` (versioned); the last line
per query wins, so re-judging never loses history.

    python scripts/judge_server.py            # http://127.0.0.1:8010
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "data/processed/products-v1/search"
QUERIES = ROOT / "eval/descriptive-v1/queries.jsonl"
POOL = ROOT / "data/processed/products-v1/eval/descriptive-v1/pool.jsonl"
JUDGMENTS = ROOT / "eval/descriptive-v1/judgments.jsonl"
PAGE = Path(__file__).resolve().parent / "judge" / "index.html"
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")
GRADES = (0, 1, 2)


# Module level: with postponed annotations FastAPI resolves the body type from module globals;
# a class defined inside create_app() is not found and is silently read as a query parameter.
class Judgment(BaseModel):
    qid: str = Field(max_length=16)
    grades: dict[str, Literal[0, 1, 2]]
    flag: Literal["bad_query"] | None = None
    note: str = Field(default="", max_length=500)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def latest_judgments(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row["qid"]] = row
    return latest


def validate_judgment(pool_ids: list[str] | None, payload: dict) -> str | None:
    """Error message, or None. Every pool image must be graded unless the query is flagged."""
    if pool_ids is None:
        return "unknown qid"
    grades = payload.get("grades") or {}
    if any(grade not in GRADES for grade in grades.values()):
        return "grades must be 0, 1 or 2"
    extra = set(grades) - set(pool_ids)
    if extra:
        return f"{len(extra)} graded images are not in the pool"
    if payload.get("flag") is None and set(grades) != set(pool_ids):
        return f"{len(set(pool_ids) - set(grades))} pool images are not graded"
    return None


def query_order(qid: str) -> str:
    # Mixed kinds in any prefix, so a partly judged set still covers every query type.
    return hashlib.sha256(f"order|{qid}".encode()).hexdigest()


def build_state(queries: list[dict], pool: list[dict], names: dict[str, list[str]]) -> list[dict]:
    by_qid = {row["qid"]: row for row in pool}
    state = []
    for query in sorted(queries, key=lambda q: query_order(q["qid"])):
        row = by_qid.get(query["qid"])
        if row is None:
            continue
        candidates = [
            {"asset_id": c["asset_id"], "names": names.get(c["asset_id"], [])}
            for c in row["candidates"]
        ]
        state.append({**query, "candidates": candidates})
    return state


def create_app(judge: str):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    graph = json.loads((ARTIFACTS / "graph.json").read_text(encoding="utf-8"))
    entity_names = {entity["id"]: entity["name"] for entity in graph["entities"]}
    image_root = Path(
        json.loads((ARTIFACTS.parent / "manifest.json").read_text(encoding="utf-8"))["image_root"]
    )
    image_paths: dict[str, str] = {}
    names: dict[str, list[str]] = {}
    for row in read_jsonl(ARTIFACTS / "doc_texts.jsonl"):
        image_paths[row["asset_id"]] = row["image_path"]
        names[row["asset_id"]] = [entity_names.get(e, e) for e in row["entity_ids"]]

    pool = read_jsonl(POOL)
    if not pool:
        raise SystemExit(f"{POOL} is missing; run scripts/build_eval_descriptive.py first")
    pool_sha = hashlib.sha256(POOL.read_bytes()).hexdigest()
    pool_ids = {row["qid"]: [c["asset_id"] for c in row["candidates"]] for row in pool}
    pooled = {asset_id for ids in pool_ids.values() for asset_id in ids}
    state = build_state(read_jsonl(QUERIES), pool, names)
    lock = threading.Lock()

    app = FastAPI(title="descriptive-v1 judging")

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/state")
    def get_state() -> dict:
        return {"queries": state, "judgments": latest_judgments(read_jsonl(JUDGMENTS))}

    @app.get("/img/{asset_id}")
    def image(asset_id: str):
        if not ASSET_ID.match(asset_id) or asset_id not in pooled:
            raise HTTPException(404)
        path = image_root / image_paths[asset_id]
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/judgments")
    def save(judgment: Judgment) -> dict:
        payload = judgment.model_dump()
        error = validate_judgment(pool_ids.get(judgment.qid), payload)
        if error:
            raise HTTPException(422, error)
        row = {
            **payload,
            "judge": judge,
            "pool_sha256": pool_sha,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with lock:
            JUDGMENTS.parent.mkdir(parents=True, exist_ok=True)
            with JUDGMENTS.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True, "at": row["at"]}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--judge", default="judge-1", help="label stored with each judgment")
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.judge), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
