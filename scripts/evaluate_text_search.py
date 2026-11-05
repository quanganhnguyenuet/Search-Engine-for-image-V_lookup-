"""Collect blind-label candidates or score an already human-judged pool."""

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import httpx

from visualseek_evaluation.relevance import evaluate_pool
from visualseek_retrieval.artifact import write_json


def collect(queries: Path, output: Path, api: str, web: str, use_llm: bool):
    records = []
    with httpx.Client(timeout=210) as client:
        status = client.get(api + "/api/v1/search/status")
        status.raise_for_status()
        for line in queries.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            query = json.loads(line)
            body = {"query": query["query"], "top_k": 30, "category": query.get("category")}
            responses = {}
            for mode in ("semantic", "contextual", "keyword"):
                if mode == "keyword":
                    response = client.get(web + "/api/culture", params={
                        "q": query["query"], "category": query.get("category") or "", "page": 1})
                else:
                    response = client.post(api + "/api/v1/search/" + (
                        "text" if mode == "contextual" else "semantic"),
                        json={**body, **({"use_llm": use_llm} if mode == "contextual" else {})})
                response.raise_for_status()
                responses[mode] = response.json()
            candidates = {item["id"]: item for response in responses.values()
                          for item in response["items"]}
            # Stable ID ordering hides which retriever ranked an image first.
            records.append({**query, "judgments": {id_: None for id_ in sorted(candidates)},
                            "candidates": [candidates[id_] for id_ in sorted(candidates)],
                            "rankings": {mode: [item["id"] for item in response["items"]]
                                         for mode, response in responses.items()}})
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json(output, {"created_at": datetime.now(UTC).isoformat(),
                                "query_set_sha256": hashlib.sha256(
                                    queries.read_bytes()).hexdigest(),
                                "status": status.json(), "use_llm": use_llm,
                                "samples": records})
            print(f"Collected {query['query_id']} ({len(candidates)} candidates)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--queries", type=Path, required=True)
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--api", default="http://127.0.0.1:8000")
    collect_parser.add_argument("--web", default="http://127.0.0.1:3000")
    collect_parser.add_argument("--use-llm", action="store_true")
    score = sub.add_parser("score")
    score.add_argument("--pool", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--split", choices=("development", "held-out"), default="held-out")
    args = parser.parse_args()
    if args.command == "collect":
        collect(args.queries, args.output, args.api.rstrip("/"), args.web.rstrip("/"), args.use_llm)
        return
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    samples = [row for row in pool["samples"] if row["split"] == args.split]
    if not samples:
        parser.error("No samples in selected split")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, {"created_at": datetime.now(UTC).isoformat(),
                            "platform": platform.platform(), "split": args.split,
                            "pool_sha256": hashlib.sha256(args.pool.read_bytes()).hexdigest(),
                            "status": pool["status"], "use_llm": pool["use_llm"],
                            "reports": [evaluate_pool(samples, mode) for mode in (
                                "semantic", "contextual", "keyword")]})


if __name__ == "__main__":
    main()
