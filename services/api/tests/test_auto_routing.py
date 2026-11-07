import json
from pathlib import Path
from unittest.mock import patch

import pytest
from test_contextual import PLAN, DocumentEncoder, call_model, metadata_artifact

from visualseek_api.application.query_routing import IntentRouter
from visualseek_api.application.query_understanding import QueryPlan
from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings


@pytest.fixture
def service(tmp_path):
    artifact = metadata_artifact(tmp_path / 'artifact')
    return SearchService(Settings(embedding_artifact_dir=str(artifact), search_llm_enabled=True,
        search_routing_mode='auto', search_llm_rerank=True, search_rerank_scope='all'),
        encoder_factory=DocumentEncoder)


@pytest.mark.parametrize('query', [
    'người mặc áo dài đỏ', 'ruộng bậc thang xanh ở vùng núi', 'phố cổ Hội An vào ban đêm',
    'nghệ nhân làm gốm', 'trụ đá trong kiến trúc Việt Nam',
    'lăng tẩm của những vị vua Việt Nam', 'một khu vườn có không gian xanh',
    'ruộng bậc thang, mây trắng',
])
def test_direct_descriptions_use_image_metadata_and_lexical_without_llm(service, query):
    with patch.object(service.understanding, 'plan', side_effect=AssertionError('No planner')), \
         patch.object(service.understanding, 'select', side_effect=AssertionError('No rerank')):
        result = service.text_search(query=query)
    assert result['search_route'] == 'hybrid' and result['llm_call_count'] == 0
    assert result['routing_reason'] == 'direct_visual_request'
    assert not result['llm_used'] and not result['llm_reranked']
    assert result['items'] and result['interpretation'] is None
    assert service.encoder.texts == [query]
    assert {'image_cosine', 'metadata_cosine', 'lexical'} <= result['items'][0]['scores'].keys()


def test_exact_name_skips_encoder_classifier_and_llm(service):
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No encoder')), \
         patch('visualseek_api.application.search.IntentRouter',
               side_effect=AssertionError('No classifier')), \
         patch.object(service.understanding, 'plan', side_effect=AssertionError('No planner')):
        result = service.text_search(query='áo dài đỏ')
    assert result['search_route'] == 'lexical' and result['llm_call_count'] == 0
    assert result['timings']['embedding_ms'] == 0 and result['interpretation'] is None


def test_corpus_ambiguity_asks_locally_in_auto_mode(service):
    rows = service.get_index().assets
    rows[0].update(keyword='Phở', tags=['phở'], category='am_thuc')
    rows[1].update(keyword='Phố', tags=['phố'], category='kien_truc')
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No encoder')), \
         patch.object(service.understanding, 'plan', side_effect=AssertionError('No LLM')):
        result = service.text_search(query='pho')
    assert result['clarification_required'] and result['items'] == []
    assert set(result['suggested_queries']) == {'phở', 'phố'}
    assert result['llm_call_count'] == 0


@pytest.mark.parametrize('query', [
    'các lăng ở Việt Nam trừ lăng chủ tịch nước',
    'mấy cái lăng ở Huế bỏ cái lăng của Bác ra nhé',
    'nơi yên nghỉ của các vua triều Nguyễn',
    'người lớn chỉ cho trẻ nhỏ cách nặn một chiếc bình',
    'Tìm ruộng bậc thang, không có người trong ảnh',
    'áo dài đỏ hoặc xanh trong cùng một ảnh có hai người ưu tiên toàn cảnh',
])
def test_reasoning_requests_keep_the_semantic_planner_and_selector(service, query):
    plan = QueryPlan(**PLAN)
    with patch.object(service.understanding, 'plan', return_value=plan) as planner, \
         patch.object(service.understanding, 'select', side_effect=lambda q, p, h, k: h[:k]):
        result = service.text_search(query=query)
    planner.assert_called_once()
    assert result['search_route'] == 'llm_hybrid' and result['llm_reranked']
    assert result['llm_call_count'] == 2 and result['interpretation']['intent'] == plan.intent


def test_user_can_force_planning_for_a_direct_request(service):
    response, calls = call_model([PLAN, {'suggestions': []}], lambda: service.text_search(
        query='áo dài đỏ', use_llm=True))
    assert len(calls) == 2 and response['routing_reason'] == 'llm_forced'


def test_unknown_intent_escalates_instead_of_guessing_local():
    decision = IntentRouter().classify('zqxvjk')
    assert decision.needs_reasoning and decision.reason == 'uncertain_intent'


def test_router_on_separate_draft_test_queries():
    path = Path(__file__).parents[1] / 'src/visualseek_api/application/query_router_training.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    held_out = [row for row in rows if row['split'] == 'held-out']
    assert len(held_out) == 20
    router = IntentRouter()
    assert all(router.classify(row['query']).needs_reasoning == row['needs_reasoning']
               for row in held_out)
