"""Build a 500-query silver ground truth and evaluate text-to-image over HTTP."""

import argparse
import json
import os
import platform
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import httpx
import numpy as np

from visualseek_evaluation.image import percentiles, score_ranking
from visualseek_evaluation.text_ground_truth import (
    concept_evidence,
    load_ground_truth,
    save_ground_truth,
)
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json
from visualseek_retrieval.text import surface

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api/src"))
from visualseek_api.core.config import Settings  # noqa: E402


def validate(index, directory):
    manifest, queries, intents = load_ground_truth(index, directory)
    evidence = concept_evidence(index.assets, manifest["catalog"])
    for intent in intents.values():
        expected = set.intersection(*(set(evidence[surface(name)])
                                      for name in intent["required_concepts"]))
        if expected != set(intent["relevant_asset_ids"]):
            raise ValueError(f"Positive rule mismatch: {intent['intent_id']}")
    return {"validated_queries": len(queries), "validated_intents": len(intents),
            "positive_rule_mismatches": 0, "human_reviewed": False,
            "label_source": manifest["label_source"]}


def summarize_latency(rows):
    good = [r for r in rows if r["ok"]]
    return {"requests": len(rows), "errors": len(rows) - len(good),
            "http_latency_ms": percentiles([r["elapsed_ms"] for r in good]),
            "stage_latency_ms": {key: percentiles([r["timings"][key] for r in good])
                                 for key in good[0]["timings"]} if good else None}


def summarize_quality(rows):
    good = [r for r in rows if r["ok"]]
    return {"queries": len(rows), "evaluated_queries": len(good),
            "metrics": {key: float(np.mean([r["metrics"][key] for r in good]))
                        for key in good[0]["metrics"]} if good and len(good) == len(rows) else None,
            "zero_hit_queries": sum(r["metrics"]["HitRate@10"] == 0 for r in good),
            "scope_stop_queries": sum(r["route"] == "scope" for r in good),
            "clarification_queries": sum(r["clarification_required"] for r in good)}


def vector_baseline(args, index):
    """Quality diagnostic only: no scope router, metadata, lexical channel or HTTP timing."""
    from visualseek_embedding.siglip2 import Siglip2Encoder

    settings = Settings()
    manifest, queries, intents = load_ground_truth(index, args.ground_truth)
    encoder = Siglip2Encoder(
        device=settings.embedding_device, revision=index.manifest["model_revision"],
        cache_dir=settings.embedding_cache_dir, cpu_threads=settings.embedding_cpu_threads,
        progress=print)
    vectors = []
    for start in range(0, len(queries), 8):
        vectors.append(encoder.encode_texts([q["query"] for q in queries[start:start + 8]]))
        if start % 80 == 0:
            print(f"Encoded {min(start + 8, len(queries))}/{len(queries)}", flush=True)
    vectors = np.concatenate(vectors)
    positions = np.asarray([i for i, row in enumerate(index.assets)
                            if not row.get("search_excluded")], dtype=np.int64)
    scores = index.vectors[positions] @ vectors.T
    rows = []
    for column, query in enumerate(queries):
        order = np.argsort(-scores[:, column], kind="stable")[:10]
        ranking = [index.assets[int(positions[i])]["asset_id"] for i in order]
        rows.append({**query, "ok": True, "route": "vector_only",
                     "clarification_required": False, "ranking": ranking,
                     "metrics": score_ranking(ranking, intents[query["intent_id"]][
                         "relevant_asset_ids"], [1, 5, 10])})
    return {"schema_version": 1, "task": "text_to_image", "mode": "vector_only",
            "created_at": datetime.now(UTC).isoformat(),
            "ground_truth_manifest_sha256": sha256_file(args.ground_truth / "manifest.json"),
            "artifact_fingerprint": index.manifest["files"],
            "model": index.manifest["model_id"], "model_revision": index.manifest["model_revision"],
            "ground_truth_source": "annotation_silver", "top_k": 10,
            "note": "Offline embedding quality diagnostic, not API behavior or latency. "
            "SigLIP2 encode_texts -> exact cosine over image vectors. No scope routing, "
            "lexical, metadata or LLM. Scores use the same frozen 500-query silver labels.",
            **summarize_quality(rows),
            "held_out": summarize_quality([r for r in rows if r["split"] == "held-out"]),
            "by_kind": {key: summarize_quality([r for r in rows if r["kind"] == key])
                        for key in sorted({r["kind"] for r in rows})},
            "samples": rows}


def evaluate(args, index):
    manifest, queries, intents = load_ground_truth(index, args.ground_truth)
    if args.split != "all":
        queries = [q for q in queries if q["split"] == args.split]
    if not queries:
        raise ValueError("Selected split has no queries")
    samples = []
    base = args.api.rstrip("/")
    with httpx.Client(timeout=120, trust_env=False) as client:
        status_response = client.get(base + "/api/v1/search/status")
        status_response.raise_for_status()
        status = status_response.json()
        if status.get("artifact_fingerprint") != manifest["artifact_fingerprint"]:
            raise ValueError("HTTP server uses a different artifact than ground truth")
        if "local" in args.modes and status.get("llm_enabled"):
            # use_llm=false on every request still guarantees this local benchmark.
            print("Local evaluation explicitly overrides use_llm=false", flush=True)
        warmup = client.post(base + "/api/v1/search/warmup", json={"include_llm": False})
        warmup.raise_for_status()

        def request(query, mode, phase):
            started = time.perf_counter()
            try:
                response = client.post(base + "/api/v1/search/" + (
                    "semantic" if mode == "semantic" else "text"), json={
                        "query": query["query"], "top_k": 10,
                        **({"use_llm": False} if mode == "local" else {})})
                response.raise_for_status()
                data = response.json()
                elapsed_ms = (time.perf_counter() - started) * 1000
                if data.get("artifact_fingerprint") is not None:
                    if data["artifact_fingerprint"] != manifest["artifact_fingerprint"]:
                        raise ValueError("Artifact changed during evaluation")
                elif data.get("search_route") != "scope":
                    raise ValueError("Search response is missing artifact fingerprint")
                if data.get("llm_call_count", 0):
                    raise ValueError("LLM calls are forbidden in local baseline")
                ranking = [item["id"] for item in data["items"]]
                if any(id_ not in index.positions for id_ in ranking) or len(ranking) > 10:
                    raise ValueError("Invalid result IDs/count")
                positive = intents[query["intent_id"]]["relevant_asset_ids"]
                return {**query, "mode": mode, "phase": phase, "ok": True,
                        "elapsed_ms": elapsed_ms, "timings": data["timings"],
                        "vector_cache_hit": data["vector_cache_hit"],
                        "route": data.get("search_route", "semantic"),
                        "routing_reason": data.get("routing_reason"),
                        "backend": data.get("retrieval_backend"),
                        "clarification_required": data.get("clarification_required", False),
                        "scope_rejected": data.get("scope_rejected", False),
                        "ranking": ranking, "positive_count": len(positive),
                        "metrics": score_ranking(ranking, positive, [1, 5, 10])}
            except Exception as exc:
                return {**query, "mode": mode, "phase": phase, "ok": False,
                        "elapsed_ms": (time.perf_counter() - started) * 1000,
                        "error_type": type(exc).__name__, "error": str(exc)[:500]}

        for number, query in enumerate(queries, 1):
            for mode in args.modes:
                samples.append(request(query, mode, "first_seen"))
                if not args.no_cache_probe:
                    samples.append(request(query, mode, "immediate_repeat"))
            if number % 50 == 0 or number == len(queries):
                print(f"Evaluated {number}/{len(queries)} queries; "
                      f"errors={sum(not r['ok'] for r in samples)}", flush=True)
    reports = {}
    for mode in args.modes:
        rows = [r for r in samples if r["mode"] == mode and r["phase"] == "first_seen"]
        by_slice = {}
        for field in ("split", "kind", "category_stratum"):
            by_slice[field] = {key: summarize_quality([r for r in rows if r[field] == key])
                               for key in sorted({r[field] for r in rows})}
        latency_groups = defaultdict(list)
        for row in samples:
            if row["mode"] != mode or not row["ok"]:
                continue
            route = row["route"]
            cache = ("cache_hit" if row["vector_cache_hit"] else "cache_miss")
            key = f"{route}:{cache}" if route in ("hybrid", "semantic") else route
            latency_groups[key].append(row)
        reports[mode] = {**summarize_quality(rows), "slices": by_slice,
                         "routes": dict(Counter(r["route"] for r in rows if r["ok"])),
                         "latency_by_route_cache": {key: summarize_latency(values)
                                                    for key, values in latency_groups.items()}}
    positives = [len(intents[q["intent_id"]]["relevant_asset_ids"]) for q in queries]
    return {
        "schema_version": 1, "task": "text_to_image", "created_at": datetime.now(UTC).isoformat(),
        "ground_truth_directory": str(args.ground_truth),
        "ground_truth_manifest_sha256": sha256_file(args.ground_truth / "manifest.json"),
        "ground_truth": manifest, "split": args.split, "query_count": len(queries),
        "independent_intents": len({q["intent_id"] for q in queries}),
        "metric_definition": "Macro over unique queries: Recall@K=hits/all silver positives; "
        "HitRate@K=at least one positive; Precision@K=hits/K. Repeat probes excluded from quality.",
        "maximum_possible_macro_recall_at_10": float(np.mean(
            [min(10, n) / n for n in positives])),
        "positive_counts": {"min": min(positives), "median": float(np.median(positives)),
                            "mean": float(np.mean(positives)), "max": max(positives)},
        "transport": "HTTP", "api": base, "concurrency": 1, "top_k": 10,
        "llm_enabled_for_evaluation": False, "cache_probe": not args.no_cache_probe,
        "cache_note": "Per-process RAM LRU, 128 entries for semantic and contextual separately. "
        "Use actual cache flags. Cache stores embeddings, not full responses; queries from "
        "earlier modes are encoded independently. Restart and eviction discard entries.",
        "latency_note": "HTTP wall time includes serialization/network/server/JSON parsing. "
        "Model/index/retriever warmup excluded. Each query is immediately repeated to probe "
        "cache; API scope and clarification responses are kept and scored as empty rankings.",
        "hardware": {"platform": platform.platform(), "processor": platform.processor(),
                     "cpu_count": os.cpu_count(), "scope": "Benchmark client",
                     "thread_environment": {name: os.environ.get(name) for name in (
                         "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}},
        "errors": sum(not r["ok"] for r in samples), "requests": len(samples),
        "complete": all(r["ok"] for r in samples), "reports": reports, "samples": samples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--count", type=int, default=500)
    prepare.add_argument("--seed", type=int, default=42)
    check = sub.add_parser("validate")
    check.add_argument("--ground-truth", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--ground-truth", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--api", default="http://127.0.0.1:8000")
    run.add_argument("--modes", nargs="+", choices=("semantic", "local"),
                     default=["semantic", "local"])
    run.add_argument("--split", choices=("all", "development", "held-out"), default="all")
    run.add_argument("--no-cache-probe", action="store_true")
    vector = sub.add_parser("vector-baseline")
    vector.add_argument("--ground-truth", type=Path, required=True)
    vector.add_argument("--output", type=Path, required=True)
    for command in (prepare, check, run, vector):
        command.add_argument("--artifact", type=Path)
    args = parser.parse_args()
    index = ArtifactIndex(args.artifact or Path(Settings().embedding_artifact_dir))
    if args.command == "prepare":
        manifest = save_ground_truth(index, args.output, count=args.count, seed=args.seed)
        print(json.dumps({key: manifest[key] for key in (
            "query_count", "intent_count", "query_splits", "query_strata", "query_kinds")},
            ensure_ascii=False, indent=2))
    elif args.command == "validate":
        print(json.dumps(validate(index, args.ground_truth), indent=2))
    elif args.command == "vector-baseline":
        if args.output.exists():
            parser.error("Output exists; use a new version")
        report = vector_baseline(args, index)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, report)
        print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    else:
        if args.output.exists():
            parser.error("Output exists; use a new version to preserve evidence")
        report = evaluate(args, index)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, report)
        print(json.dumps({"errors": report["errors"], "reports": {
            mode: {key: value[key] for key in ("queries", "metrics", "routes")}
            for mode, value in report["reports"].items()}}, ensure_ascii=False, indent=2))
        if not report["complete"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
