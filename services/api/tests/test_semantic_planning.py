"""Plan consumption, ambiguity and cache isolation; no live provider calls."""

import json
from unittest.mock import patch

import pytest
from test_contextual import PLAN, DocumentEncoder, call_model, metadata_artifact

from visualseek_api.application.query_understanding import QueryPlan, QueryUnderstanding
from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError


@pytest.fixture
def service(tmp_path):
    directory = metadata_artifact(tmp_path / 'artifact')
    return SearchService(Settings(embedding_artifact_dir=str(directory),
                                  search_llm_enabled=True, search_llm_rerank=True),
                         encoder_factory=DocumentEncoder)


def test_unclear_request_asks_question_before_encoding_or_retrieving(service):
    interpretation = {**PLAN, 'intent': 'Cần làm rõ từ pho',
        'clarification_question': 'Bạn muốn ảnh món phở hay phố cổ?',
        'suggested_queries': ['Tìm món phở Việt Nam', 'Tìm phố cổ Việt Nam']}
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No embedding')), \
         patch.object(service.get_retriever(), 'search',
                      side_effect=AssertionError('No retrieval')):
        response, captured = call_model([interpretation], lambda: service.text_search(query='pho'))
    assert len(captured) == response['llm_call_count'] == 1
    assert response['clarification_required'] and response['items'] == []
    assert response['clarification_question'] == interpretation['clarification_question']
    assert response['suggested_queries'] == interpretation['suggested_queries']
    assert response['abstention_reason'] == 'llm_clarification'
    assert not response['llm_reranked']


def test_constraints_reach_selector_and_empty_selection_is_respected(service):
    interpretation = {**PLAN,
        'requirements': [{'id': 'R1', 'text': 'Mặc áo dài đỏ hoặc xanh'},
                         {'id': 'R2', 'text': 'Hai người trong cùng một ảnh'}],
        'preferences': [{'id': 'P1', 'text': 'Ưu tiên toàn cảnh'}]}
    response, captured = call_model([interpretation, {'suggestions': []}],
        lambda: service.text_search(query='hai người mặc áo dài đỏ hoặc xanh ưu tiên toàn cảnh'))
    selection = json.loads(captured[1]['messages'][1]['content'])
    assert selection['interpretation']['requirements'] == interpretation['requirements']
    assert selection['interpretation']['preferences'] == interpretation['preferences']
    assert response['interpretation']['requirements'] == interpretation['requirements']
    assert response['interpretation']['preferences'] == interpretation['preferences']
    assert len(captured) == 2 and response['items'] == [] and response['abstained']


def test_negative_original_query_is_not_embedded(service):
    interpretation = QueryPlan(intent='Lăng vua Việt Nam, trừ Lăng Hồ Chí Minh',
        visual_query='Vietnamese royal mausoleums', metadata_query='lăng tẩm vua Việt Nam',
        keywords=['lăng vua'], exclusions=['Lăng Hồ Chí Minh'])
    service.settings.search_llm_rerank = False
    with patch.object(service.understanding, 'plan', return_value=interpretation):
        service.text_search(query='lăng vua Việt Nam bỏ cái lăng của Bác ra nhé')
    assert service.encoder.texts == [interpretation.visual_query, interpretation.metadata_query]


def test_plans_are_cached_per_query_and_category(service):
    service.settings.search_llm_rerank = False
    with patch.object(service.understanding, 'plan', return_value=QueryPlan(**PLAN)) as planner:
        first = service.text_search(query='pho', category='am_thuc')
        again = service.text_search(query='pho', category='am_thuc')
        changed = service.text_search(query='pho', category='kien_truc')
    assert [r['llm_call_count'] for r in (first, again, changed)] == [1, 0, 1]
    assert again['plan_cache_hit'] and not changed['plan_cache_hit']
    assert planner.call_args_list[0].kwargs['category'] == 'am_thuc'
    assert planner.call_args_list[1].kwargs['category'] == 'kien_truc'


@pytest.mark.parametrize('changes', [
    {'requirements': [{'id': 'R1', 'text': ''}]},
    {'requirements': [{'id': 'R1', 'text': 'a' * 241}]},
    {'preferences': [{'id': 'P1', 'text': ' '}]},
    {'requirements': ['Mặc áo dài đỏ']},
    {'requirements': [{'id': 'R1', 'text': 'Áo dài', 'extra': True}]},
    {'requirements': [{'id': 'R2', 'text': 'Áo dài'}]},
    {'requirements': [{'id': 'R1', 'text': 'Áo dài'}, {'id': 'R1', 'text': 'Hai người'}]},
    {'requirements': [{'id': 'P1', 'text': 'Áo dài'}]},
    {'preferences': [{'id': 'P2', 'text': 'Toàn cảnh'}]},
    {'preferences': [{'id': 'R1', 'text': 'Toàn cảnh'}]},
    {'preferences': [{'id': 'P1'}]},
    {'clarification_question': None, 'suggested_queries': ['Tìm phở']},
    {'visual_query': ''},
])
def test_invalid_semantic_conditions_are_rejected(changes):
    with pytest.raises(ApplicationError) as error:
        call_model([{**PLAN, **changes}] * 2, lambda: QueryUnderstanding(Settings()).plan('ảnh'))
    assert error.value.code == 'invalid_query_plan'


def test_category_is_context_for_model_and_schema_describes_conditions():
    _, captured = call_model([PLAN], lambda: QueryUnderstanding(Settings()).plan(
        'pho', category='am_thuc'))
    payload = json.loads(captured[0]['messages'][1]['content'])
    assert payload == {'request': 'pho', 'category_filter': 'am_thuc',
                       'catalog_titles': [], 'ambiguous_names': []}
    schema = captured[0]['response_format']['json_schema']['schema']
    assert {'requirements', 'preferences', 'clarification_question', 'suggested_queries'} <= set(
        schema['required'])
    for field, definition in [('requirements', 'Requirement'), ('preferences', 'Preference')]:
        assert schema['properties'][field]['items']['$ref'] == f'#/$defs/{definition}'
        condition = schema['$defs'][definition]
        assert set(condition['required']) == {'id', 'text'}
        assert condition['additionalProperties'] is False


def test_excluded_target_in_embedding_query_is_corrected_once(service):
    service.settings.search_llm_rerank = False
    broken = {**PLAN, 'metadata_query': 'áo dài đỏ không đội nón'}
    response, captured = call_model([broken, PLAN], lambda: service.text_search(query='áo dài đỏ'))
    assert len(captured) == response['llm_call_count'] == 2
    assert service.encoder.texts == [PLAN['visual_query'], PLAN['metadata_query']]
    assert 'validation_feedback' in json.loads(captured[1]['messages'][1]['content'])


def test_repeated_invalid_rewrite_stops_before_embedding(service):
    broken = {**PLAN, 'metadata_query': 'áo dài đỏ không đội nón'}
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No embedding')):
        with pytest.raises(ApplicationError) as error:
            call_model([broken, broken], lambda: service.text_search(query='áo dài đỏ'))
    assert error.value.code == 'invalid_query_plan'


def test_clarification_without_guessed_options_still_stops_retrieval(service):
    interpretation = {**PLAN, 'clarification_question': 'Bạn muốn tìm ảnh về đối tượng nào?',
                      'suggested_queries': []}
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No embedding')):
        response, _ = call_model([interpretation], lambda: service.text_search(query='cái đó'))
    assert response['clarification_required'] and response['suggested_queries'] == []
    assert response['items'] == []


def test_clarification_can_leave_embedding_queries_empty(service):
    interpretation = {**PLAN, 'visual_query': '', 'metadata_query': '',
        'clarification_question': 'Bạn muốn tìm ảnh phở hay phố?',
        'suggested_queries': ['phở', 'phố']}
    with patch.object(service, 'get_encoder', side_effect=AssertionError('No embedding')):
        response, _ = call_model([interpretation], lambda: service.text_search(query='pho'))
    assert response['clarification_required']
    assert response['llm_call_count'] == 1 and response['items'] == []


def test_schema_mistake_gets_one_repair_attempt(service):
    service.settings.search_llm_rerank = False
    response, captured = call_model([
        {**PLAN, 'requirements': [{'id': 'R2', 'text': 'Mặc áo dài đỏ'}]},
        {**PLAN, 'requirements': [{'id': 'R1', 'text': 'Mặc áo dài đỏ'}]},
    ], lambda: service.text_search(query='áo dài đỏ'))
    assert len(captured) == response['llm_call_count'] == 2
    assert response['interpretation']['requirements'] == [
        {'id': 'R1', 'text': 'Mặc áo dài đỏ'}]


def test_condition_ids_survive_cached_plan_without_additional_planning(service):
    service.settings.search_llm_rerank = False
    interpretation = {**PLAN,
        'requirements': [{'id': 'R1', 'text': 'Mặc áo dài đỏ hoặc xanh'},
                         {'id': 'R2', 'text': 'Có hai người'}],
        'preferences': [{'id': 'P1', 'text': 'Ảnh toàn cảnh'}]}

    def search_twice():
        return (service.text_search(query='hai người mặc áo dài'),
                service.text_search(query='hai người mặc áo dài'))

    (first, second), captured = call_model([interpretation], search_twice)
    assert len(captured) == 1 and second['plan_cache_hit']
    assert first['interpretation'] == second['interpretation']
    assert second['interpretation']['requirements'] == interpretation['requirements']
    assert second['interpretation']['preferences'] == interpretation['preferences']


def test_condition_text_is_trimmed_and_list_bounds_are_enforced():
    plan = QueryUnderstanding._parse_plan({**PLAN,
        'requirements': [{'id': f'R{i}', 'text': ' Điều kiện '} for i in range(1, 9)],
        'preferences': [{'id': f'P{i}', 'text': ' Ưu tiên '} for i in range(1, 5)]})
    assert plan.requirements[0].text == 'Điều kiện'
    assert plan.preferences[0].text == 'Ưu tiên'
    for field, prefix, size in [('requirements', 'R', 9), ('preferences', 'P', 5)]:
        with pytest.raises(ApplicationError) as error:
            QueryUnderstanding._parse_plan({**PLAN,
                field: [{'id': f'{prefix}{i}', 'text': 'Điều kiện'} for i in range(1, size + 1)]})
        assert error.value.code == 'invalid_query_plan'


def test_planner_receives_corpus_titles_for_grounding_aliases(service):
    _, captured = call_model([PLAN, {'suggestions': []}],
                             lambda: service.text_search(query='người mặc đồ truyền thống'))
    payload = json.loads(captured[0]['messages'][1]['content'])
    assert 'áo dài đỏ' in payload['catalog_titles']
    assert len(payload['catalog_titles']) <= 512
    assert sum(map(len, payload['catalog_titles'])) <= 12_000
