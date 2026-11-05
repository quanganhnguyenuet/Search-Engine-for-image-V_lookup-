"""Regression: metadata chunks must keep SigLIP's unmasked padding convention."""

import threading
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest

from visualseek_embedding.metadata import metadata_text
from visualseek_embedding.siglip2 import Siglip2Encoder


class Inputs(dict):
    def to(self, device):
        return self


class Tokenizer:
    @staticmethod
    def num_special_tokens_to_add(pair=False):
        return 1

    @staticmethod
    def encode(text, add_special_tokens=False):
        return [int(word) for word in text.split()]

    @staticmethod
    def prepare_for_model(tokens, *, return_attention_mask, **kwargs):
        # Adding a padding attention mask changes SigLIP text vectors.
        assert return_attention_mask is False
        tokens = tokens + [1]
        return {"input_ids": tokens + [0] * (64 - len(tokens))}

    @staticmethod
    def pad(chunks, *, return_attention_mask, **kwargs):
        assert return_attention_mask is False
        return Inputs(input_ids=np.array([chunk["input_ids"] for chunk in chunks]))


class Features:
    def __init__(self, values):
        self.values = values

    def float(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.values


def encoder():
    instance = Siglip2Encoder.__new__(Siglip2Encoder)
    instance.processor = SimpleNamespace(tokenizer=Tokenizer())
    instance.device = "cpu"
    instance.lock = threading.Lock()
    instance.dimension = 2
    instance.torch = SimpleNamespace(inference_mode=nullcontext)

    def features(input_ids):
        return Features(np.array([[float(row.sum()), 1] for row in input_ids], dtype=np.float32))

    instance.model = SimpleNamespace(get_text_features=features)
    return instance


def test_metadata_chunking_preserves_tail_and_normalizes_every_document():
    model = encoder()
    short = model.encode_documents(["2 3"])
    expected = np.array([6, 1]) / np.linalg.norm([6, 1])
    assert short[0] == pytest.approx(expected)
    long = model.encode_documents(["2 " * 63 + "1000"], batch_size=1)
    prefix = model.encode_documents(["2 " * 63])
    assert not np.allclose(long, prefix)
    multiple = model.encode_documents(["2 3", "4 5"])
    assert np.allclose(np.linalg.norm(multiple, axis=1), 1)
    assert multiple[0] == pytest.approx(short[0])


def test_metadata_never_includes_vqa_or_unobservable_cultural_history():
    text = metadata_text({"category": "am_thuc", "keyword": "phở", "tags": ["rau"],
                          "description": "bát phở", "questions": [{"answer": "SECRET"}],
                          "cultural_context": {"historical_context": "HISTORY"}})
    assert "phở" in text and "rau" in text and "bát phở" in text
    assert "SECRET" not in text and "HISTORY" not in text
