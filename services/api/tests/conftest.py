import pytest


@pytest.fixture(autouse=True)
def isolate_llm_credentials(monkeypatch):
    """Unit tests must never depend on or send the user's real API credentials."""
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("SEARCH_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("RAG_MODEL", "gpt-4o-mini")
