"""S4 visual fusion: text decides the object, the image vector decides the look.

Candidates are the union of the text channels (dense + sparse) and an image ANN top-K. Every
candidate gets both scores (bge-m3 doc cosine and SigLIP2 image cosine), z-normalised over the
candidate set, then ``alpha * text + (1 - alpha) * img``. ``alpha`` depends on whether the
query names a visual attribute (colour, material, shape, pattern): without one, the image
channel only breaks ties. Tuned on eval/descriptive-v1 dev, see scripts/tune_fusion.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from visualseek_retrieval.text import surface

# General Vietnamese visual vocabulary, matched on accented words only: folded forms are
# ambiguous ("den" is đen/đèn, "do" is đỏ/đó). Multi-word entries are matched as phrases.
COLOURS = (
    "màu",
    "đen",
    "trắng",
    "đỏ",
    "xanh",
    "vàng",
    "hồng",
    "tím",
    "xám",
    "bạc",
    "nâu",
    "cam",
    "be",
    "ghi",
    "đồng",
)
MATERIALS = (
    "gỗ",
    "inox",
    "thép",
    "nhôm",
    "sắt",
    "kim loại",
    "sứ",
    "gốm",
    "thủy tinh",
    "thuỷ tinh",
    "pha lê",
    "nhựa",
    "vải",
    "len",
    "lụa",
    "nhung",
    "mây",
    "tre",
    "cói",
    "đá",
    "bằng da",
    "dây da",
    "giả da",
    "da bò",
    "da thật",
    "nhồi bông",
    "mạ",
)
SHAPES = (
    "hình",
    "tròn",
    "vuông",
    "chữ nhật",
    "tam giác",
    "họa tiết",
    "hoạ tiết",
    "hoa văn",
    "caro",
    "sọc",
    "kẻ",
    "chấm bi",
    "trong suốt",
    "nắp gập",
    "đèn led",
    "led",
    "vân",
)
# Phrases where a vocabulary word is part of an object name, not an attribute.
NOT_ATTRIBUTE = ("đồng hồ", "dưỡng da", "da mặt", "chăm sóc da", "cánh đồng", "hình ảnh")

_TERMS = sorted({surface(term) for term in COLOURS + MATERIALS + SHAPES}, key=len, reverse=True)
_ATTRIBUTE = re.compile(r"\b(" + "|".join(re.escape(term) for term in _TERMS) + r")\b")
_MASK = re.compile(r"\b(" + "|".join(re.escape(surface(t)) for t in NOT_ATTRIBUTE) + r")\b")


def has_visual_attribute(query: str) -> bool:
    text = _MASK.sub(" ", surface(query))
    return bool(_ATTRIBUTE.search(text))


def starts_with_visual_attribute(text: str) -> bool:
    """Whether the phrase is an attribute ("màu trắng", "dây da"), not an object that carries
    one ("bàn phím màu trắng", "dây chuyền vàng", "đồng hồ dây da")."""
    text = surface(text)
    return not _MASK.match(text) and bool(_ATTRIBUTE.match(text))


@dataclass
class FusionSettings:
    text_candidates: int = 200
    img_candidates: int = 100
    alpha_attribute: float = 0.3
    alpha_plain: float = 0.7


# Chosen on eval/descriptive-v1 dev with scripts/tune_fusion.py (fusion-descriptive-v1.json):
# the image channel leads for every query that reaches fusion, the text cosine breaks ties.
TUNED = FusionSettings(text_candidates=50, img_candidates=25, alpha_attribute=0.1, alpha_plain=0.1)


def asset_payloads(rows: list[dict], review, categories: dict | None) -> dict[str, dict]:
    """The Qdrant payload of every asset, from doc_texts.jsonl rows, kept in memory.

    Fusion can rank an image that no Qdrant channel returned (image-only candidates); its
    payload, with the categories and fashion tiers of its canonical entities, comes from here.
    """
    labels = (categories or {}).get("entities", {})
    out = {}
    for row in rows:
        entity_ids = row.get("entity_ids") or []
        infos = [
            labels[e]
            for e in (review.canonical(e) if review else e for e in entity_ids)
            if e in labels
        ]
        out[row["asset_id"]] = {
            "asset_id": row["asset_id"],
            "entity_ids": entity_ids,
            "brand_ids": row.get("brand_ids") or [],
            "image_path": row.get("image_path"),
            "page_title": row.get("page_title"),
            "domain": row.get("domain"),
            "categories": sorted({i["category"] for i in infos if i.get("category")}),
            "market_tiers": sorted(
                {
                    i["market_tier"]
                    for i in infos
                    if i.get("category_l1") == "fashion" and i.get("market_tier")
                }
            ),
        }
    return out


def load_visual(root: Path, asset_ids: list[str], device: str, encoder=None):
    """(SigLIP2 text encoder, VisualFusion) for an artifact dir, or (None, None) without the
    image vectors. The image vectors must have been embedded with the encoder's checkpoint and
    in the row order of doc_texts.jsonl."""
    from visualseek_retrieval.image_text import MODEL_REVISION, ImageTextEncoder

    doc_path, img_path = root / "doc_dense.npy", root / "img_siglip2.npy"
    if not (doc_path.exists() and img_path.exists()):
        return None, None
    manifest = json.loads((root / "img_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("revision") != MODEL_REVISION:
        raise RuntimeError("img_siglip2.npy was embedded with another SigLIP2 revision")
    fusion = VisualFusion(asset_ids, np.load(doc_path), np.load(img_path), TUNED)
    # A reload passes the encoder already on the GPU instead of loading a second copy.
    return encoder or ImageTextEncoder(device=device), fusion


def zscore(values: np.ndarray) -> np.ndarray:
    std = float(values.std())
    if std < 1e-9:
        return np.zeros_like(values, dtype=np.float32)
    return ((values - values.mean()) / std).astype(np.float32)


def fuse(text_scores: np.ndarray, img_scores: np.ndarray, alpha: float) -> np.ndarray:
    """Fused score per candidate; both arrays are aligned to the same candidate list."""
    return alpha * zscore(text_scores) + (1.0 - alpha) * zscore(img_scores)


class VisualFusion:
    """Scores candidates against in-memory doc and image matrices (row order = doc_texts)."""

    def __init__(
        self,
        asset_ids: list[str],
        doc_dense: np.ndarray,
        img: np.ndarray,
        settings: FusionSettings | None = None,
    ) -> None:
        if not (len(asset_ids) == len(doc_dense) == len(img)):
            raise ValueError("asset_ids, doc_dense and img must have the same row count")
        self.asset_ids = asset_ids
        self.row = {asset_id: i for i, asset_id in enumerate(asset_ids)}
        self.doc = doc_dense.astype(np.float32, copy=False)
        self.img = img.astype(np.float32, copy=False)
        self.settings = settings or FusionSettings()

    def extend(self, asset_ids: list[str], doc: np.ndarray, img: np.ndarray) -> None:
        """Add rows (images uploaded after the artifacts were built). Not thread-safe: call it
        on the thread that runs the searches."""
        if not (len(asset_ids) == len(doc) == len(img)):
            raise ValueError("asset_ids, doc and img must have the same row count")
        new = [a for a in asset_ids if a not in self.row]
        if not new:
            return
        keep = [i for i, a in enumerate(asset_ids) if a not in self.row]
        self.doc = np.vstack([self.doc, doc[keep].astype(np.float32)])
        self.img = np.vstack([self.img, img[keep].astype(np.float32)])
        self.asset_ids = [*self.asset_ids, *new]
        self.row = {asset_id: i for i, asset_id in enumerate(self.asset_ids)}

    def img_top(self, img_query: np.ndarray, k: int) -> list[str]:
        if k <= 0:
            return []
        scores = self.img @ img_query
        top = np.argpartition(-scores, min(k, len(scores) - 1))[:k]
        return [self.asset_ids[i] for i in top[np.argsort(-scores[top])]]

    def candidates(self, text_ids: list[str], img_query: np.ndarray, k: int | None = None) -> list:
        k = self.settings.img_candidates if k is None else k
        seen = dict.fromkeys(a for a in text_ids if a in self.row)
        for asset_id in self.img_top(img_query, k):
            seen.setdefault(asset_id)
        return list(seen)

    def scores(
        self, candidates: list[str], text_query: np.ndarray, img_query: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        rows = np.fromiter((self.row[a] for a in candidates), dtype=np.int64, count=len(candidates))
        return self.doc[rows] @ text_query, self.img[rows] @ img_query

    def alpha(self, query: str) -> float:
        s = self.settings
        return s.alpha_attribute if has_visual_attribute(query) else s.alpha_plain

    def rank(
        self,
        query: str,
        text_ids: list[str],
        text_query: np.ndarray,
        img_query: np.ndarray,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        candidates = self.candidates(text_ids, img_query)
        if not candidates:
            return []
        text_scores, img_scores = self.scores(candidates, text_query, img_query)
        fused = fuse(text_scores, img_scores, self.alpha(query))
        order = np.argsort(-fused, kind="stable")[:top_k]
        return [(candidates[i], float(fused[i])) for i in order]
