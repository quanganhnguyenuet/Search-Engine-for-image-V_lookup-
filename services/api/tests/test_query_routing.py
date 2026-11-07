from unittest.mock import patch

import pytest
from test_contextual import PLAN, DocumentEncoder, call_model, metadata_artifact

from visualseek_api.application.query_routing import search_term
from visualseek_api.application.query_understanding import QueryPlan
from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError
from visualseek_retrieval.artifact import ArtifactIndex
from visualseek_retrieval.hybrid import HybridRetriever


@pytest.mark.parametrize("query", [
    "bánh chưng", "banh chung", "BÁNH CHƯNG", "phở", "Hội An", "áo dài đỏ",
    "Tôi muốn tìm ảnh bánh chưng", "Hãy tìm ảnh bánh chưng",
    "người phụ nữ mặc bộ trang phục truyền thống đứng trước một ngôi nhà cổ kính",
    "áo dài không có nón", "khong co non", "bánh chưng hoặc bánh tét",
    "bánh chưng và áo dài", "hai nhóm ảnh bánh và gốm",
    "một người hướng dẫn người còn lại làm gốm", "ít nhất hai người",
    "white ao dai without a hat", "pottery or food", "adult teaching a child",
    "5 ảnh bánh chưng; 5 ảnh áo dài", "bánh chưng, áo dài", "bánh chưng\náo dài",
    "các lăng ở Việt Nam trừ lăng chủ tịch nướcC", "cac lang tru lang Ho Chi Minh",
    "nơi yên nghỉ của các vua triều Nguyễn", "lăng tẩm của những vị vua Việt Nam",
    "trụ đá trong kiến trúc Việt Nam", "áo dài đỏ thôi nha", "bỏ cái lăng của Bác ra nhé",
])
def test_always_mode_plans_every_query_without_keyword_gates(routed_service, query):
    interpretation = QueryPlan(**PLAN)
    with patch.object(routed_service.understanding, 'plan', return_value=interpretation) as plan, \
         patch.object(routed_service.get_retriever(), 'exact_search',
                      side_effect=AssertionError('No shortcut before understanding')):
        result = routed_service.text_search(query=query)
    assert plan.call_count == 1 and plan.call_args.args == (query,)
    assert plan.call_args.kwargs['category'] is None
    assert result['search_route'] == 'llm_hybrid'
    assert result['routing_reason'] == 'llm_semantic_planning'
    assert result['llm_call_count'] == 1


def test_local_lookup_strips_polite_search_prefixes():
    assert search_term("Tôi muốn tìm ảnh bánh chưng") == "banh chung"
    assert search_term("Hãy tìm ảnh bánh chưng") == "banh chung"


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
        result = routed_service.text_search(query="Tôi muốn tìm ảnh áo dài đỏ", top_k=10,
                                           use_llm=False)
    assert result["search_route"] == "lexical"
    assert result["llm_call_count"] == 0 and result["llm_used"] is False
    assert result["query_model"] is None and result["interpretation"] is None
    assert result["timings"]["embedding_ms"] == result["timings"]["planning_ms"] == 0
    assert len(result["items"]) == 1
    assert routed_service.encoder is None


def test_exact_filter_does_not_fill_results_with_other_topics(routed_service):
    with patch.object(routed_service, "get_encoder", side_effect=AssertionError("No encoder")):
        result = routed_service.text_search(query="áo dài đỏ", category="kien_truc", use_llm=False)
    assert result["items"] == [] and result["search_route"] == "lexical"


def test_descriptive_query_uses_local_hybrid_and_caches_vectors(routed_service):
    with patch.object(routed_service.understanding, "plan", side_effect=AssertionError("No LLM")):
        first = routed_service.text_search(query="cảnh xanh ở ngoài trời", top_k=2, use_llm=False)
        second = routed_service.text_search(query="cảnh xanh ở ngoài trời", top_k=2, use_llm=False)
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


def test_local_default_never_calls_planner_or_reranker(tmp_path):
    directory = metadata_artifact(tmp_path / 'local-only')
    service = SearchService(Settings(embedding_artifact_dir=str(directory),
        search_llm_enabled=False, search_llm_rerank=True, search_rerank_scope='all',
        openai_api_key=''), encoder_factory=DocumentEncoder)
    with patch.object(service.understanding, 'plan', side_effect=AssertionError('No LLM')), \
         patch.object(service.understanding, 'select', side_effect=AssertionError('No reranker')):
        for query in ('lăng tẩm của những vị vua Việt Nam', 'áo dài đỏ, không đội nón'):
            response = service.text_search(query=query)
            assert response['search_route'] == 'hybrid'
            assert response['llm_call_count'] == 0 and not response['llm_used']
            assert not response['llm_reranked']


def test_disabled_llm_cannot_be_enabled_by_a_request(routed_service):
    routed_service.settings.search_llm_enabled = False
    with patch.object(routed_service.understanding, 'plan', side_effect=AssertionError('No LLM')):
        with pytest.raises(ApplicationError) as error:
            routed_service.text_search(query='ảnh', use_llm=True)
        assert error.value.code == 'llm_disabled'
        with pytest.raises(ApplicationError):
            routed_service.rag('ảnh', None)


@pytest.mark.parametrize('rerank', [False, True])
def test_mausoleum_exclusion_is_applied_before_top_k_and_reranking(routed_service, rerank):
    rows = routed_service.get_index().assets
    for row, title in zip(rows, ['Lăng Hồ Chí Minh', 'Lăng Tự Đức', 'Lăng Khải Định'], strict=True):
        row.update(keyword=title, tags=[title], description=title)
    routed_service.settings.search_llm_rerank = rerank
    plan = QueryPlan(intent='Lăng vua, trừ Lăng Hồ Chí Minh',
                     visual_query='Vietnamese imperial mausoleums',
                     metadata_query='lăng vua Việt Nam', keywords=['lăng'],
                     exclusions=['Lăng Hồ Chí Minh'])
    seen = []

    def select(query, plan, hits, limit):
        seen.extend(hit['asset']['keyword'] for hit in hits)
        return hits[:limit]

    with patch.object(routed_service.understanding, 'plan', return_value=plan) as planner, \
         patch.object(routed_service.understanding, 'select', side_effect=select):
        result = routed_service.text_search(
            query='các lăng ở Việt Nam trừ lăng chủ tịch nướcC', top_k=2)
    planner.assert_called_once()
    assert result['search_route'] == 'llm_hybrid'
    assert {item['title'] for item in result['items']} == {'Lăng Tự Đức', 'Lăng Khải Định'}
    assert result['llm_call_count'] == (2 if rerank else 1)
    if rerank:
        assert set(seen) == {'Lăng Tự Đức', 'Lăng Khải Định'}


def test_exclusions_preserve_accents_and_do_not_match_substrings(routed_service):
    index = routed_service.get_index()
    for row, title in zip(index.assets, ['Bánh chay', 'Bánh chày', 'Bánh chay'], strict=True):
        row.update(keyword=title, tags=[])
    retriever = routed_service.get_retriever()
    assert retriever.entities.excluded_positions(('bánh chay',)) == frozenset({0, 2})
    assert retriever.entities.excluded_positions(('hay',)) == frozenset()
    vectors, _ = routed_service.document_vectors(['bánh'])
    assert retriever.search(vectors, ['bánh'], category=None, limit=3,
                            exclusions=['bánh chay', 'bánh chày']) == []
