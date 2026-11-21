"""Portable, validated embedding artifact and an exact cosine CPU baseline."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class ArtifactIndex:
    """Exact dot-product oracle, intentionally not advertised as an ANN/HNSW index."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        manifest = self.manifest
        if manifest.get("schema_version") not in (1, 2) or manifest.get("normalization") != "l2":
            raise ValueError("Unsupported artifact schema or normalization")
        if manifest.get("preprocess_version") != "siglip2-fixres224-exif-rgb-l2-v1":
            raise ValueError("Unsupported preprocessing version")
        if not re.fullmatch(r"[a-f0-9]{40}", manifest.get("model_revision", "")):
            raise ValueError("Artifact must pin an immutable model revision")
        if manifest.get("model_id") != "google/siglip2-base-patch16-224":
            raise ValueError("Unsupported embedding model")
        for name in ("vectors.npy", "assets.jsonl"):
            if sha256_file(directory / name) != manifest["files"].get(name):
                raise ValueError(f"Artifact checksum mismatch: {name}")
        self.assets = [
            json.loads(line)
            for line in (directory / "assets.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        ids = [row["asset_id"] for row in self.assets]
        if len(ids) != len(set(ids)) or any(not re.fullmatch(r"[a-f0-9]{64}", id_) for id_ in ids):
            raise ValueError("Asset IDs must be unique SHA-256 identifiers")
        self.positions = {asset_id: i for i, asset_id in enumerate(ids)}
        self.vectors = np.load(directory / "vectors.npy", mmap_mode="r", allow_pickle=False)
        if (
            self.vectors.dtype != np.float32
            or self.vectors.shape != (len(ids), manifest["dimension"])
            or len(ids) != manifest["count"]
            or not ids
        ):
            raise ValueError("Artifact vector/metadata shape mismatch")
        if not np.isfinite(self.vectors).all() or not np.allclose(
            np.linalg.norm(self.vectors, axis=1),
            1,
            atol=2e-3,
        ):
            raise ValueError("Stored vectors must be finite and unit-normalized")
        self.searchable_count = sum(not row.get('search_excluded') for row in self.assets)
        self.counts = dict(Counter(row['category'] for row in self.assets
                                   if not row.get('search_excluded')))
        self.dimension = manifest["dimension"]
        self.searchable_mask = np.asarray(
            [not row.get('search_excluded') for row in self.assets], dtype=bool)
        self.category_masks = {
            category: np.asarray([row['category'] == category for row in self.assets], dtype=bool)
            for category in self.counts
        }
        self.metadata_vectors = None
        if manifest["schema_version"] == 2:
            if manifest.get("metadata_version") != "title-tags-description-chunk-mean-l2-v2":
                raise ValueError("Unsupported metadata preprocessing")
            name = "metadata_vectors.npy"
            if sha256_file(directory / name) != manifest["files"].get(name):
                raise ValueError("Artifact checksum mismatch: metadata_vectors.npy")
            values = np.load(directory / name, mmap_mode="r", allow_pickle=False)
            if (values.dtype != np.float32 or values.shape != self.vectors.shape
                or not np.isfinite(values).all()
                or not np.allclose(np.linalg.norm(values, axis=1), 1, atol=2e-3)):
                raise ValueError("Invalid metadata vectors")
            self.metadata_vectors = values

    def search(
        self, vector: np.ndarray, limit: int = 10, category: str | None = None,
        *, exclude_asset_ids: set[str] | None = None,
    ) -> list[dict]:
        query = np.asarray(vector, dtype=np.float32).reshape(-1)
        if query.shape != (self.dimension,) or not np.isfinite(query).all():
            raise ValueError("Invalid query vector")
        norm = np.linalg.norm(query)
        if norm <= 1e-12:
            raise ValueError("Zero query vector")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        allowed = self.searchable_mask.copy()
        if category:
            allowed &= self.category_masks.get(category, np.zeros(len(self.assets), dtype=bool))
        for asset_id in exclude_asset_ids or ():
            position = self.positions.get(asset_id)
            if position is not None:
                allowed[position] = False
        count = int(allowed.sum())
        if not count:
            return []
        # Avoid copying the full image matrix through advanced indexing per query.
        scores = self.vectors @ (query / norm)
        scores[~allowed] = -np.inf
        order = np.argsort(-scores, kind="stable")[:min(limit, count)]
        return [{"asset": self.assets[int(i)], "score": float(scores[i])} for i in order]
