"""Scope rejection and contextual clarification must precede nearest-neighbour search."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from test_contextual import PLAN, DocumentEncoder, call_model, metadata_artifact

from visualseek_api.api.routes.search import get_search_service
from visualseek_api.application.query_understanding import QueryUnderstanding
from visualseek_api.application.search import SearchService
from visualseek_api.application.search_scope import assess_scope
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError
from visualseek_api.main import create_app


@pytest.fixture
def service(tmp_path):
    artifact = metadata_artifact(tmp_path / 'artifact')
    return SearchService(Settings(embedding_artifact_dir=str(artifact),
        search_llm_enabled=True, search_routing_mode='auto', search_llm_rerank=True),
        encoder_factory=DocumentEncoder)


@pytest.mark.parametrize('query', [
    'Tàu vũ trụ trên sao Hỏa', 'tau vu tru tren sao Hoa',
    'astronaut walking on Mars', 'Tháp Eiffel ở Paris', 'điện thoại iPhone',
    'người mặc áo dài trên sao Hỏa', 'viết code python',
])
@pytest.mark.parametrize('use_llm', [None, False, True])
def test_clear_outside_scope_never_reaches_encoder_planner_or_retriever(service, query, use_llm):
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No encoder')), \
         patch.object(service.understanding, 'plan', side_effect=AssertionError('No planner')), \
         patch.object(service.get_retriever(), 'search', side_effect=AssertionError('No search')):
        result = service.text_search(query=query, use_llm=use_llm, category='phong_canh')
    assert result['items'] == [] and result['scope_rejected'] and result['abstained']
    assert result['abstention_reason'] == 'out_of_scope'
    assert result['search_scope']['status'] == 'out_of_scope'
    assert result['llm_call_count'] == result['timings']['embedding_ms'] == 0
    assert not result['clarification_required'] and result['suggested_queries']


def test_local_scope_is_enforced_without_llm_and_without_relevance_policy(service):
    service.settings.search_llm_enabled = False
    service.settings.search_relevance_mode = 'off'
    result = service.text_search(query='tàu vũ trụ trên sao Hỏa')
    assert result['scope_rejected'] and result['items'] == []


def test_unknown_subject_asks_instead_of_returning_arbitrary_top_k(service):
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No embedding')):
        result = service.text_search(query='zqxvjk', use_llm=False)
    assert result['clarification_required'] and not result['scope_rejected']
    assert result['search_scope']['status'] == 'unclear' and result['items'] == []
    assert result['clarification_context'][0]['query'] == 'zqxvjk'


def test_negated_outside_subject_is_not_rejected_by_an_isolated_keyword(service):
    assert assess_scope('áo dài, không có tàu vũ trụ').status == 'unclear'
    safe = {**PLAN, 'scope': 'in_scope', 'exclusions': ['tàu vũ trụ']}
    response, calls = call_model([safe, {'suggestions': []}], lambda: service.text_search(
        query='áo dài, không có tàu vũ trụ'))
    assert not response['scope_rejected'] and len(calls) == 2


def outside_plan():
    return {**PLAN, 'scope': 'out_of_scope', 'scope_reason': 'Địa danh ngoài Việt Nam.',
            'visual_query': '', 'metadata_query': '', 'keywords': [], 'exclusions': [],
            'suggested_queries': ['Phố cổ Hội An']}


def test_planner_can_reject_novel_outside_subject_before_embedding(service):
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No embedding')):
        result, calls = call_model([outside_plan()], lambda: service.text_search(
            query='Angkor Wat'))
    assert len(calls) == 1 and result['scope_rejected'] and result['items'] == []
    assert result['search_scope']['source'] == 'llm_plan'
    assert not result['llm_reranked'] and result['suggested_queries'] == ['Phố cổ Hội An']


def test_semantic_and_rag_text_cannot_bypass_scope(service):
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No embedding')), \
         patch.object(service.generator, 'generate', side_effect=AssertionError('No generator')):
        for result in (service.search(query='tàu vũ trụ trên sao Hỏa'),
                       service.rag('tàu vũ trụ trên sao Hỏa', None)):
            assert result['scope_rejected'] and result['items'] == []


def test_llm_clarification_roundtrip_preserves_original_constraints_and_cache(service):
    original = 'Tìm pho vào ban đêm, không có người trong ảnh'
    unclear = {**PLAN, 'scope': 'unclear', 'scope_reason': 'Chưa rõ phở hay phố.',
               'visual_query': '', 'metadata_query': '',
               'clarification_question': 'Bạn muốn món phở hay phố cổ?',
               'suggested_queries': ['Phở vào ban đêm', 'Phố cổ ban đêm không có người']}
    clarified = {**PLAN, 'intent': 'Phố cổ vào ban đêm không có người',
                 'visual_query': 'empty ancient Vietnamese street at night',
                 'metadata_query': 'phố cổ ban đêm', 'keywords': ['phố cổ', 'ban đêm'],
                 'exclusions': ['người'],
                 'requirements': [{'id': 'R1', 'text': 'Phố cổ vào ban đêm'},
                                  {'id': 'R2', 'text': 'Không có người trong ảnh'}]}

    def run():
        first = service.text_search(query=original, use_llm=True)
        assert first['clarification_required'] and service.encoder is None
        second = service.text_search(query='phố cổ',
                                     clarification_context=first['clarification_context'])
        return first, second

    (first, second), calls = call_model([unclear, clarified, {'suggestions': []}], run)
    payload = json.loads(calls[1]['messages'][1]['content'])
    assert payload['request'] == 'phố cổ'
    assert payload['clarification_context'] == [{
        'query': original, 'question': first['clarification_question']}]
    assert second['interpretation']['requirements'] == clarified['requirements']
    plan, hit = service.query_plan('phố cổ', None, first['clarification_context'])
    assert hit and plan.intent == clarified['intent']
    different = [{'query': 'pho ban ngày', 'question': first['clarification_question']}]
    with patch.object(service.understanding, 'plan', return_value=plan) as planner:
        _, hit = service.query_plan('phố cổ', None, different)
    assert not hit and planner.call_count == 1


def test_followup_without_llm_never_embeds_short_answer_as_new_request(service):
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No embedding')):
        response = service.text_search(query='đỏ', use_llm=False,
            clarification_context=[{'query': 'áo dài hay áo bà ba?', 'question': 'Bạn chọn gì?'}])
    assert response['clarification_required'] and response['items'] == []


@pytest.mark.parametrize('changes', [
    {'scope': 'elsewhere'},
    {'scope': 'unclear', 'clarification_question': None},
    {'scope': 'out_of_scope', 'scope_reason': ''},
])
def test_invalid_scope_plans_are_rejected(changes):
    with pytest.raises(ApplicationError) as error:
        QueryUnderstanding._parse_plan({**PLAN, **changes})
    assert error.value.code == 'invalid_query_plan'


def test_missing_scope_cannot_default_to_permission_to_search():
    with pytest.raises(ApplicationError):
        QueryUnderstanding._parse_plan({k: v for k, v in PLAN.items() if k != 'scope'})


def test_api_scope_and_history_validation(service):
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    with TestClient(app) as client:
        response = client.post('/api/v1/search/text', json={'query': 'tàu vũ trụ trên sao Hỏa'})
        assert response.status_code == 200 and response.json()['scope_rejected']
        assert response.headers['x-request-id']
        for history in ([{'query': '', 'question': 'Ý bạn là gì?'}],
                        [{'query': 'pho', 'question': 'Phở hay phố?'}] * 5,
                        [{'query': 'pho', 'question': 'Phở hay phố?', 'extra': 'unexpected'}]):
            assert client.post('/api/v1/search/text', json={
                'query': 'phố', 'clarification_context': history}).status_code == 422


def test_conversation_limit_asks_for_complete_request_without_losing_old_constraints(service):
    history = [{'query': f'Yêu cầu {i}', 'question': 'Bạn muốn gì?'} for i in range(4)]
    result = service.text_search(query='cái đó', use_llm=False, clarification_context=history)
    assert result['clarification_context'] == []
    assert 'mô tả đầy đủ' in result['clarification_question']
