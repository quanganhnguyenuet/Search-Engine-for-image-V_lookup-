"""Real, shared image/text encoder used by Kaggle export and online queries."""

import io
import threading
import warnings
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

MODEL_ID = "google/siglip2-base-patch16-224"
MODEL_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
PREPROCESS_VERSION = "siglip2-fixres224-exif-rgb-l2-v1"


def load_image(source: Path | bytes, *, max_pixels: int = 36_000_000) -> Image.Image:
    """Decode pixels only. Metadata and annotations never enter the image encoder."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source) if isinstance(source, bytes) else source) as image:
                if image.width * image.height > max_pixels:
                    raise ValueError(f"Image exceeds the {max_pixels // 1_000_000} megapixel limit")
                return ImageOps.exif_transpose(image).convert("RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Image exceeds the pixel limit") from exc


def unit_vectors(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise ValueError("Embeddings must be a finite two-dimensional matrix")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= 1e-12):
        raise ValueError("Zero embeddings are invalid")
    return values / norms


class Siglip2Encoder:
    def __init__(
        self,
        *,
        device: str = "auto",
        revision: str = MODEL_REVISION,
        cache_dir: str | None = None,
        cpu_threads: int = 4,
        progress: Callable[[str], None] | None = None,
    ):
        notify = progress or (lambda message: None)
        notify("[model] Importing torch and transformers")
        import torch
        from transformers import AutoModel, AutoProcessor

        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("device must be auto, cpu or cuda")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; select cpu or auto")
        self.device = "cuda" if device == "auto" and torch.cuda.is_available() else device
        if self.device == "auto":
            self.device = "cpu"
        if self.device == "cpu":
            torch.set_num_threads(cpu_threads)
        self.torch = torch
        self.revision = revision
        self.lock = threading.Lock()
        if self.device == "cuda":
            notify(f"[model] GPU: {torch.cuda.get_device_name(0)} (one GPU)")
        notify(f"[model] Loading/downloading processor: {MODEL_ID}@{revision}")
        self.processor = AutoProcessor.from_pretrained(
            MODEL_ID,
            revision=revision,
            cache_dir=cache_dir,
            trust_remote_code=False,
            use_fast=False,
        )
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        notify(f"[model] Loading/downloading weights; dtype={dtype}; cache={cache_dir}")
        self.model = AutoModel.from_pretrained(
            MODEL_ID,
            revision=revision,
            cache_dir=cache_dir,
            dtype=dtype,
            use_safetensors=True,
            trust_remote_code=False,
        )
        notify(f"[model] Moving model to {self.device}")
        self.model = self.model.eval().to(self.device)
        self.dimension = int(self.model.config.vision_config.hidden_size)

    def encode_images(self, images: list[Image.Image]) -> np.ndarray:
        with self.lock, self.torch.inference_mode():
            inputs = self.processor(images=images, return_tensors="pt").to(self.device)
            inputs["pixel_values"] = inputs["pixel_values"].to(self.model.dtype)
            vectors = self.model.get_image_features(**inputs)
            return unit_vectors(vectors.float().cpu().numpy())

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        with self.lock, self.torch.inference_mode():
            lengths = self.processor.tokenizer(texts, truncation=False)["input_ids"]
            if any(len(tokens) > 64 for tokens in lengths):
                raise ValueError("Text exceeds SigLIP2's 64-token budget; shorten the query")
            inputs = self.processor(
                text=texts,
                padding="max_length",
                truncation=True,
                max_length=64,
                return_tensors="pt",
            ).to(self.device)
            vectors = self.model.get_text_features(**inputs)
            return unit_vectors(vectors.float().cpu().numpy())

    def encode_documents(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Embed every token of metadata; mean-pool normalized 64-token chunks."""
        tokenizer = self.processor.tokenizer
        budget = 64 - tokenizer.num_special_tokens_to_add(pair=False)
        chunks, owners = [], []
        for owner, text in enumerate(texts):
            tokens = tokenizer.encode(text, add_special_tokens=False)
            for start in range(0, max(1, len(tokens)), budget):
                chunks.append(tokenizer.prepare_for_model(
                    tokens[start:start + budget], padding="max_length", max_length=64,
                    truncation=False, return_attention_mask=False,
                ))
                owners.append(owner)
        pooled = np.zeros((len(texts), self.dimension), dtype=np.float32)
        with self.lock, self.torch.inference_mode():
            for start in range(0, len(chunks), batch_size):
                inputs = tokenizer.pad(
                    chunks[start:start + batch_size], padding="max_length",
                    max_length=64, return_tensors="pt", return_attention_mask=False,
                ).to(self.device)
                vectors = self.model.get_text_features(**inputs)
                values = unit_vectors(vectors.float().cpu().numpy())
                np.add.at(pooled, owners[start:start + batch_size], values)
        return unit_vectors(pooled)
