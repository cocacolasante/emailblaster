"""Phase 6: web_research service (Anthropic + web_search, mocked)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import web_research


def _anthropic_text_response(body: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)])


@pytest.fixture(autouse=True)
def _reset_client():
    """Force a fresh client per test so api-key patches take effect."""
    web_research._client = None
    yield
    web_research._client = None


async def test_returns_default_when_no_api_key(monkeypatch):
    monkeypatch.setattr(web_research.settings, "ANTHROPIC_API_KEY", "")
    result = await web_research.research_person_web("John", "Doe", "Acme", "CEO")
    assert result == {
        "person_news": [],
        "company_news": [],
        "company_description": "",
        "found": False,
    }


async def test_parses_valid_json_response(monkeypatch):
    monkeypatch.setattr(web_research.settings, "ANTHROPIC_API_KEY", "test-key")
    response_text = (
        '{"person_news": ["raised Series B"], "company_news": ["launched product"], '
        '"company_description": "AI startup", "found": true}'
    )
    with patch.object(
        web_research, "_get_client",
        return_value=SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=_anthropic_text_response(response_text)))
        ),
    ):
        result = await web_research.research_person_web("Jane", "Doe", "Acme", "CEO")

    assert result["person_news"] == ["raised Series B"]
    assert result["company_news"] == ["launched product"]
    assert result["company_description"] == "AI startup"
    assert result["found"] is True


async def test_strips_markdown_fences(monkeypatch):
    monkeypatch.setattr(web_research.settings, "ANTHROPIC_API_KEY", "test-key")
    text = '```json\n{"person_news": [], "company_news": [], "company_description": "x", "found": false}\n```'
    with patch.object(
        web_research, "_get_client",
        return_value=SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=_anthropic_text_response(text)))
        ),
    ):
        result = await web_research.research_person_web("J", "D", "Acme", "CEO")
    assert result["company_description"] == "x"
    assert result["found"] is False


async def test_extracts_json_from_prose(monkeypatch):
    monkeypatch.setattr(web_research.settings, "ANTHROPIC_API_KEY", "test-key")
    text = (
        'Here is what I found:\n'
        '{"person_news": ["news"], "company_news": [], "company_description": "desc", "found": true}\n'
        'Hope that helps!'
    )
    with patch.object(
        web_research, "_get_client",
        return_value=SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=_anthropic_text_response(text)))
        ),
    ):
        result = await web_research.research_person_web("J", "D", "Acme", "CEO")
    assert result["person_news"] == ["news"]
    assert result["company_description"] == "desc"


async def test_returns_default_on_api_exception(monkeypatch):
    monkeypatch.setattr(web_research.settings, "ANTHROPIC_API_KEY", "test-key")
    with patch.object(
        web_research, "_get_client",
        return_value=SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("timeout")))
        ),
    ):
        result = await web_research.research_person_web("J", "D", "Acme", "CEO")
    assert result["found"] is False
    assert result["person_news"] == []


async def test_returns_default_on_unparseable_response(monkeypatch):
    monkeypatch.setattr(web_research.settings, "ANTHROPIC_API_KEY", "test-key")
    with patch.object(
        web_research, "_get_client",
        return_value=SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=_anthropic_text_response("garbage no json")))
        ),
    ):
        result = await web_research.research_person_web("J", "D", "Acme", "CEO")
    assert result["found"] is False


async def test_invokes_web_search_tool(monkeypatch):
    monkeypatch.setattr(web_research.settings, "ANTHROPIC_API_KEY", "test-key")
    create_mock = AsyncMock(return_value=_anthropic_text_response('{}'))
    with patch.object(
        web_research, "_get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=create_mock)),
    ):
        await web_research.research_person_web("J", "D", "Acme", "CEO")

    kwargs = create_mock.call_args.kwargs
    tools = kwargs["tools"]
    assert any(t.get("type") == "web_search_20250305" for t in tools)
