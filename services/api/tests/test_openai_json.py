import json
from unittest.mock import patch

import httpx
import pytest

from visualseek_api.application.openai_json import OpenAIJson, strict_schema
from visualseek_api.application.query_understanding import QueryPlan
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError


def run_response(response, settings=None, model="gpt-4o-mini"):
    original = httpx.Client
    requests = []

    def respond(request):
        requests.append(request)
        return response

    with patch("visualseek_api.application.openai_json.httpx.Client",
               side_effect=lambda **kwargs: original(transport=httpx.MockTransport(respond))):
        result = OpenAIJson(settings or Settings()).generate(
            QueryPlan.model_json_schema(), "System instructions", {"request": "áo dài"},
            model=model, max_tokens=512)
    return result, requests


def test_openai_wire_contract_and_server_only_authorization():
    result, requests = run_response(httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}))
    assert result == {"ok": True}
    request = requests[0]
    payload = json.loads(request.content)
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer unit-test-key"
    assert payload["model"] == "gpt-4o-mini"
    assert payload["store"] is False
    assert payload["max_completion_tokens"] == 512
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert "unit-test-key" not in request.content.decode()
    assert "unit-test-key" not in repr(Settings())


def test_missing_key_fails_before_network():
    with patch("visualseek_api.application.openai_json.httpx.Client") as client:
        with pytest.raises(ApplicationError) as error:
            OpenAIJson(Settings(openai_api_key="")).generate({}, "system", {}, model="gpt-4o-mini")
    assert error.value.code == "openai_key_missing"
    client.assert_not_called()


@pytest.mark.parametrize(("status", "code"), [
    (401, "openai_authentication_failed"), (403, "openai_authentication_failed"),
    (429, "openai_rate_limited"), (500, "query_model_unavailable"),
])
def test_openai_failure_has_actionable_error_without_key_or_remote_body(status, code):
    with pytest.raises(ApplicationError) as error:
        run_response(httpx.Response(status, text="sensitive remote error"))
    assert error.value.code == code
    assert "sensitive remote error" not in error.value.message
    assert "unit-test-key" not in error.value.message


@pytest.mark.parametrize(("body", "code"), [
    ({"choices": []}, "invalid_llm_response"),
    ({"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
     "query_model_incomplete"),
    ({"choices": [{"finish_reason": "stop", "message": {"refusal": "No"}}]},
     "query_model_refused"),
    ({"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]},
     "invalid_llm_response"),
    ({"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]},
     "invalid_llm_response"),
])
def test_reject_refusal_truncation_and_malformed_responses(body, code):
    with pytest.raises(ApplicationError) as error:
        run_response(httpx.Response(200, json=body))
    assert error.value.code == code


def test_schema_conversion_does_not_mutate_local_validation_constraints():
    original = QueryPlan.model_json_schema()
    converted = strict_schema(original)
    assert "maxLength" in original["properties"]["visual_query"]
    assert "maxLength" not in converted["properties"]["visual_query"]
    assert set(converted["required"]) == set(converted["properties"])


def test_luna_uses_reasoning_options_without_legacy_temperature():
    _, requests = run_response(httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}),
        settings=Settings(openai_reasoning_effort="low"), model="gpt-5.6-luna")
    payload = json.loads(requests[0].content)
    assert payload["model"] == "gpt-5.6-luna"
    assert "temperature" not in payload
    assert payload["reasoning_effort"] == "low"
    assert payload["max_completion_tokens"] == 2048
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_invalid_parameter_error_is_actionable_without_echoing_remote_message():
    with pytest.raises(ApplicationError) as error:
        run_response(httpx.Response(400, json={"error": {
            "param": "temperature", "message": "sensitive remote content"}}))
    assert error.value.code == "openai_request_invalid"
    assert "temperature" in error.value.message
    assert "sensitive remote content" not in error.value.message
