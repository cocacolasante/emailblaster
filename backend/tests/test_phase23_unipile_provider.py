"""Tests for ``UnipileLinkedInProvider`` using httpx.MockTransport.

The provider is just a thin async wrapper over Unipile's REST API, so
the tests are mostly about: did we hit the right path with the right
payload, did we map Unipile's error envelopes to our domain exceptions,
did we extract the right IDs from success responses.

Real Unipile API calls are NEVER made here — we wire an
``httpx.MockTransport`` into the provider via its ``transport=`` kwarg.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from app.services.linkedin.base import (
    AccountRestricted,
    ChallengeRequired,
    ProfileRef,
)
from app.services.linkedin.unipile_impl import (
    UnipileError,
    UnipileLinkedInProvider,
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@dataclass
class _FakeAccount:
    """Stand-in for the LinkedInAccount SQLAlchemy model."""
    id: str = "li-acct-1"
    unipile_account_id: str | None = "up-acct-XYZ"
    pending_challenge_url: str | None = None
    last_error: str | None = None
    status: str | None = None
    session_cookies_encrypted: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def _provider_with(handler) -> UnipileLinkedInProvider:
    """Build a provider with an injected MockTransport that delegates to ``handler``."""
    transport = httpx.MockTransport(handler)
    return UnipileLinkedInProvider(
        dsn="api-test.unipile.com:13443",
        api_key="test-api-key",
        transport=transport,
    )


def _capture(records: list[httpx.Request]):
    """Build a MockTransport handler that records every request, returning canned 200s.

    The handler can be parametrised in tests by passing a different
    response factory or wrapping it.  By default it returns
    ``{"ok": true}`` to every call.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        records.append(request)
        return httpx.Response(200, json={"ok": True})
    return handler


# --------------------------------------------------------------------------
# _request — base behaviour, error mapping, header injection
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_headers_include_api_key():
    seen: list[httpx.Request] = []
    prov = _provider_with(_capture(seen))
    await prov.fetch_account_status("up-1")
    assert seen[0].headers["X-API-KEY"] == "test-api-key"
    assert seen[0].headers["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_dsn_with_scheme_is_respected():
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(_capture(seen))
    prov = UnipileLinkedInProvider(
        dsn="https://custom.unipile.com:1313",
        api_key="k",
        transport=transport,
    )
    await prov.fetch_account_status("up-1")
    assert str(seen[0].url).startswith("https://custom.unipile.com:1313/")


@pytest.mark.asyncio
async def test_request_4xx_raises_unipile_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"type": "errors/validation", "title": "bad input", "detail": "username missing"},
        )
    prov = _provider_with(handler)
    with pytest.raises(UnipileError) as ei:
        await prov.fetch_account_status("up-1")
    assert ei.value.status == 422
    assert ei.value.code == "errors/validation"
    assert "username missing" in str(ei.value)


@pytest.mark.asyncio
async def test_checkpoint_error_maps_to_challenge_required():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"type": "errors/checkpoint", "title": "needs 2fa", "detail": "..."},
        )
    prov = _provider_with(handler)
    with pytest.raises(ChallengeRequired):
        await prov.fetch_account_status("up-1")


@pytest.mark.asyncio
async def test_restricted_error_maps_to_account_restricted():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"type": "errors/restricted", "title": "account banned"},
        )
    prov = _provider_with(handler)
    with pytest.raises(AccountRestricted):
        await prov.fetch_account_status("up-1")


@pytest.mark.asyncio
async def test_network_error_maps_to_unipile_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")
    prov = _provider_with(handler)
    with pytest.raises(UnipileError) as ei:
        await prov.fetch_account_status("up-1")
    assert ei.value.status == 0
    assert ei.value.code == "network"


# --------------------------------------------------------------------------
# Config errors
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_dsn_raises_clean_error():
    prov = UnipileLinkedInProvider(dsn="", api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(UnipileError) as ei:
        await prov.fetch_account_status("up-1")
    assert "UNIPILE_DSN" in str(ei.value) or "DSN" in str(ei.value)


@pytest.mark.asyncio
async def test_missing_api_key_raises_clean_error():
    prov = UnipileLinkedInProvider(dsn="api.unipile.com:1313", api_key="", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(UnipileError) as ei:
        await prov.fetch_account_status("up-1")
    assert "UNIPILE_API_KEY" in str(ei.value) or "API_KEY" in str(ei.value)


@pytest.mark.asyncio
async def test_account_missing_unipile_id_returns_actionable_error():
    acct = _FakeAccount(unipile_account_id=None)
    prov = _provider_with(lambda r: httpx.Response(200, json={}))
    res = await prov.test_connection(acct)
    assert res.ok is False
    assert "hosted login" in res.error or "no_account" in (res.meta or {}).get("code", "")


# --------------------------------------------------------------------------
# Hosted-auth link
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_hosted_auth_link_posts_expected_payload():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"url": "https://accounts.unipile.com/x", "id": "link-1"})
    prov = _provider_with(handler)
    out = await prov.create_hosted_auth_link(
        success_redirect_url="https://app.example.com/back",
        failure_redirect_url="https://app.example.com/fail",
        notify_url="https://api.example.com/webhooks/unipile",
        name="my-account",
    )
    assert out["url"].startswith("https://")
    body = json.loads(seen[0].content)
    assert body["providers"] == ["LINKEDIN"]
    assert body["success_redirect_url"] == "https://app.example.com/back"
    assert body["failure_redirect_url"] == "https://app.example.com/fail"
    assert body["notify_url"] == "https://api.example.com/webhooks/unipile"
    assert body["name"] == "my-account"
    assert body["type"] == "create"


# --------------------------------------------------------------------------
# test_connection — status mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize("upstream_status", ["OK", "CONNECTED", "ACTIVE"])
@pytest.mark.asyncio
async def test_test_connection_ok_statuses(upstream_status):
    prov = _provider_with(lambda r: httpx.Response(200, json={"status": upstream_status}))
    res = await prov.test_connection(_FakeAccount())
    assert res.ok is True


@pytest.mark.parametrize("upstream_status", ["CHECKPOINT", "OTP_REQUIRED_2FA"])
@pytest.mark.asyncio
async def test_test_connection_checkpoint_statuses_signal_challenge(upstream_status):
    prov = _provider_with(lambda r: httpx.Response(200, json={"status": upstream_status}))
    acct = _FakeAccount()
    res = await prov.test_connection(acct)
    assert res.ok is False
    assert (res.meta or {}).get("challenged") is True
    assert acct.pending_challenge_url == "https://www.linkedin.com"


@pytest.mark.parametrize("upstream_status", ["BANNED", "RESTRICTED", "SUSPENDED"])
@pytest.mark.asyncio
async def test_test_connection_penalty_statuses_signal_restricted(upstream_status):
    prov = _provider_with(lambda r: httpx.Response(200, json={"status": upstream_status}))
    res = await prov.test_connection(_FakeAccount())
    assert res.ok is False
    assert (res.meta or {}).get("restricted") is True


# --------------------------------------------------------------------------
# view_profile / follow / react / latest_post
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_profile_calls_users_endpoint_with_account_id():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"provider_id": "urn:li:fsd_profile:ABC", "name": "Test"})
    prov = _provider_with(handler)
    res = await prov.view_profile(_FakeAccount(), ProfileRef(public_id="john-doe"))
    assert res.ok is True
    assert res.external_id == "urn:li:fsd_profile:ABC"
    assert "/api/v1/users/john-doe" in str(seen[0].url)
    assert "account_id=up-acct-XYZ" in str(seen[0].url)


@pytest.mark.asyncio
async def test_view_profile_no_id_returns_error():
    prov = _provider_with(lambda r: httpx.Response(200, json={}))
    res = await prov.view_profile(_FakeAccount(), ProfileRef())
    assert res.ok is False


@pytest.mark.asyncio
async def test_view_profile_propagates_challenge():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"type": "errors/checkpoint", "title": "checkpoint"})
    prov = _provider_with(handler)
    acct = _FakeAccount()
    with pytest.raises(ChallengeRequired):
        await prov.view_profile(acct, ProfileRef(public_id="x"))
    assert acct.pending_challenge_url == "https://www.linkedin.com"


@pytest.mark.asyncio
async def test_follow_profile_posts_to_follow_endpoint():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"followed": True})
    prov = _provider_with(handler)
    res = await prov.follow_profile(_FakeAccount(), ProfileRef(public_id="j"))
    assert res.ok is True
    assert seen[0].method == "POST"
    assert "/api/v1/users/j/follow" in str(seen[0].url)


@pytest.mark.asyncio
async def test_react_to_post_sends_reaction_type():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"reacted": True})
    prov = _provider_with(handler)
    res = await prov.react_to_post(_FakeAccount(), "urn:li:share:1", reaction="celebrate")
    assert res.ok is True
    body = json.loads(seen[0].content)
    assert body == {"type": "CELEBRATE"}


@pytest.mark.asyncio
async def test_latest_post_urn_returns_first_item():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"urn": "urn:li:share:111"}, {"urn": "urn:li:share:222"}]})
    prov = _provider_with(handler)
    urn = await prov.latest_post_urn(_FakeAccount(), ProfileRef(public_id="x"))
    assert urn == "urn:li:share:111"


@pytest.mark.asyncio
async def test_latest_post_urn_returns_none_on_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "broken"})
    prov = _provider_with(handler)
    urn = await prov.latest_post_urn(_FakeAccount(), ProfileRef(public_id="x"))
    assert urn is None


# --------------------------------------------------------------------------
# send_connect / send_dm / invite_to_page / inmail / comment
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_connect_uses_invite_endpoint_with_message_trim():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"invitation_id": "INV-1"})
    prov = _provider_with(handler)
    note = "x" * 250  # over 200-char floor
    res = await prov.send_connect_request(_FakeAccount(), ProfileRef(public_id="j"), note=note)
    assert res.ok is True
    body = json.loads(seen[0].content)
    assert body["provider_id"] == "j"
    assert body["account_id"] == "up-acct-XYZ"
    assert len(body["message"]) == 200


@pytest.mark.asyncio
async def test_send_connect_omits_message_when_none():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={})
    prov = _provider_with(handler)
    await prov.send_connect_request(_FakeAccount(), ProfileRef(public_id="j"), note=None)
    body = json.loads(seen[0].content)
    assert "message" not in body


@pytest.mark.asyncio
async def test_send_dm_starts_chat_with_text():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"chat_id": "C-1", "message_id": "M-1"})
    prov = _provider_with(handler)
    res = await prov.send_dm(_FakeAccount(), ProfileRef(public_id="j"), text="hi there")
    assert res.ok is True
    body = json.loads(seen[0].content)
    assert body == {"account_id": "up-acct-XYZ", "attendees_ids": ["j"], "text": "hi there"}
    assert res.external_id == "M-1"


@pytest.mark.asyncio
async def test_invite_to_page_posts_to_company_endpoint():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={})
    prov = _provider_with(handler)
    res = await prov.invite_to_page(_FakeAccount(), ProfileRef(public_id="j"), page_id="9876")
    assert res.ok is True
    assert "/api/v1/companies/9876/invite" in str(seen[0].url)
    body = json.loads(seen[0].content)
    assert body == {"provider_id": "j"}


@pytest.mark.asyncio
async def test_send_inmail_signals_premium_required_when_unipile_says_so():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={"type": "errors/premium_required", "title": "no inmail credits"},
        )
    prov = _provider_with(handler)
    res = await prov.send_inmail(_FakeAccount(), ProfileRef(public_id="j"), subject="hi", body="msg")
    assert res.ok is False
    assert (res.meta or {}).get("premium_required") is True


@pytest.mark.asyncio
async def test_comment_on_post_posts_to_comments_endpoint():
    seen: list[httpx.Request] = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"comment_id": "CMT-1"})
    prov = _provider_with(handler)
    res = await prov.comment_on_post(_FakeAccount(), "urn:li:share:1", "nice post")
    assert res.ok is True
    body = json.loads(seen[0].content)
    assert body == {"text": "nice post"}


# --------------------------------------------------------------------------
# inbox_recent_events — polling fallback
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_recent_events_returns_events_after_since():
    since = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    new_ts = (since + timedelta(minutes=10)).isoformat()
    old_ts = (since - timedelta(minutes=10)).isoformat()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "items": [
                {
                    "id": "chat-new",
                    "last_message": {
                        "timestamp": new_ts,
                        "text": "hello back",
                        "sender": {"provider_id": "lead-1", "urn": "urn:li:fsd_profile:ABC"},
                    },
                },
                {
                    "id": "chat-old",
                    "last_message": {
                        "timestamp": old_ts,
                        "text": "ignore me",
                        "sender": {"provider_id": "lead-old"},
                    },
                },
            ]
        })
    prov = _provider_with(handler)
    events = await prov.inbox_recent_events(_FakeAccount(), since)
    assert len(events) == 1
    assert events[0].kind == "message_received"
    assert events[0].from_public_id == "lead-1"
    assert events[0].message_text == "hello back"


@pytest.mark.asyncio
async def test_inbox_recent_events_returns_empty_on_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})
    prov = _provider_with(handler)
    events = await prov.inbox_recent_events(_FakeAccount(), datetime.now(timezone.utc))
    assert events == []
