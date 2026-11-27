"""SigLIP2: encodes a query (text tower) into the space of the ``img`` vectors (image tower).

Must be the same checkpoint as ``scripts/embed_images.py``; callers check the revision
against ``img_manifest.json`` before searching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

MODEL_ID = "google/siglip2-base-patch16-224"
MODEL_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
# Training-time text preprocessing (the processor also lowercases).
MAX_LENGTH = 64


@dataclass
class ImageTextEncoder:
    device: str = "cuda"

    def __post_init__(self) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        import torch
        from transformers import AutoModel, AutoProcessor

        if self.device.startswith("cuda") and not torch.cuda.is_available():
            self.device = "cpu"
        dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        model = AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION, dtype=dtype)
        self._model = model.to(self.device).eval()
        self._processor = AutoProcessor.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
        self._torch = torch

    def encode(self, texts: list[str]) -> np.ndarray:
        """L2-normalised float32 vectors, one per text."""
        torch = self._torch
        inputs = self._processor(
            text=[text.lower() for text in texts],
            padding="max_length",
            max_length=MAX_LENGTH,
            truncation=True,
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            out = self._model.get_text_features(**inputs)
            features = out.pooler_output if hasattr(out, "pooler_output") else out
        return torch.nn.functional.normalize(features.float(), dim=-1).cpu().numpy()

    def encode_images(self, images: list) -> np.ndarray:
        """L2-normalised float32 vectors for PIL images, preprocessed as scripts/embed_images.py
        does (first frame, RGB)."""
        torch = self._torch
        frames = []
        for image in images:
            image.seek(0)
            frames.append(image.convert("RGB"))
        inputs = self._processor(images=frames, return_tensors="pt")
        pixels = inputs["pixel_values"].to(self.device, self._model.dtype)
        with torch.inference_mode():
            out = self._model.get_image_features(pixel_values=pixels)
            features = out.pooler_output if hasattr(out, "pooler_output") else out
        return torch.nn.functional.normalize(features.float(), dim=-1).cpu().numpy()
