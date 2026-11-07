import pytest


@pytest.fixture(autouse=True)
def isolate_llm_credentials(monkeypatch):
    """Unit tests must never depend on or send the user's real API credentials."""
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("SEARCH_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("RAG_MODEL", "gpt-4o-mini")
    # Existing LLM orchestration tests explicitly exercise the opt-in configuration.
    monkeypatch.setenv('SEARCH_LLM_ENABLED', 'true')
    # Planner/selector contract tests exercise full planning; auto-routing has its own tests.
    monkeypatch.setenv('SEARCH_ROUTING_MODE', 'always')
    monkeypatch.setenv('SEARCH_LLM_RERANK', 'false')
    monkeypatch.setenv('SEARCH_RERANK_SCOPE', 'planned')
    monkeypatch.setenv('SEARCH_RERANK_EVIDENCE', 'annotations')
    monkeypatch.setenv('SEARCH_RELEVANCE_MODE', 'shadow')
    monkeypatch.delenv('SEARCH_RELEVANCE_POLICY', raising=False)
