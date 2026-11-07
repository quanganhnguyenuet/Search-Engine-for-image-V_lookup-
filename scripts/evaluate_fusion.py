"""Collect local ablations, import human labels, tune fusion and calibrate rejection."""

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import _bootstrap  # noqa: F401

from visualseek_embedding.siglip2 import Siglip2Encoder
from visualseek_evaluation.fusion import (
    calibrate_gate,
    compare_modes,
    fused_ranking,
    policy_ranking,
    tune_fusion,
)
from visualseek_retrieval.artifact import ArtifactIndex, write_json
from visualseek_retrieval.hybrid import FusionConfig, HybridRetriever
from visualseek_retrieval.relevance import RelevancePolicy, context_signature, pipeline_context

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api/src"))
from visualseek_api.core.config import Settings  # noqa: E402


def collect(args) -> dict:
    settings = Settings()
    index = ArtifactIndex(Path(settings.embedding_artifact_dir))
    depth = settings.search_candidate_depth
    config = FusionConfig(
        image_weight=settings.search_image_weight,
        metadata_weight=settings.search_metadata_weight,
        lexical_weight=settings.search_lexical_weight,
        rank_constant=settings.search_rrf_constant,
        image_depth=depth,
        metadata_depth=depth,
        lexical_depth=depth,
    )
    context = pipeline_context(index, config, settings.search_query_roles)
    signature = context_signature(context)
    query_rows = [
        json.loads(line)
        for line in args.queries.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        query_rows = query_rows[: args.limit]
    if not query_rows or len({q["query_id"] for q in query_rows}) != len(query_rows):
        raise ValueError("Queries must have unique query IDs and not be empty")
    if args.output.exists():
        raise ValueError("Output already exists; choose a new pool version to preserve labels")
    encoder = Siglip2Encoder(
        device=settings.embedding_device,
        cache_dir=settings.embedding_cache_dir,
        revision=index.manifest["model_revision"],
        progress=print,
    )
    retriever = HybridRetriever(index, config)
    retriever.prepare()
    selector = None
    if args.with_rerank:
        from visualseek_api.application.query_understanding import QueryUnderstanding

        selector = QueryUnderstanding(settings)
    modes = {
        "image_only": replace(config, image_weight=1, metadata_weight=0, lexical_weight=0),
        "metadata_only": replace(config, image_weight=0, metadata_weight=1, lexical_weight=0),
        "lexical_only": replace(config, image_weight=0, metadata_weight=0, lexical_weight=1),
        "rrf": replace(config, image_weight=1, metadata_weight=1, lexical_weight=1),
        "weighted": config,
    }
    pool = {
        "schema_version": 1,
        "query_profile": "local",
        "pipeline_context": context,
        "pipeline_signature": signature,
        "fusion_config": config.as_dict(),
        "query_set_sha256": hashlib.sha256(args.queries.read_bytes()).hexdigest(),
        "with_rerank": args.with_rerank,
        "samples": [],
        "collection_note": "Controlled local ablations, exact-title router bypassed; no planner. "
        "All candidate grades and answerability require human review.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    positions = {asset["asset_id"]: position for position, asset in enumerate(index.assets)}
    for query in query_rows:
        vectors = encoder.encode_documents([query["query"]])
        scores = retriever.channel_scores(vectors, [query["query"]])
        # The full branch union is retained, so weighted replay cannot invent unjudged candidates.
        candidates = retriever.rank(
            scores, category=query.get("category"), limit=depth * 3, config=modes["rrf"]
        )
        evidence = {
            hit["asset"]["asset_id"]: {
                "scores": hit["scores"],
                "ranks": hit["ranks"],
                "metadata_complete": hit["metadata_complete"],
                "source_position": positions[hit["asset"]["asset_id"]],
            }
            for hit in candidates
        }
        rankings = {
            name: [
                hit["asset"]["asset_id"]
                for hit in retriever.rank(
                    scores, category=query.get("category"), limit=depth * 3, config=mode_config
                )
            ]
            for name, mode_config in modes.items()
        }
        if selector:
            shortlist = retriever.rank(
                scores, category=query.get("category"), limit=settings.search_rerank_candidates
            )
            rankings["reranked"] = [
                hit["asset"]["asset_id"]
                for hit in selector.select(
                    query["query"], None, shortlist, min(50, settings.search_rerank_candidates)
                )
            ]
        pool["samples"].append(
            {
                **query,
                "answerable": None,
                "pipeline_signature": signature,
                "judgments": {id_: None for id_ in sorted(evidence)},
                "channel_evidence": evidence,
                "rankings": rankings,
                # IDs, not ranking positions, order the blind label candidates.
                "candidates": sorted(
                    [
                        {
                            "id": hit["asset"]["asset_id"],
                            "title": hit["asset"].get("keyword", ""),
                            "description": hit["asset"].get("description", ""),
                            "image_path": hit["asset"]["image_path"],
                            "imageUrl": f"/api/culture/images/{hit['asset']['asset_id']}",
                        }
                        for hit in candidates
                    ],
                    key=lambda row: row["id"],
                ),
            }
        )
        if len(pool["samples"]) % 10 == 0:
            write_json(args.output, pool)
        print(
            f"Collected {len(pool['samples'])}/{len(query_rows)}: {query['query_id']}", flush=True
        )
    write_json(args.output, pool)
    return pool


def export_labels(pool: dict, output: Path, split: str = "all") -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "query_id",
                "query",
                "answerable",
                "asset_id",
                "grade",
                "image_path",
                "title",
                "description",
            ],
        )
        writer.writeheader()
        for sample in pool["samples"]:
            if split != "all" and sample["split"] != split:
                continue
            for row in sample["candidates"]:
                writer.writerow(
                    {
                        "query_id": sample["query_id"],
                        "query": sample["query"],
                        "answerable": "",
                        "asset_id": row["id"],
                        "grade": "",
                        "image_path": row["image_path"],
                        "title": row["title"],
                        "description": row["description"],
                    }
                )
            if not sample["candidates"]:
                writer.writerow(
                    {
                        "query_id": sample["query_id"],
                        "query": sample["query"],
                        "answerable": "",
                        "asset_id": "",
                        "grade": "",
                    }
                )


def import_labels(pool: dict, source: Path, reviewer: str, split: str = "all") -> dict:
    if not reviewer.strip():
        raise ValueError("A human reviewer name is required")
    samples = {sample["query_id"]: sample for sample in pool["samples"]}
    seen = {}
    answerability = {}
    with source.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["query_id"] not in samples:
                raise ValueError("Label CSV contains an unknown query ID")
            sample = samples[row["query_id"]]
            if split != "all" and sample["split"] != split:
                continue
            if row["answerable"].strip():
                value = row["answerable"].strip().lower()
                if value not in ("true", "false"):
                    raise ValueError("answerable must be true or false")
                if (
                    sample["query_id"] in answerability
                    and answerability[sample["query_id"]] != value
                ):
                    raise ValueError("Conflicting answerability labels")
                answerability[sample["query_id"]] = value
            if not row["asset_id"]:
                continue
            id_ = row["asset_id"]
            if id_ not in sample["judgments"] or row["grade"] not in ("0", "1", "2"):
                raise ValueError("Every candidate needs a human grade 0, 1 or 2")
            key = (sample["query_id"], id_)
            if key in seen:
                raise ValueError("Duplicate query/asset label row")
            seen[key] = int(row["grade"])
    for sample in pool["samples"]:
        if split != "all" and sample["split"] != split:
            continue
        if sample["query_id"] not in answerability:
            raise ValueError("Every query needs confirmed answerability")
        sample["answerable"] = answerability[sample["query_id"]] == "true"
        for id_ in sample["judgments"]:
            key = (sample["query_id"], id_)
            if key not in seen:
                raise ValueError("Label CSV is incomplete")
            sample["judgments"][id_] = seen[key]
    pool["label_provenance"] = {
        "reviewer": reviewer,
        "csv_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "split": split,
    }
    pool.setdefault("label_history", []).append(pool["label_provenance"])
    return pool


def replay_config(pool: dict, config_file: Path) -> dict:
    document = json.loads(config_file.read_text())
    config = FusionConfig(**document.get("best", {}).get("config", document))
    if any(
        getattr(config, channel + "_depth") > pool["fusion_config"][channel + "_depth"]
        for channel in ("image", "metadata", "lexical")
    ):
        raise ValueError("A deeper candidate pool requires recollection and new labels")
    pool["pipeline_context"]["fusion"] = config.as_dict()
    pool["fusion_config"] = config.as_dict()
    pool["pipeline_signature"] = context_signature(pool["pipeline_context"])
    for sample in pool["samples"]:
        sample["pipeline_signature"] = pool["pipeline_signature"]
        sample["rankings"]["weighted"] = fused_ranking(sample, config)
    return pool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gather = sub.add_parser("collect")
    gather.add_argument("--queries", type=Path, default=Path("data/evaluation/queries-v1.jsonl"))
    gather.add_argument("--output", type=Path, required=True)
    gather.add_argument("--limit", type=int, default=0)
    gather.add_argument(
        "--with-rerank",
        action="store_true",
        help="Send candidate annotations/images to the configured LLM",
    )
    for command in ("export-labels", "import-labels", "score", "tune", "calibrate"):
        child = sub.add_parser(command)
        child.add_argument("--pool", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
        if command in ("score", "calibrate"):
            child.add_argument("--fusion-config", type=Path)
        if command in ("score", "tune", "calibrate"):
            child.add_argument("--k", type=int, default=10)
        if command == "score":
            child.add_argument("--split", choices=("development", "held-out"), default="held-out")
            child.add_argument("--policy", type=Path)
        if command == "import-labels":
            child.add_argument("--labels", type=Path, required=True)
            child.add_argument("--reviewer", required=True)
        if command in ("export-labels", "import-labels"):
            child.add_argument("--split", choices=("development", "held-out", "all"), default="all")
        if command == "calibrate":
            child.add_argument("--max-outside-false-accept-rate", type=float, default=0.05)
            child.add_argument("--min-precision", type=float, default=0.8)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("Output already exists; use a new versioned output path")
        if args.command == "collect":
            if args.limit < 0:
                raise ValueError("limit must be nonnegative")
            collect(args)
            return
        pool = json.loads(args.pool.read_text(encoding="utf-8"))
        if getattr(args, "k", 1) < 1:
            raise ValueError("k must be positive")
        if getattr(args, "fusion_config", None):
            pool = replay_config(pool, args.fusion_config)
        if args.command == "export-labels":
            export_labels(pool, args.output, args.split)
            return
        if args.command == "import-labels":
            result = import_labels(pool, args.labels, args.reviewer, args.split)
        elif args.command == "score":
            if args.policy:
                policy = RelevancePolicy.read(args.policy, pool["pipeline_signature"])
                if pool["query_profile"] != policy.query_profile:
                    raise ValueError("Policy and pool query profiles differ")
                for sample in pool["samples"]:
                    sample["rankings"]["weighted_gate"] = policy_ranking(sample, policy, "weighted")
            result = {
                "split": args.split,
                "pipeline_signature": pool["pipeline_signature"],
                "reports": compare_modes(pool, args.split, k=args.k),
            }
        elif args.command == "tune":
            result = tune_fusion(pool, k=args.k)
        else:
            if "label_provenance" not in pool or pool.get("query_profile") != "local":
                raise ValueError(
                    "Calibration requires imported human labels for the local query profile"
                )
            result = calibrate_gate(
                pool,
                k=args.k,
                max_outside_false_accept_rate=args.max_outside_false_accept_rate,
                min_precision=args.min_precision,
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(args.output)


if __name__ == "__main__":
    main()
