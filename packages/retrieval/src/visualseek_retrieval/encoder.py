"""Dense text encoder shared by indexing and query time (one model, one revision)."""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

MODEL_ID = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DIM = 1024
DOC_MAX_TOKENS = 512
QUERY_MAX_TOKENS = 64


@dataclass
class TextEncoder:
    device: str = "cuda"
    max_tokens: int = QUERY_MAX_TOKENS

    def __post_init__(self) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        # TensorFlow is never used, but transformers imports it when installed (~0.4 GB commit).
        os.environ.setdefault("USE_TF", "0")
        import torch
        from sentence_transformers import SentenceTransformer

        if self.device.startswith("cuda") and not torch.cuda.is_available():
            self.device = "cpu"
        self.model = SentenceTransformer(MODEL_ID, revision=MODEL_REVISION, device=self.device)
        if self.device.startswith("cuda"):
            self.model.half()
        self.model.max_seq_length = self.max_tokens

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        vectors = self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > 1000,
        )
        return vectors.astype(np.float32)
