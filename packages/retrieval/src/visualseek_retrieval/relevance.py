"""Version-bound, human-calibrated relevance gates; raw scores are not probabilities."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from visualseek_retrieval.artifact import ArtifactIndex
from visualseek_retrieval.hybrid import FusionConfig


def pipeline_context(index: ArtifactIndex, config: FusionConfig, query_roles: bool) -> dict:
    return {
        "version": "image-metadata-fusion-v5-corpus-grounding",
        "model_id": index.manifest["model_id"],
        "model_revision": index.manifest["model_revision"],
        "files": index.manifest["files"],
        "fusion": config.as_dict(),
        "query_roles": query_roles,
    }


def context_signature(context: dict) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def pipeline_signature(index: ArtifactIndex, config: FusionConfig, query_roles: bool) -> str:
    return context_signature(pipeline_context(index, config, query_roles))


@dataclass(frozen=True)
class RelevancePolicy:
    pipeline_signature: str
    min_image_cosine: float
    min_metadata_cosine: float
    query_profile: str = "local"

    @classmethod
    def read(cls, path: Path, signature: str):
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            value.get("schema_version") != 1
            or value.get("kind") != "human-calibrated-cosine-gate"
            or value.get("pipeline_signature") != signature
            or not re.fullmatch(r"[a-f0-9]{64}", value.get("development_pool_sha256", ""))
        ):
            raise ValueError("Relevance policy is invalid or belongs to another search pipeline")
        image, metadata = value["min_image_cosine"], value["min_metadata_cosine"]
        if any(
            type(v) not in (int, float) or not np.isfinite(v) or not -1 <= v <= 1
            for v in (image, metadata)
        ):
            raise ValueError("Invalid calibrated relevance thresholds")
        if value.get("query_profile") not in ("local", "planned"):
            raise ValueError("Invalid relevance query profile")
        return cls(signature, float(image), float(metadata), value["query_profile"])

    def accepts(self, hit: dict) -> bool:
        scores = hit["scores"]
        image, metadata = scores.get("image_cosine"), scores.get("metadata_cosine")
        if any(v is None or not np.isfinite(v) for v in (image, metadata)):
            return False
        # Missing description is unknown evidence, not proof of irrelevance.
        return bool(
            image >= self.min_image_cosine
            and (not hit.get("metadata_complete", True) or metadata >= self.min_metadata_cosine)
        )


def apply_relevance(
    hits: list[dict], policy: RelevancePolicy | None, mode: str
) -> tuple[list[dict], dict]:
    accepted = [hit for hit in hits if policy is None or policy.accepts(hit)]
    info = {
        "mode": mode,
        "status": "off"
        if mode == "off"
        else ("calibrated" if policy is not None else "calibration_required"),
        "candidates": len(hits),
        "would_reject": len(hits) - len(accepted) if policy is not None else None,
        "enforced": mode == "enforce" and policy is not None,
    }
    return (accepted if info["enforced"] else hits), info
