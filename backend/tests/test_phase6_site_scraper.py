"""Phase 6: site_scraper (Anthropic web search, mocked)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import site_scraper, web_research


def _resp(body: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)])


@pytest.fixture(autouse=True)
def _reset_client():
    web_research._client = None
    yield
    web_research._client = None


async def test_returns_default_with_no_key(monkeypatch):
    monkeypatch.setattr(site_scraper.settings, "ANTHROPIC_API_KEY", "")
    result = await site_scraper.scrape_company_site("Acme")
    assert result == {"about": "", "recent_updates": [], "industry": "", "size_hint": ""}


async def test_returns_default_with_empty_company(monkeypatch):
    monkeypatch.setattr(site_scraper.settings, "ANTHROPIC_API_KEY", "key")
    result = await site_scraper.scrape_company_site("")
    assert result["about"] == ""


async def test_parses_company_summary(monkeypatch):
    monkeypatch.setattr(site_scraper.settings, "ANTHROPIC_API_KEY", "key")
    body = (
        '{"about": "AI for SMB", "recent_updates": ["Series A"], '
        '"industry": "SaaS", "size_hint": "startup"}'
    )
    with patch.object(
        web_research, "_get_client",
        return_value=SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=_resp(body)))
        ),
    ):
        result = await site_scraper.scrape_company_site("Acme")
    assert result == {
        "about": "AI for SMB",
        "recent_updates": ["Series A"],
        "industry": "SaaS",
        "size_hint": "startup",
    }


async def test_handles_api_failure_gracefully(monkeypatch):
    monkeypatch.setattr(site_scraper.settings, "ANTHROPIC_API_KEY", "key")
    with patch.object(
        web_research, "_get_client",
        return_value=SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(side_effect=Exception("boom")))
        ),
    ):
        result = await site_scraper.scrape_company_site("Acme")
    assert result["about"] == ""
