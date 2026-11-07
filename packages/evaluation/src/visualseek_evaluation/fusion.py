"""Evaluate saved retrieval channels and calibrate abstention using development labels only."""

import hashlib
import json
from dataclasses import replace

import numpy as np

from visualseek_evaluation.relevance import evaluate_pool
from visualseek_retrieval.hybrid import FusionConfig
from visualseek_retrieval.relevance import RelevancePolicy


def fused_ranking(sample: dict, config: FusionConfig) -> list[str]:
    scores = {}
    for id_, candidate in sample["channel_evidence"].items():
        total = 0.0
        for channel in ("image", "metadata", "lexical"):
            rank = candidate["ranks"].get(channel)
            if rank is not None and rank <= getattr(config, channel + "_depth"):
                total += getattr(config, channel + "_weight") / (config.rank_constant + rank)
        if total > 0:
            scores[id_] = total
    # source_position preserves production's stable tie ordering for this artifact.
    return sorted(
        scores, key=lambda id_: (-scores[id_], sample["channel_evidence"][id_]["source_position"])
    )


def compare_modes(pool: dict, split: str, k: int = 10) -> list[dict]:
    samples = [sample for sample in pool["samples"] if sample["split"] == split]
    if not samples:
        raise ValueError("No samples in selected split")
    modes = sorted(set.intersection(*(set(sample["rankings"]) for sample in samples)))
    if not modes:
        raise ValueError("Pool has no common evaluation modes")
    return [evaluate_pool(samples, mode, k=k) for mode in modes]


def tune_fusion(pool: dict, *, k: int = 10) -> dict:
    samples = [sample for sample in pool["samples"] if sample["split"] == "development"]
    if not samples:
        raise ValueError("Development labels required for fusion tuning")
    config = FusionConfig(**pool["fusion_config"])
    trials = []
    for image in (0.5, 1.0, 2.0):
        for metadata in (0.5, 1.0, 2.0):
            for lexical in (0.5, 1.0, 2.0):
                candidate = replace(
                    config, image_weight=image, metadata_weight=metadata, lexical_weight=lexical
                )
                ranked = [
                    {**sample, "rankings": {"trial": fused_ranking(sample, candidate)}}
                    for sample in samples
                ]
                metrics = evaluate_pool(ranked, "trial", k=k)
                all_metrics = metrics["metrics"].get("all")
                if all_metrics is None:
                    raise ValueError("Answerable development queries required")
                trials.append({"config": candidate.as_dict(), "metrics": all_metrics})
    trials.sort(
        key=lambda trial: (-trial["metrics"][f"nDCG@{k}"], -trial["metrics"][f"HitRate@{k}"])
    )
    return {
        "fit_split": "development",
        "best": trials[0],
        "trials": trials,
        "note": "Select on development only; score the chosen config once on held-out labels",
    }


def policy_ranking(sample: dict, policy: RelevancePolicy, mode: str) -> list[str]:
    return [
        id_ for id_ in sample["rankings"][mode] if policy.accepts(sample["channel_evidence"][id_])
    ]


def calibrate_gate(
    pool: dict,
    *,
    mode: str = "weighted",
    k: int = 10,
    max_outside_false_accept_rate: float = 0.05,
    min_precision: float = 0.8,
) -> dict:
    """Choose two cosine thresholds; never manufacture labels or touch held-out data."""
    if not 0 <= max_outside_false_accept_rate <= 1 or not 0 <= min_precision <= 1:
        raise ValueError("Calibration rates must lie between 0 and 1")
    samples = [sample for sample in pool["samples"] if sample["split"] == "development"]
    if not samples:
        raise ValueError("Development labels required to calibrate relevance")
    # Enforce the same human-label validation as evaluation before fitting anything.
    evaluate_pool(samples, mode, k=k)
    if not any(sample["answerable"] for sample in samples) or not any(
        not sample["answerable"] for sample in samples
    ):
        raise ValueError(
            "Calibration requires both answerable and unanswerable development queries"
        )
    evidence = [
        sample["channel_evidence"][id_] for sample in samples for id_ in sample["rankings"][mode]
    ]
    if not evidence:
        raise ValueError("No candidates available for calibration")
    signatures = {sample["pipeline_signature"] for sample in samples}
    if len(signatures) != 1 or mode != "weighted":
        raise ValueError("Gate calibration requires one configured weighted pipeline")
    signature = signatures.pop()
    thresholds = {}
    for channel in ("image", "metadata"):
        values = [hit["scores"][channel + "_cosine"] for hit in evidence]
        if not np.isfinite(values).all():
            raise ValueError("Invalid calibration evidence")
        thresholds[channel] = np.unique(
            np.clip(np.r_[-1.0, np.quantile(values, np.linspace(0, 1, 17)), 1.0], -1, 1)
        )
    best = None
    for image in thresholds["image"]:
        for metadata in thresholds["metadata"]:
            policy = RelevancePolicy(signature, float(image), float(metadata), "local")
            kept = 0
            correct = 0
            hits = 0
            outside_accepted = 0
            for sample in samples:
                accepted = policy_ranking(sample, policy, mode)[:k]
                kept += len(accepted)
                correct += sum(sample["judgments"][id_] == 2 for id_ in accepted)
                hits += int(
                    sample["answerable"] and any(sample["judgments"][id_] == 2 for id_ in accepted)
                )
                outside_accepted += int(not sample["answerable"] and bool(accepted))
            outside = sum(not sample["answerable"] for sample in samples)
            precision = correct / kept if kept else 0.0
            false_accept = outside_accepted / outside
            if (
                precision < min_precision
                or false_accept > max_outside_false_accept_rate
                or hits == 0
            ):
                continue
            quality = (hits, precision, -false_accept, -float(image), -float(metadata))
            if best is None or quality > best[0]:
                best = quality, policy, precision, false_accept
    if best is None:
        raise ValueError(
            "No useful gate meets development targets; improve retrieval or collect labels"
        )
    _, policy, precision, false_accept = best
    # Hash only development records. Held-out labels cannot influence the policy.
    digest = hashlib.sha256(json.dumps(samples, sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": 1,
        "kind": "human-calibrated-cosine-gate",
        "pipeline_signature": signature,
        "query_profile": "local",
        "min_image_cosine": policy.min_image_cosine,
        "min_metadata_cosine": policy.min_metadata_cosine,
        "development_pool_sha256": digest,
        "calibration": {
            "queries": len(samples),
            "fit_split": "development",
            "candidate_precision": precision,
            "outside_false_accept_rate": false_accept,
            "k": k,
            "min_precision_target": min_precision,
            "outside_false_accept_target": max_outside_false_accept_rate,
        },
        "note": "Development-calibrated policy; evaluate once on held-out before enforcement",
    }
