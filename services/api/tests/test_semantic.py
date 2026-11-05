import base64
import io
import json
import struct
import zlib
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visualseek_api.api.routes.search import get_search_service
from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError
from visualseek_api.main import create_app
from visualseek_embedding.siglip2 import MODEL_ID, MODEL_REVISION, PREPROCESS_VERSION, unit_vectors
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json


def make_artifact(directory: Path) -> Path:
    directory.mkdir()
    vectors = np.asarray([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
    np.save(directory / "vectors.npy", vectors)
    rows = [
        {
            "asset_id": str(i + 1) * 64,
            "image_path": f"images/am_thuc/test/{i}.jpg",
            "category": "am_thuc" if i < 2 else "kien_truc",
            "keyword": f"item {i}",
            "description": f"Evidence {i}",
            "questions": [{"answer": "SECRET_VQA_LABEL"}],
        }
        for i in range(3)
    ]
    (directory / "assets.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    write_json(
        directory / "manifest.json",
        {
            "schema_version": 1,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "preprocess_version": PREPROCESS_VERSION,
            "dimension": 3,
            "normalization": "l2",
            "count": 3,
            "complete": True,
            "files": {
                name: sha256_file(directory / name) for name in ("vectors.npy", "assets.jsonl")
            },
        },
    )
    return directory


class TestEncoder:
    """A test double for orchestration tests, never used by production code."""

    __test__ = False
    dimension = 3
    device = "cpu"

    def __init__(self, **kwargs):
        self.texts = []

    def encode_texts(self, texts):
        self.texts.extend(texts)
        return np.asarray([[0, 1, 0] for _ in texts], dtype=np.float32)

    def encode_images(self, images):
        return np.asarray([[0, 0, 1] for _ in images], dtype=np.float32)


@pytest.fixture
def service(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    return SearchService(
        Settings(embedding_artifact_dir=str(artifact)), encoder_factory=TestEncoder
    )


def test_exact_ranking_and_category_filter(service):
    index = service.get_index()
    assert index.search(np.array([0, 1, 0]), 1)[0]["asset"]["asset_id"] == "2" * 64
    assert index.search(np.array([0, 1, 0]), 10, "kien_truc")[0]["asset"]["asset_id"] == "3" * 64
    assert index.search(np.array([0, 1, 0]), 10, "nonexistent") == []


def test_normalization_and_invalid_query_vectors(service):
    assert np.allclose(unit_vectors(np.array([[3, 4]])), [[0.6, 0.8]])
    for query in (np.zeros(3), np.array([np.nan, 0, 1]), np.ones(4)):
        with pytest.raises(ValueError):
            service.get_index().search(query)
    with pytest.raises(ValueError):
        unit_vectors(np.zeros((1, 3)))


def test_tampered_artifact_is_rejected(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    with (artifact / "assets.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="checksum"):
        ArtifactIndex(artifact)


def test_missing_index_is_actionable_and_does_not_load_model(tmp_path):
    service = SearchService(
        Settings(embedding_artifact_dir=str(tmp_path / "missing")),
        encoder_factory=lambda **kwargs: pytest.fail("Model should not load"),
    )
    with pytest.raises(ApplicationError) as error:
        service.search(query="áo dài")
    assert error.value.code == "index_unavailable"


def test_text_query_preserves_accents_and_caches_encoder(service):
    first = service.search(query="Áo dài Việt Nam", top_k=1)
    service.search(query="Áo dài Việt Nam", top_k=1)
    assert first["items"][0]["id"] == "2" * 64
    assert service.encoder.texts == ["Áo dài Việt Nam"]
    assert first["result_scope"] == "top_k"
    assert first["libraryTotal"] == 3


def test_image_query_uses_visual_encoder(service):
    data = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(data, format="PNG")
    result = service.search(image_base64=base64.b64encode(data.getvalue()).decode())
    assert result["items"][0]["id"] == "3" * 64
    with pytest.raises(ApplicationError) as error:
        service.search(image_base64="invalid base64")
    assert error.value.code == "invalid_image"
    # A tiny PNG with a forged huge IHDR must be rejected before pixel allocation.
    oversized = bytearray(data.getvalue())
    oversized[16:24] = struct.pack(">II", 100_000, 100_000)
    oversized[29:33] = struct.pack(">I", zlib.crc32(oversized[12:29]))
    with pytest.raises(ApplicationError) as error:
        service.search(image_base64=base64.b64encode(oversized).decode())
    assert error.value.code == "invalid_image"


def generation(answer):
    return httpx.Response(200, json={"message": {"content": json.dumps(answer)}})


def test_rag_grounding_and_citations(service):
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return generation(
            {
                "answer": "Theo mô tả, ảnh có ruộng lúa xanh và cảnh quan tự nhiên [S1].",
                "citations": ["S1"],
                "abstained": False,
            }
        )

    original_client = httpx.Client
    with patch(
        "visualseek_api.application.search.httpx.Client",
        side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond)),
    ):
        result = service.rag("Câu hỏi", None)
    assert result["citations"][0]["asset_id"] == "2" * 64
    assert result["generator_model"] == "qwen2.5:3b"
    assert "SECRET_VQA_LABEL" not in json.dumps(captured)
    assert result["evidence_type"] == "retrieved-image-annotations"


@pytest.mark.parametrize(
    "answer",
    [
        {"answer": "[S1]", "citations": ["S1"], "abstained": False},
        {"answer": "Bịa nguồn [S99]", "citations": ["S99"], "abstained": False},
        {"answer": "Thiếu dẫn nguồn", "citations": [], "abstained": False},
        {"answer": "Nguồn không khớp [S2]", "citations": ["S1"], "abstained": False},
        {"answer": "Kiểu sai", "citations": "S1", "abstained": False},
    ],
)
def test_rag_rejects_invalid_citations(service, answer):
    original_client = httpx.Client
    with patch(
        "visualseek_api.application.search.httpx.Client",
        side_effect=lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda request: generation(answer))
        ),
    ):
        with pytest.raises(ApplicationError) as error:
            service.rag("Câu hỏi", None)
    assert error.value.code == "invalid_generation"


def test_rag_abstains_without_evidence_and_without_generator(service):
    service.settings.rag_min_score = 1.1
    with patch("visualseek_api.application.search.httpx.Client") as client:
        result = service.rag("Câu hỏi", None)
    client.assert_not_called()
    assert result["abstained"] is True
    assert result["citations"] == []


def test_generator_unavailable_is_not_fake_answer(service):
    original_client = httpx.Client
    with patch(
        "visualseek_api.application.search.httpx.Client",
        side_effect=lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(503))
        ),
    ):
        with pytest.raises(ApplicationError) as error:
            service.rag("Câu hỏi", None)
    assert error.value.code == "generator_unavailable"


def test_api_validation_and_response(service):
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    with TestClient(app) as client:
        assert client.get("/api/v1/search/status").json()["indexed"] == 3
        assert client.post("/api/v1/search/semantic", json={"query": "áo dài"}).status_code == 200
        for body in (
            {},
            {"query": " "},
            {"query": "áo dài", "image_base64": "abc"},
            {"query": "áo dài", "category": "invalid"},
            {"query": "a", "top_k": 101},
        ):
            assert client.post("/api/v1/search/semantic", json=body).status_code == 422
        assert client.post("/api/v1/search/rag", json={"query": " "}).status_code == 422
