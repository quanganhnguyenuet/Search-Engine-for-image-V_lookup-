"""Latency of the live search API, by flow, at a human pace, and under concurrent load.

Flows are sampled from eval sets that are not the sealed holdout:

  silver types   silver-v1.1 dev: single_named, single_brand, multi_intent, multi_hop, no_answer
  category       eval/category-v1 single-category queries
  descriptive    eval/descriptive-v1 dev (colour, material, shape: the visual fusion path)
  place          "đặc sản <place>" for places with several food products (ORIGIN path)

Scenarios (the API must already be running; nothing is cached between requests):

  flows     every query once, sequentially, shuffled with a fixed seed; per flow client p50, p95,
            p99 and the API's own stage timings
  paced     one query every ``--think`` seconds (a person reading results): this is where idle
            connections to the vector store used to cost ~380 ms
  load      ``--clients`` threads send queries back to back for ``--seconds`` each; the API runs
            one search at a time (one GPU), so this shows throughput and queueing

  python scripts/bench_latency.py --out runs/latency.json [--per-flow 25] [--think 10]
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
SILVER = ROOT / "data/processed/products-v1/eval/silver-v1.1/dev.jsonl"
PLACES = ("Hà Nội", "Huế", "Sài Gòn", "Đà Lạt", "Bến Tre", "Phú Quốc", "Quảng Nam", "An Giang")
STAGES = ("embed_query", "s1_spotting", "s2_expand", "embed_image", "qdrant", "similarity", "total")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def search(query: str, timeout: float = 30.0) -> tuple[float, dict | None]:
    request = urllib.request.Request(
        f"{API}/api/v1/search/text",
        json.dumps({"query": query}).encode(),
        {"Content-Type": "application/json"},
    )
    tick = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except Exception:  # an error is counted by the caller, not hidden
        return (time.perf_counter() - tick) * 1000, None
    return (time.perf_counter() - tick) * 1000, body


def pct(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    data = np.asarray(values)
    return {
        "n": len(values),
        "p50": round(float(np.percentile(data, 50)), 1),
        "p95": round(float(np.percentile(data, 95)), 1),
        "p99": round(float(np.percentile(data, 99)), 1),
        "mean": round(float(data.mean()), 1),
        "max": round(float(data.max()), 1),
    }


def flow_queries(per_flow: int, seed: int) -> dict[str, list[str]]:
    rng = random.Random(seed)
    silver = jsonl(SILVER)
    flows: dict[str, list[str]] = {}
    for kind in ("single_named", "single_brand", "multi_intent", "multi_hop", "no_answer"):
        pool = [row["query"] for row in silver if row["type"] == kind]
        flows[f"silver:{kind}"] = rng.sample(pool, min(per_flow, len(pool)))
    category = [
        row["query"]
        for row in jsonl(ROOT / "eval/category-v1/queries.jsonl")
        if row["kind"] == "category"
    ]
    flows["category"] = rng.sample(category, min(per_flow, len(category)))
    descriptive = [
        row["query"]
        for row in jsonl(ROOT / "eval/descriptive-v1/queries.jsonl")
        if row["split"] == "dev"
    ]
    flows["descriptive"] = rng.sample(descriptive, min(per_flow, len(descriptive)))
    flows["place"] = [f"đặc sản {place}" for place in PLACES]
    return flows


def run_flows(flows: dict[str, list[str]], seed: int) -> dict:
    items = [(flow, query) for flow, queries in flows.items() for query in queries]
    random.Random(seed).shuffle(items)
    client: dict[str, list[float]] = defaultdict(list)
    stages: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    errors = 0
    for flow, query in items:
        ms, body = search(query)
        if body is None:
            errors += 1
            continue
        client[flow].append(ms)
        for stage in STAGES:
            if stage in body["timings"]:
                stages[flow][stage].append(body["timings"][stage])
    everything = [ms for values in client.values() for ms in values]
    return {
        "errors": errors,
        "all": pct(everything),
        "by_flow": {
            flow: {
                "client": pct(client[flow]),
                "stage_median_ms": {
                    stage: round(float(np.median(values)), 1)
                    for stage, values in stages[flow].items()
                },
            }
            for flow in flows
        },
    }


def run_paced(flows: dict[str, list[str]], think: float, count: int, seed: int) -> dict:
    pool = [query for queries in flows.values() for query in queries]
    random.Random(seed + 1).shuffle(pool)
    values, errors = [], 0
    for query in pool[:count]:
        time.sleep(think)
        ms, body = search(query)
        if body is None:
            errors += 1
        else:
            values.append(ms)
    return {"think_seconds": think, "errors": errors, **pct(values)}


def run_load(flows: dict[str, list[str]], clients: int, seconds: float, seed: int) -> dict:
    pool = [query for queries in flows.values() for query in queries]
    stop = time.perf_counter() + seconds
    lock = threading.Lock()
    values: list[float] = []
    errors = 0

    def worker(index: int) -> None:
        nonlocal errors
        rng = random.Random(seed * 100 + index)
        while time.perf_counter() < stop:
            ms, body = search(rng.choice(pool))
            with lock:
                if body is None:
                    errors += 1
                else:
                    values.append(ms)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=clients) as executor:
        list(executor.map(worker, range(clients)))
    elapsed = time.perf_counter() - started
    return {
        "clients": clients,
        "seconds": round(elapsed, 1),
        "errors": errors,
        "qps": round(len(values) / elapsed, 2),
        **pct(values),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-flow", type=int, default=25)
    parser.add_argument("--think", type=float, default=10.0)
    parser.add_argument("--paced-count", type=int, default=12)
    parser.add_argument("--clients", type=int, nargs="*", default=[1, 2, 4, 8])
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    info = json.load(urllib.request.urlopen(f"{API}/api/v1/search/info"))
    flows = flow_queries(args.per_flow, args.seed)
    for query in [q for queries in flows.values() for q in queries][:6]:
        search(query)  # a few untimed requests so JIT, caches of the encoders and pools are warm
    report = {
        "api": {
            key: info.get(key)
            for key in ("collection", "points", "encoder_device", "qdrant_transport")
        },
        "queries_per_flow": {flow: len(queries) for flow, queries in flows.items()},
    }
    print("flows ...", flush=True)
    report["flows"] = run_flows(flows, args.seed)
    print(json.dumps(report["flows"]["all"]), flush=True)
    print(f"paced (think {args.think:.0f}s) ...", flush=True)
    report["paced"] = run_paced(flows, args.think, args.paced_count, args.seed)
    print(json.dumps(report["paced"]), flush=True)
    report["load"] = []
    for clients in args.clients:
        print(f"load x{clients} ...", flush=True)
        report["load"].append(run_load(flows, clients, args.seconds, args.seed))
        print(json.dumps(report["load"][-1]), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
