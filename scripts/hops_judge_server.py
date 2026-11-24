"""Local page to grade the eval/hops-v1 pool (scripts/build_eval_hops.py) per product.

multi_hop queries: 0 wrong, 1 partly (e.g. right brand, wrong product type asked for),
2 right. multi_intent queries: which part of the query ("vế") the product answers, or wrong.
Every save appends to eval/<set>/judgments.jsonl; the last line per query wins.

    python scripts/hops_judge_server.py --judge <name> [--set hops-v2-holdout]   # :8013
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/processed/products-v1"
DEFAULT_SET = "hops-v1"
PAGE = Path(__file__).resolve().parent / "hops_judge" / "index.html"
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


class Grade(BaseModel):
    grade: Literal[0, 1, 2]
    intent: int | None = Field(default=None, ge=0, le=5)


class Judgment(BaseModel):
    qid: str = Field(max_length=16)
    grades: dict[str, Grade]
    flag: Literal["bad_query"] | None = None
    note: str = Field(default="", max_length=500)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def latest(rows: list[dict]) -> dict[str, dict]:
    return {row["qid"]: row for row in rows}


def validate(judgment: Judgment, query: dict | None, pool: dict | None) -> str | None:
    if query is None or pool is None:
        return f"unknown query {judgment.qid}"
    ids = {e["entity_id"] for e in pool["entities"]}
    # Every product of the current pool needs a grade. Grades of products an earlier pool
    # showed are kept: a rebuilt pool (new configurations) must not block saving or drop them.
    missing = ids - set(judgment.grades)
    if missing:
        return f"{len(missing)} product(s) of the query are not graded"
    parts = len(query["intents"] or [])
    for entity_id, grade in judgment.grades.items():
        if query["kind"] == "multi_intent" and grade.grade == 2:
            if grade.intent is None or not 0 <= grade.intent < parts:
                return f"{entity_id}: a relevant product must name its part of the query"
        elif grade.intent is not None:
            return f"{entity_id}: only relevant multi_intent products carry a part"
    return None


def create_app(judge: str, name: str = DEFAULT_SET):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    judgments = ROOT / "eval" / name / "judgments.jsonl"
    queries = {q["qid"]: q for q in read_jsonl(ROOT / "eval" / name / "queries.jsonl")}
    pool = {p["qid"]: p for p in read_jsonl(CORPUS / "eval" / name / "pool.jsonl")}
    if not pool:
        raise SystemExit(f"pool is missing: run scripts/build_eval_hops.py --set {name}")
    paths = {
        asset["asset_id"]: asset["image_path"]
        for row in pool.values()
        for entity in row["entities"]
        for asset in entity["assets"]
    }
    image_root = Path(json.loads((CORPUS / "manifest.json").read_text("utf-8"))["image_root"])
    lock = threading.Lock()
    app = FastAPI(title=f"{name} judging")

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/state")
    def state() -> dict:
        decided = latest(read_jsonl(judgments))
        return {
            "judge": judge,
            "set": name,
            "queries": [
                {**queries[qid], **pool[qid], "judgment": decided.get(qid)}
                for qid in queries
                if qid in pool
            ],
        }

    @app.get("/img/{asset_id}")
    def image(asset_id: str):
        if not ASSET_ID.match(asset_id) or asset_id not in paths:
            raise HTTPException(404)
        path = image_root / paths[asset_id]
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/judgments")
    def save(judgment: Judgment) -> dict:
        error = validate(judgment, queries.get(judgment.qid), pool.get(judgment.qid))
        if error:
            raise HTTPException(422, error)
        at = datetime.now(UTC).isoformat(timespec="seconds")
        row = {**judgment.model_dump(), "judge": judge, "at": at}
        with lock:
            judgments.parent.mkdir(parents=True, exist_ok=True)
            with judgments.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True, "at": at}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--judge", default="human-1")
    parser.add_argument("--set", default=DEFAULT_SET, help="query set under eval/")
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.judge, args.set), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
