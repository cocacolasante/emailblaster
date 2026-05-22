"""Tests for the 'research a client' one-shot endpoint.

Three layers covered:

1. Pure parser: ``parse_linkedin_url`` — URL shape recognition + name
   guess + hex-suffix stripping.
2. Compose helpers: truncation at sentence boundary + char_limit
   validation + prompt-builder smoke (asserting the char_limit instruction
   makes it into the prompt).
3. End-to-end POST /research-client with the Anthropic client mocked.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import compose_client, research_client
from app.services.compose_client import _truncate_at_sentence
from app.services.research_client import parse_linkedin_url


# ---- Parser ---------------------------------------------------------------


def test_parse_linkedin_url_basic_slug():
    slug, name = parse_linkedin_url("https://www.linkedin.com/in/jane-doe/")
    assert slug == "jane-doe"
    assert name == "Jane Doe"


def test_parse_linkedin_url_strips_hex_suffix():
    slug, name = parse_linkedin_url("https://www.linkedin.com/in/jane-doe-a5898840a")
    assert slug == "jane-doe-a5898840a"
    # The trailing dedup hash is dropped from the human-readable guess.
    assert name == "Jane Doe"


def test_parse_linkedin_url_handles_pub_path():
    slug, name = parse_linkedin_url("https://linkedin.com/pub/john-q-smith/12/345/678")
    assert slug == "john-q-smith"
    assert name == "John Q Smith"


def test_parse_linkedin_url_rejects_non_linkedin_urls():
    assert parse_linkedin_url("https://twitter.com/x") == ("", "")
    assert parse_linkedin_url("https://www.linkedin.com/company/x") == ("", "")
    assert parse_linkedin_url("not a url") == ("", "")
    assert parse_linkedin_url("") == ("", "")


# ---- Compose helpers: truncation -----------------------------------------


def test_truncate_at_sentence_under_limit_returns_unchanged():
    text = "Short message."
    assert _truncate_at_sentence(text, 100) == text


def test_truncate_at_sentence_cuts_at_period():
    text = "First sentence here. Second sentence runs longer than the cap allows for sure."
    out = _truncate_at_sentence(text, 30)
    # The cut should land on the period boundary, not mid-word.
    assert out == "First sentence here."


def test_truncate_at_sentence_falls_back_to_word_boundary():
    text = "one two three four five six seven eight nine ten eleven twelve thirteen"
    out = _truncate_at_sentence(text, 25)
    # No sentence punctuation → fall back to a word boundary, no mid-word cut.
    assert " " not in out[-1:]  # not trailing space
    assert not out.endswith("fou")  # didn't cut mid-word
    assert len(out) <= 25


def test_compose_for_client_rejects_out_of_range_char_limit():
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(compose_client.compose_for_client(
            output_kind="email", goal="g", tone="t", sender_name="s",
            char_limit=10, research={"quality": "low"},
        ))
    with pytest.raises(ValueError):
        asyncio.run(compose_client.compose_for_client(
            output_kind="email", goal="g", tone="t", sender_name="s",
            char_limit=10000, research={"quality": "low"},
        ))


def test_research_prompt_includes_freshness_rules():
    """The research prompt must:

    1. Explicitly bound news/personalization signals to a 6-month window
       with a 1-year hard floor (regression: a previous prompt asked
       for "last 12 months" company news which let stale references
       leak into compose).
    2. Distinguish between IDENTITY fields (no freshness gate — the
       LinkedIn profile is source of truth for current role) and
       PERSONALIZATION signals (strict freshness).  Regression: a
       previous version of this prompt required Anthropic to find a
       news article within 6 months to confirm the prospect's current
       role.  That left job_title/company blank for typical sales
       prospects with no press coverage — the user reported "no
       research" on 2026-05-21.
    """
    import inspect
    from app.services import research_client as rc
    src = inspect.getsource(rc.research_from_linkedin_url)
    # Freshness anchors for news.
    assert "6 months" in src.lower() or "183 days" in src or "freshness" in src.lower()
    assert "over a year old" in src.lower() or "365" in src
    # Current-role guard.
    assert "current role" in src.lower() or "CURRENT" in src
    # Identity vs personalization separation — must NOT require fresh
    # confirmation for identity fields.
    assert "IDENTITY" in src or "identity" in src
    assert "PERSONALIZATION" in src or "personalization" in src
    # The anti-regression: the prompt must NOT say "verified within the
    # freshness window" for the IDENTITY/CURRENT-role section.
    assert "verified within the freshness window" not in src
    assert "fresh-enough source" not in src


def test_compose_prompts_include_freshness_block_for_all_paths():
    """All four compose prompt paths (email-low, email-rich, dm-low,
    dm-rich) must carry the FRESHNESS RULES block so the message-writing
    LLM doesn't infer a stale reference from the research bullets."""
    low_research = {"quality": "low", "first_name": "Jane", "last_name": "Doe"}
    rich_research = {
        "quality": "rich", "first_name": "Jane", "last_name": "Doe",
        "job_title": "CEO", "company": "Acme",
        "headline": "Founder", "person_news": ["spoke at conf (Apr 2026)"],
        "company_news": ["raised Series B (Mar 2026)"],
        "company_description": "B2B platform",
    }
    for research in (low_research, rich_research):
        for builder in (compose_client._build_email_prompt, compose_client._build_dm_prompt):
            prompt = builder(
                goal="Book a call", tone="warm", sender_name="A",
                char_limit=400, research=research,
            )
            assert "FRESHNESS RULES" in prompt, builder.__name__
            assert "6 months" in prompt, builder.__name__
            assert "over a year old" in prompt.lower() or "over a year" in prompt, builder.__name__
            # And the current-role guard.
            assert "no longer holds" in prompt or "current role" in prompt.lower(), builder.__name__


def test_freshness_block_includes_concrete_dates():
    """The freshness block embeds today's ISO date plus the 6-month and
    1-year floors as concrete ISO dates so the LLM doesn't have to do
    date math (it's bad at it).  Asserting only the structure, not the
    exact dates, since this test runs at variable wall-clock times."""
    block = compose_client._freshness_block()
    # ISO-shaped dates appear three times: today, 6mo floor, 1y floor.
    import re as _re
    dates = _re.findall(r"\d{4}-\d{2}-\d{2}", block)
    assert len(dates) >= 3


def test_email_prompt_includes_char_limit():
    prompt = compose_client._build_email_prompt(
        goal="Book a call", tone="warm", sender_name="Anthony",
        char_limit=450,
        research={"quality": "rich", "first_name": "Jane", "last_name": "Doe",
                  "job_title": "CEO", "company": "Acme",
                  "headline": "Builder of things",
                  "person_news": ["raised Series B"], "company_news": [],
                  "company_description": "B2B platform"},
    )
    assert "450 characters" in prompt
    assert "raised Series B" in prompt


def test_dm_prompt_uses_low_quality_path_when_research_empty():
    prompt = compose_client._build_dm_prompt(
        goal="Open conversation", tone="casual", sender_name="Anthony",
        char_limit=250, research={"quality": "low", "first_name": "Jane"},
    )
    assert "250 characters" in prompt
    assert "No personalization signals" in prompt
    # Subject line must be requested for both DM paths now.
    assert "subject" in prompt.lower()


def test_dm_subject_falls_back_to_empty_when_model_omits_it():
    """Body-only DM responses still parse — older mocks / model drift
    shouldn't break the flow; we surface ``subject=""`` and let the UI
    render the body alone."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    body_only = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"body": "Hi"}')])
    create = AsyncMock(return_value=body_only)
    with patch(
        "app.workers.compose._get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=create)),
    ):
        out = asyncio.run(compose_client.compose_for_client(
            output_kind="linkedin_dm", goal="g", tone="t", sender_name="s",
            char_limit=200,
            research={"quality": "low", "first_name": "Jane", "last_name": "Doe"},
        ))
    assert out["body"] == "Hi"
    assert out["subject"] == ""


# ---- End-to-end via the FastAPI client -----------------------------------


def _anthropic_text(body: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)])


@pytest.fixture(autouse=True)
def _reset_clients():
    research_client._client = None
    # The compose helper uses the bulk worker's _get_client, which is in compose mod.
    from app.workers import compose as compose_mod
    compose_mod._client = None


async def test_rejects_non_linkedin_url(client):
    resp = await client.post("/research-client", json={
        "linkedin_url": "https://example.com/profile",
        "goal": "Book a discovery call",
    })
    assert resp.status_code == 400
    assert "linkedin profile url" in resp.json()["detail"].lower()


async def test_happy_path_email(client):
    """Two Anthropic calls expected: research, then compose."""
    research_response = (
        '{"first_name": "Jane", "last_name": "Doe", '
        '"headline": "Founder", "company": "Acme", '
        '"company_website": "acme.io", "job_title": "CEO", '
        '"industry": "SaaS", "person_news": ["raised Series B"], '
        '"company_news": ["launched Acme Pro"], '
        '"company_description": "B2B platform", '
        '"recent_updates": [], "found": true}'
    )
    compose_response = (
        '{"subject": "Quick thought after your Series B", '
        '"body": "Hi Jane, congrats on the Series B raise. I help SaaS founders ship faster. Worth a 15-min call?"}'
    )

    research_mock = AsyncMock(return_value=_anthropic_text(research_response))
    compose_mock = AsyncMock(return_value=_anthropic_text(compose_response))

    with patch.object(
        research_client, "_get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=research_mock)),
    ), patch(
        "app.workers.compose._get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=compose_mock)),
    ):
        resp = await client.post("/research-client", json={
            "linkedin_url": "https://www.linkedin.com/in/jane-doe/",
            "goal": "Book a discovery call about scaling SaaS",
            "tone": "warm",
            "sender_name": "Anthony",
            "research_mode": "fast",
            "output_kind": "email",
            "char_limit": 600,
        })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["first_name"] == "Jane"
    assert body["profile"]["company"] == "Acme"
    assert body["profile"]["found"] is True
    assert body["profile"]["quality"] == "rich"
    assert "Series B" in body["body"]
    assert body["subject"]
    assert body["char_count"] == len(body["body"])
    assert body["char_count"] <= 600
    # Sanity: each mock was called exactly once.
    assert research_mock.await_count == 1
    assert compose_mock.await_count == 1


async def test_happy_path_linkedin_dm(client):
    """LinkedIn DM path: subject is empty, body present, char limit enforced."""
    research_response = (
        '{"first_name": "Bob", "last_name": "Smith", '
        '"headline": "VP Eng", "company": "Beta Corp", '
        '"company_website": "beta.com", "job_title": "VP Engineering", '
        '"industry": "FinTech", "person_news": ["spoke at Strange Loop"], '
        '"company_news": [], "company_description": "infra", '
        '"recent_updates": [], "found": true}'
    )
    dm_response = (
        '{"subject": "Notes on resilience", '
        '"body": "Hi Bob, caught your Strange Loop talk on resilience. Wanted to swap notes on what we ship at our shop. Worth a quick chat?"}'
    )

    research_mock = AsyncMock(return_value=_anthropic_text(research_response))
    compose_mock = AsyncMock(return_value=_anthropic_text(dm_response))

    with patch.object(
        research_client, "_get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=research_mock)),
    ), patch(
        "app.workers.compose._get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=compose_mock)),
    ):
        resp = await client.post("/research-client", json={
            "linkedin_url": "https://www.linkedin.com/in/bob-smith-9999999a/",
            "goal": "Open a conversation about distributed systems",
            "research_mode": "deep",
            "output_kind": "linkedin_dm",
            "char_limit": 300,
        })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == "Notes on resilience"
    assert "Strange Loop" in body["body"]
    assert body["char_count"] <= 300


async def test_anthropic_auth_failure_returns_502_with_actionable_detail(client):
    """A bad/expired ANTHROPIC_API_KEY surfaces as a 502 with a clear
    remediation hint, not a generic 500.  Regression: prior to the fix
    a 401 from Anthropic propagated up through both the research stage
    (caught) and the compose stage (uncaught), and the latter became
    an opaque 500 in the browser console."""
    from anthropic import AuthenticationError

    # The research stage already catches every exception and returns
    # an empty dict, so the failure mode that escapes is the compose
    # call.  Patch the compose stage's Anthropic client to raise
    # AuthenticationError on every messages.create.
    auth_err = AuthenticationError(
        message="Invalid authentication credentials",
        response=SimpleNamespace(status_code=401, headers={}, request=SimpleNamespace()),
        body=None,
    )
    research_response = (
        '{"first_name": "Jane", "last_name": "Doe", '
        '"headline": "", "company": "", "company_website": "", '
        '"job_title": "", "industry": "", "person_news": [], '
        '"company_news": [], "company_description": "", '
        '"recent_updates": [], "found": false}'
    )
    research_mock = AsyncMock(return_value=_anthropic_text(research_response))
    compose_mock = AsyncMock(side_effect=auth_err)

    with patch.object(
        research_client, "_get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=research_mock)),
    ), patch(
        "app.workers.compose._get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=compose_mock)),
    ):
        resp = await client.post("/research-client", json={
            "linkedin_url": "https://www.linkedin.com/in/jane-doe/",
            "goal": "Book a call",
            "char_limit": 300,
            "output_kind": "linkedin_dm",
        })

    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"].lower()
    # Actionable: tell the user what to do, not just "auth failed".
    assert "anthropic" in detail
    assert "key" in detail
    assert ".env" in detail


async def test_compose_failure_returns_502(client):
    """Anthropic returns garbage twice → compose helper raises → 502 to caller."""
    research_response = (
        '{"first_name": "X", "last_name": "Y", "headline": "", "company": "Z", '
        '"company_website": "", "job_title": "", "industry": "", '
        '"person_news": [], "company_news": [], "company_description": "", '
        '"recent_updates": [], "found": false}'
    )
    research_mock = AsyncMock(return_value=_anthropic_text(research_response))
    # Compose returns unparseable text both times.
    compose_mock = AsyncMock(return_value=_anthropic_text("not a json blob"))

    with patch.object(
        research_client, "_get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=research_mock)),
    ), patch(
        "app.workers.compose._get_client",
        return_value=SimpleNamespace(messages=SimpleNamespace(create=compose_mock)),
    ):
        resp = await client.post("/research-client", json={
            "linkedin_url": "https://www.linkedin.com/in/foo/",
            "goal": "ping",
            "char_limit": 200,
            "output_kind": "email",
        })
    assert resp.status_code == 502
