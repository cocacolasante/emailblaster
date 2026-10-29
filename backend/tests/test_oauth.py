"""OAuth for MCP clients that won't take a pasted key (Claude, ChatGPT):
discovery → dynamic registration → authorize (PKCE) → consent → token →
the token works on /mcp exactly like an API key."""
from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text

from app.config import settings
from tests.conftest import DEFAULT_USER_ID

ORIGIN = "https://outreach.example.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture(autouse=True)
def _public_origin(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_ORIGIN", ORIGIN)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _register(anon_client, **overrides) -> dict:
    body = {"client_name": "Claude", "redirect_uris": [CALLBACK],
            "token_endpoint_auth_method": "none", **overrides}
    resp = await anon_client.post("/oauth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _authorize(anon_client, client_id, challenge, **extra):
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
              "resource": f"{ORIGIN}/mcp", **extra}
    return await anon_client.get("/oauth/authorize", params=params, follow_redirects=False)


def _query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def _approved_code(client, anon_client) -> tuple[str, str, str]:
    """(client_id, code, verifier) after the signed-in user approves."""
    reg = await _register(anon_client)
    verifier, challenge = _pkce()
    resp = await _authorize(anon_client, reg["client_id"], challenge)
    assert resp.status_code == 302, resp.text
    request_id = _query(resp.headers["location"])["request"]
    decided = await client.post(f"/oauth/requests/{request_id}/decision", json={"approve": True})
    assert decided.status_code == 200, decided.text
    back = decided.json()["redirect_to"]
    assert back.startswith(CALLBACK)
    q = _query(back)
    assert q["state"] == "xyz"
    return reg["client_id"], q["code"], verifier


async def _exchange(anon_client, client_id, code, verifier, **extra):
    return await anon_client.post("/oauth/token", data={
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "code_verifier": verifier, "redirect_uri": CALLBACK, **extra,
    })


async def test_unauthenticated_mcp_points_clients_at_oauth_discovery(anon_client):
    resp = await anon_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp.status_code == 401
    assert f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/mcp"' in resp.headers["www-authenticate"]

    prm = (await anon_client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert prm["resource"] == f"{ORIGIN}/mcp" and prm["authorization_servers"] == [ORIGIN]
    asm = (await anon_client.get("/.well-known/oauth-authorization-server")).json()
    assert asm["issuer"] == ORIGIN
    assert asm["registration_endpoint"] == f"{ORIGIN}/oauth/register"
    assert asm["code_challenge_methods_supported"] == ["S256"]


async def test_full_flow_issues_a_token_that_works_on_mcp(client, anon_client, db_session):
    client_id, code, verifier = await _approved_code(client, anon_client)

    resp = await _exchange(anon_client, client_id, code, verifier)
    assert resp.status_code == 200, resp.text
    tok = resp.json()
    assert tok["token_type"] == "Bearer" and tok["access_token"].startswith("eb_")
    assert resp.headers["cache-control"] == "no-store"

    listed = await anon_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                    headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert listed.status_code == 200 and listed.json()["result"]["tools"]

    # Shows up (and is revocable) under Settings → Agent access, acting as the approver.
    keys = (await client.get("/api-keys")).json()
    assert len(keys) == 1 and keys[0]["name"].startswith("Claude (OAuth")
    assert keys[0]["created_by"] == str(DEFAULT_USER_ID)
    assert (await db_session.execute(text("SELECT oauth_client_id FROM api_keys"))).scalar() == client_id


async def test_code_is_single_use_and_pkce_checked(client, anon_client):
    client_id, code, verifier = await _approved_code(client, anon_client)
    bad = await _exchange(anon_client, client_id, code, "wrong-verifier")
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    # The failed attempt consumed it.
    again = await _exchange(anon_client, client_id, code, verifier)
    assert again.json()["error"] == "invalid_grant"


async def test_code_bound_to_its_client(client, anon_client):
    _, code, verifier = await _approved_code(client, anon_client)
    other = await _register(anon_client, client_name="ChatGPT")
    resp = await _exchange(anon_client, other["client_id"], code, verifier)
    assert resp.json()["error"] == "invalid_grant"


async def test_deny_sends_access_denied_back(client, anon_client):
    reg = await _register(anon_client)
    _, challenge = _pkce()
    request_id = _query((await _authorize(anon_client, reg["client_id"], challenge)).headers["location"])["request"]
    detail = await client.get(f"/oauth/requests/{request_id}")
    assert detail.status_code == 200 and detail.json()["client_name"] == "Claude"
    assert detail.json()["redirect_host"] == "claude.ai"
    resp = await client.post(f"/oauth/requests/{request_id}/decision", json={"approve": False})
    q = _query(resp.json()["redirect_to"])
    assert q["error"] == "access_denied" and q["state"] == "xyz"
    # One decision per request.
    assert (await client.get(f"/oauth/requests/{request_id}")).status_code == 404


async def test_consent_needs_a_signed_in_person(client, anon_client):
    reg = await _register(anon_client)
    _, challenge = _pkce()
    request_id = _query((await _authorize(anon_client, reg["client_id"], challenge)).headers["location"])["request"]
    assert (await anon_client.post(f"/oauth/requests/{request_id}/decision",
                                   json={"approve": True})).status_code == 401
    key = (await client.post("/api-keys", json={"name": "k"})).json()["token"]
    assert (await anon_client.post(f"/oauth/requests/{request_id}/decision", json={"approve": True},
                                   headers={"Authorization": f"Bearer {key}"})).status_code == 403


async def test_authorize_rejects_unregistered_redirect_without_redirecting(anon_client):
    reg = await _register(anon_client)
    _, challenge = _pkce()
    resp = await _authorize(anon_client, reg["client_id"], challenge, redirect_uri="https://evil.example/cb")
    assert resp.status_code == 400 and "location" not in resp.headers


async def test_authorize_requires_s256_pkce(anon_client):
    reg = await _register(anon_client)
    resp = await _authorize(anon_client, reg["client_id"], "abc", code_challenge_method="plain")
    assert resp.status_code == 302
    assert _query(resp.headers["location"])["error"] == "invalid_request"


@pytest.mark.parametrize("uri", ["javascript:alert(1)", "http://example.com/cb", "https://a.example/cb#frag"])
async def test_register_rejects_unsafe_redirect_uris(anon_client, uri):
    resp = await anon_client.post("/oauth/register", json={"redirect_uris": [uri]})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_redirect_uri"


async def test_confidential_client_must_authenticate(client, anon_client):
    reg = await _register(anon_client, token_endpoint_auth_method="client_secret_post")
    assert reg["client_secret"].startswith("ebs_")
    verifier, challenge = _pkce()
    request_id = _query((await _authorize(anon_client, reg["client_id"], challenge)).headers["location"])["request"]
    code = _query((await client.post(f"/oauth/requests/{request_id}/decision",
                                     json={"approve": True})).json()["redirect_to"])["code"]
    no_secret = await _exchange(anon_client, reg["client_id"], code, verifier)
    assert no_secret.status_code == 401 and no_secret.json()["error"] == "invalid_client"
