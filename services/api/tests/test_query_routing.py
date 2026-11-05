from unittest.mock import patch

import pytest
from test_contextual import PLAN, DocumentEncoder, call_model, metadata_artifact

from visualseek_api.application.query_routing import needs_query_planning, search_term
from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError
from visualseek_retrieval.artifact import ArtifactIndex
from visualseek_retrieval.hybrid import HybridRetriever


@pytest.mark.parametrize("query", [
    "bánh chưng", "banh chung", "BÁNH CHƯNG", "phở", "Hội An", "áo dài đỏ",
    "Tôi muốn tìm ảnh bánh chưng", "Hãy tìm ảnh bánh chưng",
    "người phụ nữ mặc bộ trang phục truyền thống đứng trước một ngôi nhà cổ kính",
])
def test_simple_and_long_descriptions_do_not_require_an_llm(query):
    assert needs_query_planning(query)[0] is False


@pytest.mark.parametrize("query", [
    "áo dài không có nón", "khong co non", "bánh chưng hoặc bánh tét",
    "bánh chưng và áo dài", "hai nhóm ảnh bánh và gốm",
    "một người hướng dẫn người còn lại làm gốm", "ít nhất hai người",
    "white ao dai without a hat", "pottery or food", "adult teaching a child",
    "5 ảnh bánh chưng; 5 ảnh áo dài", "bánh chưng, áo dài", "bánh chưng\náo dài",
])
def test_negation_logic_and_multiple_requests_select_planning(query):
    assert needs_query_planning(query)[0] is True


def test_routing_is_word_based_and_strips_polite_search_prefixes():
    assert search_term("Tôi muốn tìm ảnh bánh chưng") == "banh chung"
    assert search_term("Hãy tìm ảnh bánh chưng") == "banh chung"
    assert needs_query_planning("Hoi An")[0] is False
    assert needs_query_planning("orange pottery")[0] is False  # not the word 'or'


@pytest.fixture
def routed_service(tmp_path):
    artifact = metadata_artifact(tmp_path / "artifact")
    return SearchService(Settings(embedding_artifact_dir=str(artifact), openai_api_key="",
                                  corpus_manifest_path=str(artifact / "assets.jsonl")),
                         encoder_factory=DocumentEncoder)


def test_known_entity_needs_neither_encoder_nor_llm(routed_service):
    with patch.object(routed_service, "get_encoder", side_effect=AssertionError("No encoder")), \
         patch.object(routed_service.understanding, "plan", side_effect=AssertionError("No LLM")), \
         patch.object(routed_service.understanding, "select", side_effect=AssertionError("No LLM")):
        result = routed_service.text_search(query="Tôi muốn tìm ảnh áo dài đỏ", top_k=10)
    assert result["search_route"] == "lexical"
    assert result["llm_call_count"] == 0 and result["llm_used"] is False
    assert result["query_model"] is None and result["interpretation"] is None
    assert result["timings"]["embedding_ms"] == result["timings"]["planning_ms"] == 0
    assert len(result["items"]) == 1
    assert routed_service.encoder is None


def test_exact_filter_does_not_fill_results_with_other_topics(routed_service):
    with patch.object(routed_service, "get_encoder", side_effect=AssertionError("No encoder")):
        result = routed_service.text_search(query="áo dài đỏ", category="kien_truc")
    assert result["items"] == [] and result["search_route"] == "lexical"


def test_descriptive_query_uses_local_hybrid_and_caches_vectors(routed_service):
    with patch.object(routed_service.understanding, "plan", side_effect=AssertionError("No LLM")):
        first = routed_service.text_search(query="cảnh xanh ở ngoài trời", top_k=2)
        second = routed_service.text_search(query="cảnh xanh ở ngoài trời", top_k=2)
    assert first["search_route"] == "hybrid"
    assert first["vector_cache_hit"] is False and second["vector_cache_hit"] is True
    assert first["llm_call_count"] == second["llm_call_count"] == 0
    assert routed_service.encoder.texts == ["cảnh xanh ở ngoài trời"]


def test_complex_query_uses_planner_but_false_explicitly_disables_it(routed_service):
    with patch.object(routed_service.understanding, "plan", side_effect=ApplicationError(
            "Missing key", code="openai_key_missing", status_code=503)):
        with pytest.raises(ApplicationError) as error:
            routed_service.text_search(query="áo dài không đội nón")
        result = routed_service.text_search(query="áo dài không đội nón", use_llm=False)
    assert error.value.code == "openai_key_missing"
    assert result["search_route"] == "hybrid" and result["llm_used"] is False


def test_explicit_true_forces_llm_even_for_an_exact_entity(tmp_path):
    artifact = metadata_artifact(tmp_path / "artifact")
    service = SearchService(Settings(embedding_artifact_dir=str(artifact)),
                            encoder_factory=DocumentEncoder)
    result, captured = call_model([PLAN], lambda: service.text_search(
        query="áo dài đỏ", use_llm=True, top_k=1))
    assert len(captured) == 1 and result["search_route"] == "llm_hybrid"
    assert result["llm_call_count"] == 1 and result["llm_used"] is True


def test_exact_tag_is_a_phrase_and_title_has_priority(tmp_path):
    index = ArtifactIndex(metadata_artifact(tmp_path / "artifact"))
    retriever = HybridRetriever(index)
    hits = retriever.exact_search("ao dai do", category=None, limit=10)
    assert hits[0]["asset"] == index.assets[0]
    assert retriever.exact_search("áo dài", category=None, limit=10)[0]["asset"] == index.assets[0]
    assert retriever.exact_search("dà", category=None, limit=10) is None
    assert retriever._fields is None  # Descriptions are not scanned by an exact entity lookup.
