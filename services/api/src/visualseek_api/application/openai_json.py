"""Server-side structured generation with model-specific OpenAI request options."""

import copy
import json

import httpx

from visualseek_api.core.errors import ApplicationError


def strict_schema(schema: dict) -> dict:
    """Use a conservative JSON-schema subset; validate bounds again in the application."""
    result = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
            for key in ("minLength", "maxLength", "minItems", "maxItems", "pattern", "default"):
                node.pop(key, None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


class OpenAIJson:
    def __init__(self, settings):
        self.settings = settings

    def generate(self, schema: dict, system: str, payload: dict, *, model: str,
                 max_tokens: int = 2048, timeout: float | None = None) -> dict:
        key = self.settings.openai_api_key.get_secret_value().strip()
        if not key:
            raise ApplicationError(
                "Chưa cấu hình OPENAI_API_KEY trong .env của backend.",
                code="openai_key_missing", status_code=503,
            )
        try:
            request_timeout = timeout or self.settings.search_llm_timeout_seconds
            options = {"temperature": 0, "max_completion_tokens": max_tokens}
            if model == "gpt-5.6-luna":
                # This reasoning model rejects the legacy temperature=0 override.
                # Its completion budget includes reasoning as well as visible JSON.
                options = {"reasoning_effort": self.settings.openai_reasoning_effort,
                           "max_completion_tokens": max(2048, max_tokens)}
            with httpx.Client(timeout=request_timeout) as client:
                response = client.post(
                    self.settings.openai_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json={"model": model, "store": False, **options,
                          "response_format": {"type": "json_schema", "json_schema": {
                              "name": "image_search", "strict": True,
                              "schema": strict_schema(schema),
                          }}, "messages": [
                              {"role": "system", "content": system},
                              {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                          ]},
                )
                response.raise_for_status()
                choice = response.json()["choices"][0]
                message = choice["message"]
                if message.get("refusal"):
                    raise ApplicationError("Model từ chối yêu cầu. Hãy mô tả lại ảnh cần tìm.",
                                           code="query_model_refused", status_code=422)
                if choice.get("finish_reason") != "stop":
                    raise ApplicationError("Phản hồi model chưa hoàn tất. Hãy thử lại.",
                                           code="query_model_incomplete", status_code=502)
                result = json.loads(message["content"])
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON object")
                return result
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                code, message = "openai_authentication_failed", (
                    "OpenAI không chấp nhận API key hoặc quyền truy cập model. "
                    "Kiểm tra OPENAI_API_KEY và SEARCH_LLM_MODEL.")
            elif status == 429:
                code, message = "openai_rate_limited", (
                    "OpenAI đang giới hạn yêu cầu hoặc hết quota. Kiểm tra hạn mức tài khoản.")
            elif status == 400:
                code, message = "openai_request_invalid", (
                    "OpenAI từ chối cấu hình request. Kiểm tra tham số tương thích với model.")
                try:
                    error = exc.response.json().get("error", {})
                    parameter = error.get("param") if isinstance(error, dict) else None
                    if parameter in {"temperature", "reasoning_effort", "max_completion_tokens",
                                     "response_format", "model"}:
                        message += f" Tham số bị từ chối: {parameter}."
                except (AttributeError, ValueError):
                    pass
            else:
                code, message = "query_model_unavailable", (
                    "Chưa gọi được OpenAI API. Kiểm tra model, OPENAI_BASE_URL và thử lại.")
            raise ApplicationError(message, code=code, status_code=503) from exc
        except httpx.HTTPError as exc:
            raise ApplicationError("Không kết nối được OpenAI API hoặc yêu cầu đã timeout.",
                                   code="query_model_unavailable", status_code=503) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ApplicationError("OpenAI trả về phản hồi không hợp lệ. Hãy thử lại.",
                                   code="invalid_llm_response", status_code=502) from exc
