import json
from unittest.mock import patch

import httpx
import numpy as np
import pytest
from pydantic import ValidationError
from test_semantic import TestEncoder, make_artifact

from visualseek_api.api.routes.search import TextSearchRequest
from visualseek_api.application.query_understanding import QueryPlan, QueryUnderstanding
from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json
from visualseek_retrieval.hybrid import HybridRetriever

PLAN = {"intent": "Tìm áo dài đỏ, không có nón", "visual_query": "red ao dai without hats",
        "metadata_query": "áo dài đỏ không có nón", "keywords": ["áo dài", "đỏ"],
        "exclusions": ["nón"]}


def metadata_artifact(path):
    directory = make_artifact(path)
    rows = [json.loads(line) for line in (directory / "assets.jsonl").read_text().splitlines()]
    rows[0].update(keyword="áo dài đỏ", tags=["áo dài", "đỏ"], description="Áo dài đỏ")
    rows[1].update(keyword="áo xanh", tags=["xanh"], description="Áo xanh")
    (directory / "assets.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    # Deliberately different image/metadata ranks: fusion must use both streams.
    np.save(directory / "metadata_vectors.npy", np.array(
        [[0, 1, 0], [1, 0, 0], [0, 0, 1]], dtype=np.float32))
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest.update(schema_version=2, metadata_version="title-tags-description-chunk-mean-l2-v2")
    manifest["files"] = {name: sha256_file(directory / name)
                         for name in ("vectors.npy", "assets.jsonl", "metadata_vectors.npy")}
    write_json(directory / "manifest.json", manifest)
    return directory


class DocumentEncoder(TestEncoder):
    def encode_documents(self, texts):
        self.texts.extend(texts)
        return np.asarray([[0, 1, 0] for _ in texts], dtype=np.float32)


def test_metadata_fusion_and_category(tmp_path):
    index = ArtifactIndex(metadata_artifact(tmp_path / "artifact"))
    hybrid = HybridRetriever(index)
    hits = hybrid.search(np.array([[0, 1, 0]]), ["ao dai do"], category=None, limit=3)
    assert hits[0]["asset"]["asset_id"] == "1" * 64
    assert hits[0]["scores"]["image_cosine"] == 0
    assert hits[0]["scores"]["metadata_cosine"] == 1
    assert hits[0]["scores"]["lexical"] > hits[1]["scores"]["lexical"]
    filtered = hybrid.search(np.array([[0, 1, 0]]), ["áo dài"], category="kien_truc", limit=3)
    assert len(filtered) == 1 and filtered[0]["asset"]["category"] == "kien_truc"
    assert hybrid.search(np.array([[0, 1, 0]]), [], category="missing", limit=3) == []


def test_metadata_tamper_rejected(tmp_path):
    directory = metadata_artifact(tmp_path / "artifact")
    with (directory / "metadata_vectors.npy").open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        ArtifactIndex(directory)


def call_model(results, callback):
    client = httpx.Client
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        assert request.url == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer unit-test-key"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(results.pop(0))}}]})

    with patch("visualseek_api.application.openai_json.httpx.Client",
               side_effect=lambda **kwargs: client(transport=httpx.MockTransport(respond))):
        result = callback()
    return result, captured


def test_contextual_pipeline_real_ids_no_vqa_and_no_implicit_category(tmp_path):
    directory = metadata_artifact(tmp_path / "artifact")
    service = SearchService(Settings(embedding_artifact_dir=str(directory),
                                    search_llm_rerank=True,
                                    corpus_manifest_path=str(directory / "assets.jsonl")),
                            encoder_factory=DocumentEncoder)
    result, captured = call_model([
        PLAN, {"suggestions": [{"candidate_id": "C1", "reason": "Chú thích có áo dài đỏ."}]}
    ], lambda: service.text_search(query="  áo dài đỏ không có nón  ", top_k=2))
    assert result["items"][0]["id"] == "1" * 64
    assert result["items"][0]["match_reason"] == "Chú thích có áo dài đỏ."
    assert result["interpretation"]["exclusions"] == ["nón"]
    assert result["coverage_complete"] is True
    assert result["query_model"] == "gpt-4o-mini"
    assert len(json.loads(captured[1]["messages"][1]["content"])["candidates"]) == 3
    assert "SECRET_VQA_LABEL" not in json.dumps(captured)
    assert service.encoder.texts[0] == "áo dài đỏ không có nón"


@pytest.mark.parametrize("suggestions", [
    [{"candidate_id": "C99", "reason": "Không có ID này"}],
    [{"candidate_id": "C1", "reason": "a"}, {"candidate_id": "C1", "reason": "b"}],
    [{"candidate_id": "C1", "reason": "a"}, {"candidate_id": "C2", "reason": "b"}],
])
def test_reject_unknown_duplicate_or_excessive_llm_ids(suggestions):
    model = QueryUnderstanding(Settings())
    hits = [{"asset": {"asset_id": str(i) * 64}} for i in (1, 2)]
    with pytest.raises(ApplicationError, match="không hợp lệ"):
        call_model([{"suggestions": suggestions}], lambda: model.select(
            "áo dài", QueryPlan(**PLAN), hits, 1))


def test_model_empty_result_is_not_filled_with_irrelevant_images():
    model = QueryUnderstanding(Settings())
    result, _ = call_model([{"suggestions": []}], lambda: model.select(
        "tên lửa ngoài vũ trụ", QueryPlan(**PLAN), [{"asset": {"asset_id": "1" * 64}}], 10))
    assert result == []


def test_invalid_plan_and_model_failure_are_explicit():
    model = QueryUnderstanding(Settings())
    with pytest.raises(ApplicationError) as error:
        call_model([{**PLAN, "category": "am_thuc"}], lambda: model.plan("áo dài"))
    assert error.value.code == "invalid_query_plan"
    with patch.object(model, "generate", side_effect=ApplicationError(
            "Unavailable", code="query_model_unavailable", status_code=503)):
        with pytest.raises(ApplicationError) as error:
            model.plan("áo dài")
    assert error.value.code == "query_model_unavailable"


def test_hybrid_without_llm_and_partial_coverage(tmp_path):
    directory = metadata_artifact(tmp_path / "artifact")
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text((directory / "assets.jsonl").read_text() + json.dumps({"asset_id": "4" * 64}))
    service = SearchService(Settings(embedding_artifact_dir=str(directory),
                                    corpus_manifest_path=str(corpus)),
                            encoder_factory=DocumentEncoder)
    with patch.object(service.understanding, "plan", side_effect=AssertionError("No LLM")):
        response = service.text_search(query="áo dài đỏ", use_llm=False, top_k=1)
    assert response["items"][0]["id"] == "1" * 64
    assert response["coverage_complete"] is False and response["corpus_total"] == 4
    assert response["query_model"] is None


def test_old_artifact_requires_enrichment_before_llm(tmp_path):
    service = SearchService(Settings(embedding_artifact_dir=str(make_artifact(tmp_path / "old"))))
    with patch.object(service.understanding, "plan", side_effect=AssertionError("No LLM")):
        with pytest.raises(ApplicationError) as error:
            service.text_search(query="áo dài")
    assert error.value.code == "metadata_index_unavailable"


def test_text_request_validation_and_unicode():
    assert TextSearchRequest(query=" a\u0301o da\u0300i ").query == "áo dài"
    for payload in ({"query": " "}, {"query": "x" * 1001},
                    {"query": "x", "category": "invalid"}, {"query": "x", "top_k": 51}):
        with pytest.raises(ValidationError):
            TextSearchRequest(**payload)


def test_planner_cached_and_rerank_optional(tmp_path):
    directory = metadata_artifact(tmp_path / "artifact")
    service = SearchService(Settings(embedding_artifact_dir=str(directory)),
                            encoder_factory=DocumentEncoder)
    with patch.object(service.understanding, "select", side_effect=AssertionError("No rerank")):
        result, captured = call_model([PLAN], lambda: [
            service.text_search(query="áo dài đỏ", top_k=1, use_llm=True),
            service.text_search(query="áo dài đỏ", top_k=1, use_llm=True),
        ])
    assert len(captured) == 1
    assert result[0]["plan_cache_hit"] is False
    assert result[1]["plan_cache_hit"] is True
    assert result[1]["llm_reranked"] is False


def test_contextual_api_contract(tmp_path):
    from fastapi.testclient import TestClient

    from visualseek_api.api.routes.search import get_search_service
    from visualseek_api.main import create_app

    directory = metadata_artifact(tmp_path / "artifact")
    service = SearchService(Settings(embedding_artifact_dir=str(directory)),
                            encoder_factory=DocumentEncoder)
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    with TestClient(app) as client:
        result = client.post("/api/v1/search/text", json={"query": "áo dài", "use_llm": False})
        assert result.status_code == 200
        assert result.json()["mode"] == "contextual"
        assert result.json()["score_type"] == "lexical_exact"
        assert result.json()["search_route"] == "lexical"
        assert result.json()["llm_call_count"] == 0
        assert result.headers["x-request-id"]
        status = client.get("/api/v1/search/status").json()
        assert status["metadata_ready"] is True
        assert client.post("/api/v1/search/warmup", json={}).json()["encoder_ready"] is True
        for body in ({"query": " "}, {"query": "x", "category": "wrong"}):
            assert client.post("/api/v1/search/text", json=body).status_code == 422


def test_reject_repetitive_plan_instead_of_polluting_retrieval():
    with pytest.raises(ApplicationError) as error:
        call_model([{**PLAN, "visual_query": "fields: " * 16}],
                   lambda: QueryUnderstanding(Settings()).plan("ruộng bậc thang"))
    assert error.value.code == "invalid_query_plan"
