"""Prepare image queries, then measure exact cosine baseline quality and latency."""

import argparse
import base64
import hashlib
import json
import os
import platform
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import httpx
import numpy as np

from visualseek_evaluation.image import (
    percentiles,
    prepare_queries,
    score_ranking,
    validate_queries,
)
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api/src"))
from visualseek_api.application.search import SearchService  # noqa: E402
from visualseek_api.core.config import Settings  # noqa: E402


def benchmark(args, settings: Settings, index: ArtifactIndex) -> dict:
    queries = [json.loads(line) for line in args.queries.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    validate_queries(index, queries)
    if args.requests < len(queries):
        raise ValueError("requests must cover every query at least once")
    bodies = []
    for row in queries:
        body = {"top_k": max(args.k), "category": row.get("category")}
        if args.query_mode == "stored-vector":
            body["asset_id"] = row["asset_id"]
        else:
            asset = index.assets[index.positions[row["asset_id"]]]
            root = Path(settings.search_image_root).resolve()
            path = (root / asset["image_path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Image path escapes image root")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != row["asset_id"]:
                raise ValueError("Query image checksum does not match artifact asset ID")
            body["image_base64"] = base64.b64encode(content).decode("ascii")
        bodies.append(body)
    service = SearchService(settings)
    url = args.api.rstrip("/") if args.api else None
    with httpx.Client(timeout=args.timeout) as client:
        if url:
            status = client.get(url + "/api/v1/search/status")
            status.raise_for_status()
            if status.json().get("artifact_fingerprint") != index.manifest["files"]:
                raise ValueError("HTTP server and query set use different artifacts")

        def request(number):
            position = number % len(queries)
            row = queries[position]
            started = time.perf_counter()
            try:
                if url:
                    response = client.post(url + "/api/v1/search/semantic", json=bodies[position])
                    response.raise_for_status()
                    data = response.json()
                else:
                    data = service.search(**bodies[position])
                elapsed_ms = (time.perf_counter() - started) * 1000
                ids = [item["id"] for item in data["items"]]
                expected_source = ("stored-image" if args.query_mode == "stored-vector"
                                   else "encoded-image")
                if (row["asset_id"] in ids or data.get("vector_source") != expected_source
                        or data.get("artifact_fingerprint") != index.manifest["files"]
                        or data.get("retrieval_backend") != "numpy-exact"):
                    raise ValueError("Response violates baseline/artifact/self-exclusion contract")
                return {"query_id": row["query_id"], "ok": True, "elapsed_ms": elapsed_ms,
                        "timings": data["timings"], "ranking": ids,
                        "metrics": score_ranking(ids, row["relevant_asset_ids"], args.k)}
            except Exception as exc:
                return {"query_id": row["query_id"], "ok": False,
                        "elapsed_ms": (time.perf_counter() - started) * 1000,
                        "error_type": type(exc).__name__, "error": str(exc)[:500]}

        cold = request(0)
        warmups = [request(number) for number in range(args.warmup)]
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            samples = list(executor.map(request, range(args.requests)))
    good = [row for row in samples if row["ok"]]
    unique = {row["query_id"]: row for row in good}
    by_category = defaultdict(list)
    for query in queries:
        if query["query_id"] in unique:
            category = query.get("query_category", "unspecified")
            by_category[category].append(unique[query["query_id"]])

    def average(rows):
        return {name: float(np.mean([row["metrics"][name] for row in rows]))
                for name in rows[0]["metrics"]} if rows else None

    return {
        "schema_version": 1, "created_at": datetime.now(UTC).isoformat(),
        "baseline": "siglip2-image-cosine-numpy-exact", "query_mode": args.query_mode,
        "transport": "http" if url else "in-process", "api": url,
        "model": index.manifest["model_id"], "model_revision": index.manifest["model_revision"],
        "artifact_fingerprint": index.manifest["files"],
        "indexed": len(index.assets), "searchable": index.searchable_count,
        "dimension": index.dimension, "query_set_sha256": sha256_file(args.queries),
        "query_count": len(queries), "evaluated_queries": len(unique),
        "ground_truth_sources": dict(Counter(row["ground_truth_source"] for row in queries)),
        "gallery_policy": "All searchable indexed assets, excluding the query SHA-256 ID",
        "quality_note": "Macro average over unique queries. keyword_proxy is label-based recall, "
        "not human-judged visual relevance. Exact search is the oracle; this is not ANN recall.",
        "hardware": {"platform": platform.platform(), "processor": platform.processor(),
                     "scope": "Benchmark client; record remote HTTP server hardware separately",
                     "cpu_count": os.cpu_count(), "embedding_device": settings.embedding_device,
                     "embedding_cpu_threads": settings.embedding_cpu_threads,
                     "numpy_version": np.__version__,
                     "python_version": platform.python_version(),
                     "thread_environment": {name: os.environ.get(name) for name in (
                         "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}},
        "requests": args.requests, "concurrency": args.concurrency, "k": args.k,
        "errors": len(samples) - len(good), "complete": len(good) == len(samples),
        "cold_after_status": cold, "warmup": warmups,
        "latency_ms": percentiles([row["elapsed_ms"] for row in good]),
        "all_request_latency_ms": percentiles([row["elapsed_ms"] for row in samples]),
        "stage_latency_ms": {name: percentiles([row["timings"][name] for row in good])
                             for name in good[0]["timings"]} if good else None,
        "metrics": average(list(unique.values())),
        "by_category": {key: {"queries": len(rows), "metrics": average(rows)}
                        for key, rows in sorted(by_category.items())},
        "latency_note": "perf_counter wall time; image file reads/base64 preparation excluded. "
        "Cold request and warmups excluded. HTTP includes request serialization, network and "
        "response parsing. Successful-request percentiles use numpy linear interpolation. "
        "Repeated queries are re-encoded in image mode. No image-vector response cache.",
        "samples": samples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--count", type=int, default=200)
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument("--split", choices=("train", "validation", "test", "all"), default="test")
    run = sub.add_parser("run")
    run.add_argument("--queries", type=Path, required=True)
    run.add_argument("--query-mode", choices=("stored-vector", "image"), default="stored-vector")
    run.add_argument("--api", help="Omit for in-process measurement; set for HTTP end-to-end")
    run.add_argument("--requests", type=int, default=200)
    run.add_argument("--warmup", type=int, default=3)
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--timeout", type=float, default=120)
    run.add_argument("--k", type=int, nargs="+", default=[1, 5, 10])
    for command in (prepare, run):
        command.add_argument("--artifact", type=Path)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new version to preserve baseline evidence")
    if args.command == "run" and (args.requests < 1 or args.warmup < 0 or args.concurrency < 1
                                  or not all(1 <= k <= 50 for k in args.k)):
        parser.error("Invalid requests, warmup, concurrency or k (1..50)")
    settings = Settings()
    if args.artifact:
        settings.embedding_artifact_dir = str(args.artifact)
    index = ArtifactIndex(Path(settings.embedding_artifact_dir))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.command == "prepare":
        rows = prepare_queries(index, count=args.count, seed=args.seed, split=args.split)
        args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                               encoding="utf-8")
        print(json.dumps({"queries": len(rows), "seed": args.seed, "split": args.split,
                          "categories": dict(Counter(row["query_category"] for row in rows))}))
    else:
        report = benchmark(args, settings, index)
        write_json(args.output, report)
        print(json.dumps({key: report[key] for key in (
            "query_mode", "transport", "query_count", "errors", "latency_ms", "metrics")},
            ensure_ascii=False, indent=2))
        if not report["complete"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
