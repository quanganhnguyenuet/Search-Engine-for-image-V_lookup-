"""HTTP latency benchmark; LLM and non-LLM runs have separate latency budgets."""

import argparse
import json
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import httpx
import numpy as np

from visualseek_retrieval.artifact import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, choices=(1, 4), default=1)
    llm = parser.add_mutually_exclusive_group()
    llm.add_argument("--use-llm", action="store_true")
    llm.add_argument("--auto-llm", action="store_true")
    args = parser.parse_args()
    use_llm = None if args.auto_llm else args.use_llm
    if args.requests < 1:
        parser.error("requests must be positive")
    queries = [json.loads(line)["query"] for line in args.queries.read_text().splitlines()
               if line.strip()]
    if not queries:
        parser.error("queries must not be empty")
    with httpx.Client(timeout=210) as client:
        status = client.get(args.api.rstrip("/") + "/api/v1/search/status")
        status.raise_for_status()

        def request(index):
            started = time.perf_counter()
            try:
                response = client.post(args.api.rstrip("/") + "/api/v1/search/text", json={
                    "query": queries[index % len(queries)], "use_llm": use_llm, "top_k": 24})
                response.raise_for_status()
                data = response.json()
                return {"elapsed_ms": (time.perf_counter() - started) * 1000,
                        "ok": True, "plan_cache_hit": data["plan_cache_hit"],
                        "search_route": data.get("search_route"),
                        "llm_call_count": data.get("llm_call_count"),
                        "timings": data["timings"]}
            except (httpx.HTTPError, ValueError) as exc:
                return {"ok": False, "error": type(exc).__name__}

        cold = request(0)
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            rows = list(executor.map(request, range(args.requests)))
    good = [row["elapsed_ms"] for row in rows if row["ok"]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, {"created_at": datetime.now(UTC).isoformat(),
                            "hardware": platform.platform() + " / " + platform.processor(),
                            "concurrency": args.concurrency, "use_llm": use_llm,
                            "requests": args.requests, "status": status.json(), "cold": cold,
                            "errors": sum(not row["ok"] for row in rows),
                            "latency_ms": {f"p{p}": float(np.percentile(good, p))
                                           for p in (50, 90, 95)} if good else None,
                            "samples": rows,
                            "cache_note": "Repeated queries may hit planner cache; see samples"})


if __name__ == "__main__":
    main()
