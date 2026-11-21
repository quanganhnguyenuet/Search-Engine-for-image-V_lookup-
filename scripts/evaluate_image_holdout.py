"""Evaluate semantic image similarity on 100 image queries absent from the gallery."""

import argparse
import base64
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import httpx
import numpy as np

from visualseek_evaluation.image import percentiles, score_ranking
from visualseek_evaluation.image_holdout import build_holdout, load_holdout
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api/src"))
from visualseek_api.core.config import Settings  # noqa: E402


def summarize(rows):
    good = [r for r in rows if r["ok"]]
    def average(key):
        return {name: float(np.mean([r[key][name] for r in good])) for name in good[0][key]}
    return {"queries": len(rows), "errors": len(rows) - len(good),
            "target_metrics": (average("target_metrics")
                               if good and len(good) == len(rows) else None),
            "object_metrics": (average("object_metrics")
                               if good and len(good) == len(rows) else None),
            "http_latency_ms": percentiles([r["elapsed_ms"] for r in good]),
            "stage_latency_ms": {key: percentiles([r["timings"][key] for r in good
                                                   if key in r["timings"]])
                                 for key in sorted({k for r in good for k in r["timings"]})},
            "scope_stops": sum(r["route"] == "scope" for r in good),
            "clarifications": sum(r["clarification_required"] for r in good)}


def mode_reports(samples, queries, modes):
    attribute_ids = {q["query_id"] for q in queries if q["attributes"]}
    reports = {}
    for mode in modes:
        rows = [r for r in samples if r["mode"] == mode]
        reports[mode] = {**summarize(rows),
                         "attribute_targets": summarize([r for r in rows if r["query_id"]
                                                         in attribute_ids]),
                         "object_only_targets": summarize([r for r in rows if r["query_id"]
                                                           not in attribute_ids])}
    return reports


def rescore(args):
    """Change labels only; reuse measured rankings only when every request is identical."""
    index = ArtifactIndex(args.benchmark / "gallery")
    manifest, queries = load_holdout(args.benchmark, index)
    report = json.loads(args.source_report.read_text())
    source_manifest_path = args.source_benchmark / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text())
    source_queries_path = args.source_benchmark / "queries.jsonl"
    if (sha256_file(source_manifest_path) != report["benchmark_manifest_sha256"]
            or sha256_file(source_queries_path) != source_manifest["queries_sha256"]
            or source_manifest["gallery_fingerprint"] != manifest["gallery_fingerprint"]
            or source_manifest["source_fingerprint"] != manifest["source_fingerprint"]):
        raise ValueError("Source report/query/gallery checksums do not match")
    source_queries = [json.loads(line) for line in source_queries_path.read_text().splitlines()]
    def inputs(rows):
        return [(q["query_id"], q["asset_id"], q["text_query"], q["target"], q["attributes"])
                for q in rows]
    if inputs(source_queries) != inputs(queries):
        raise ValueError("Changed request inputs require a new HTTP measurement")
    by_id = {q["query_id"]: q for q in queries}
    modes = list(report["reports"])
    if (not report["complete"] or report["errors"]
            or len(report["samples"]) != len(queries) * len(modes)
            or {(r["query_id"], r["mode"]) for r in report["samples"]}
            != {(q["query_id"], mode) for q in queries for mode in modes}):
        raise ValueError("A complete paired source report is required")
    for row in report["samples"]:
        query = by_id[row["query_id"]]
        if (not row["ok"] or row["asset_id"] != query["asset_id"]
                or not set(row["ranking"]) <= set(index.positions)):
            raise ValueError("Invalid source sample or held-out query leakage")
        row["target_metrics"] = score_ranking(row["ranking"], query["relevant_asset_ids"],
                                              [1, 5, 10])
        row["object_metrics"] = score_ranking(row["ranking"],
                                              query["object_relevant_asset_ids"], [10])
    report.update({"protocol": manifest,
                   "reports": mode_reports(report["samples"], queries, modes),
                   "benchmark_manifest_sha256": sha256_file(args.benchmark / "manifest.json"),
                   "maximum_possible_macro_target_recall_at_10": float(np.mean([
                       min(10, len(q["relevant_asset_ids"])) / len(q["relevant_asset_ids"])
                       for q in queries])),
                   "label_rescore": {"created_at": datetime.now(UTC).isoformat(),
                       "source_report_sha256": sha256_file(args.source_report),
                       "source_manifest_sha256": sha256_file(source_manifest_path),
                       "identical_request_inputs_and_gallery_verified": True,
                       "note": "No inference rerun. Original HTTP rankings, measured_at "
                       "(created_at), cache flags and latencies retained; labels rescored only."}})
    return report


def run(args):
    index = ArtifactIndex(args.benchmark / "gallery")
    manifest, queries = load_holdout(args.benchmark, index)
    query_ids = {q["asset_id"] for q in queries}
    gallery_ids = set(index.positions)
    bodies = {q["query_id"]: base64.b64encode(
        (args.benchmark / q["image_path"]).read_bytes()).decode("ascii") for q in queries}
    samples = []
    base = args.api.rstrip("/")
    with httpx.Client(timeout=120, trust_env=False) as client:
        status = client.get(base + "/api/v1/search/status")
        status.raise_for_status()
        if status.json().get("artifact_fingerprint") != manifest["gallery_fingerprint"]:
            raise ValueError("API must use the separate gallery artifact, not the original corpus")
        warmup = client.post(base + "/api/v1/search/warmup", json={"include_llm": False})
        warmup.raise_for_status()
        # Warm the vision path too, without caching any query image vector.
        first = client.post(base + "/api/v1/search/semantic", json={
            "image_base64": bodies[queries[0]["query_id"]], "top_k": 10})
        first.raise_for_status()
        for number, query in enumerate(queries, 1):
            for mode in args.modes:
                payload = {"top_k": 10}
                if mode == "image":
                    payload["image_base64"] = bodies[query["query_id"]]
                else:
                    payload["query"] = query["text_query"]
                    if mode == "text-local":
                        payload["use_llm"] = False
                started = time.perf_counter()
                try:
                    response = client.post(base + "/api/v1/search/" + (
                        "text" if mode == "text-local" else "semantic"), json=payload)
                    response.raise_for_status()
                    data = response.json()
                    elapsed = (time.perf_counter() - started) * 1000
                    ids = [item["id"] for item in data["items"]]
                    if set(ids) & query_ids or not set(ids) <= gallery_ids or len(ids) > 10:
                        raise ValueError("Query leakage or invalid gallery result")
                    if data.get("artifact_fingerprint") is not None:
                        if data["artifact_fingerprint"] != manifest["gallery_fingerprint"]:
                            raise ValueError("Gallery changed during evaluation")
                    elif data.get("search_route") != "scope":
                        raise ValueError("Missing gallery fingerprint")
                    if mode == "image" and data.get("vector_source") != "encoded-image":
                        raise ValueError("Held-out query must be encoded from uploaded pixels")
                    samples.append({"query_id": query["query_id"], "asset_id": query["asset_id"],
                        "target": query["target"], "attributes": query["attributes"],
                        "category_stratum": query["category_stratum"], "mode": mode, "ok": True,
                        "elapsed_ms": elapsed, "timings": data["timings"], "ranking": ids,
                        "vector_cache_hit": data.get("vector_cache_hit"),
                        "route": data.get("search_route", "semantic"),
                        "backend": data.get("retrieval_backend"),
                        "clarification_required": data.get("clarification_required", False),
                        "target_metrics": score_ranking(
                            ids, query["relevant_asset_ids"], [1, 5, 10]),
                        "object_metrics": score_ranking(
                            ids, query["object_relevant_asset_ids"], [10])})
                except Exception as exc:
                    samples.append({"query_id": query["query_id"], "mode": mode, "ok": False,
                                    "error": type(exc).__name__ + ": " + str(exc)[:500]})
            if number % 20 == 0 or number == len(queries):
                print(f"Evaluated {number}/{len(queries)}; errors="
                      f"{sum(not r['ok'] for r in samples)}", flush=True)
    reports = mode_reports(samples, queries, args.modes)
    return {"created_at": datetime.now(UTC).isoformat(), "protocol": manifest,
            "complete": all(r["ok"] for r in samples), "errors": sum(not r["ok"] for r in samples),
            "requests": len(samples), "query_gallery_overlap": len(query_ids & gallery_ids),
            "transport": "HTTP loopback", "concurrency": 1, "top_k": 10,
            "query_payload_for_image": ["image_base64", "top_k"],
            "label_note": "Target-specific silver recall, not human visual gold. For attribute "
            "targets, positives require the named object+attribute. Object recall is separate. "
            "Paired text states the target explicitly and local search uses the label-source tags; "
            "this diagnostic is annotation-aligned, not independent semantic model validation.",
            "maximum_possible_macro_target_recall_at_10": float(np.mean([
                min(10, len(q["relevant_asset_ids"])) / len(q["relevant_asset_ids"])
                for q in queries])),
            "benchmark_manifest_sha256": sha256_file(args.benchmark / "manifest.json"),
            "cpu_threads": Settings().embedding_cpu_threads,
            "thread_environment": {name: os.environ.get(name) for name in (
                "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")},
            "reports": reports, "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--source-artifact", type=Path)
    prepare.add_argument("--image-root", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--count", type=int, default=100)
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument("--review", type=Path, help="Versioned visual-review query overrides")
    check = sub.add_parser("validate")
    check.add_argument("--benchmark", type=Path, required=True)
    evaluate = sub.add_parser("run")
    evaluate.add_argument("--benchmark", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--api", default="http://127.0.0.1:8000")
    evaluate.add_argument("--modes", nargs="+", choices=("image", "text-semantic", "text-local"),
                          default=["image", "text-semantic", "text-local"])
    revise = sub.add_parser("rescore", help="Rescore unchanged HTTP rankings with revised labels")
    revise.add_argument("--benchmark", type=Path, required=True)
    revise.add_argument("--source-benchmark", type=Path, required=True)
    revise.add_argument("--source-report", type=Path, required=True)
    revise.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        settings = Settings()
        manifest = build_holdout(
            ArtifactIndex(args.source_artifact or Path(settings.embedding_artifact_dir)),
            args.image_root or Path(settings.search_image_root), args.output,
            count=args.count, seed=args.seed,
            review=json.loads(args.review.read_text()) if args.review else None)
        print(json.dumps({key: manifest[key] for key in (
            "query_count", "unique_targets", "unique_base_objects", "attribute_query_count",
            "gallery_count", "query_gallery_overlap", "query_categories")}, indent=2))
    elif args.command == "validate":
        manifest, rows = load_holdout(args.benchmark, ArtifactIndex(args.benchmark / "gallery"))
        print(json.dumps({"validated_queries": len(rows), "query_gallery_overlap": 0,
                          "gallery_count": manifest["gallery_count"]}, indent=2))
    else:
        if args.output.exists():
            parser.error("Output exists; choose a new version")
        report = rescore(args) if args.command == "rescore" else run(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, report)
        print(json.dumps({mode: {key: value[key] for key in (
            "target_metrics", "object_metrics", "http_latency_ms")}
            for mode, value in report["reports"].items()}, ensure_ascii=False, indent=2))
        if not report["complete"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
