"""Tests for the /linkedin-accounts router + the LinkedInProvider plumbing.

The actual `linkedin-api` library is monkeypatched at the provider-impl
level — we never make real LinkedIn HTTP calls in tests.
"""
import uuid

import pytest

from app.models import LinkedInAccount, LinkedInAccountStatus
from app.services import encryption
from app.services.linkedin.base import ChallengeRequired, ActionResult


def _payload(**overrides):
    base = {
        "label": "throwaway",
        "linkedin_email": "throwaway@example.com",
        "password": "test-password",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


async def test_create_linkedin_account(client, db_session):
    r = await client.post("/linkedin-accounts/", json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["label"] == "throwaway"
    assert body["linkedin_email"] == "throwaway@example.com"
    assert body["status"] == "untested"
    assert "password" not in body  # never echoed
    # Verify password is encrypted in DB.
    acc = await db_session.get(LinkedInAccount, uuid.UUID(body["id"]))
    assert acc.password_encrypted != "test-password"
    assert encryption.decrypt(acc.password_encrypted) == "test-password"


async def test_list_empty(client):
    r = await client.get("/linkedin-accounts/")
    assert r.status_code == 200
    assert r.json() == []


async def test_get_and_404(client):
    r = await client.post("/linkedin-accounts/", json=_payload())
    aid = r.json()["id"]
    r2 = await client.get(f"/linkedin-accounts/{aid}")
    assert r2.status_code == 200
    r3 = await client.get(f"/linkedin-accounts/{uuid.uuid4()}")
    assert r3.status_code == 404


async def test_patch_password_clears_cookies(client, db_session):
    r = await client.post("/linkedin-accounts/", json=_payload())
    aid = r.json()["id"]
    acc = await db_session.get(LinkedInAccount, uuid.UUID(aid))
    # Pretend a prior session existed.
    acc.session_cookies_encrypted = encryption.encrypt("[]")
    acc.status = LinkedInAccountStatus.OK
    await db_session.commit()

    r2 = await client.patch(f"/linkedin-accounts/{aid}", json={"password": "new-pass"})
    assert r2.status_code == 200
    await db_session.refresh(acc)
    assert acc.session_cookies_encrypted is None
    assert acc.status == LinkedInAccountStatus.UNTESTED
    assert encryption.decrypt(acc.password_encrypted) == "new-pass"


async def test_delete(client):
    r = await client.post("/linkedin-accounts/", json=_payload())
    aid = r.json()["id"]
    r2 = await client.delete(f"/linkedin-accounts/{aid}")
    assert r2.status_code == 204
    r3 = await client.get(f"/linkedin-accounts/{aid}")
    assert r3.status_code == 404


# --------------------------------------------------------------------------
# /test + /resolve-challenge
# --------------------------------------------------------------------------


async def test_test_endpoint_success(client, db_session, monkeypatch):
    """Provider returns OK → account flips to OK, cookies persisted."""
    r = await client.post("/linkedin-accounts/", json=_payload())
    aid = r.json()["id"]

    # Patch at the router's get_provider call-site so this works regardless
    # of which concrete provider is configured (Playwright or HTTP).
    async def fake_test_connection(account):
        account.session_cookies_encrypted = encryption.encrypt(
            '{"cookies":[{"name":"li_at","value":"x"}],"origins":[]}'
        )
        return ActionResult(ok=True)

    from unittest.mock import MagicMock
    mock_prov = MagicMock()
    mock_prov.test_connection = fake_test_connection

    import app.routers.linkedin_accounts as _router
    monkeypatch.setattr(_router, "get_provider", lambda: mock_prov)

    rt = await client.post(f"/linkedin-accounts/{aid}/test")
    assert rt.status_code == 200, rt.text
    body = rt.json()
    assert body["ok"] is True
    assert body["status"] == "ok"

    acc = await db_session.get(LinkedInAccount, uuid.UUID(aid))
    assert acc.status == LinkedInAccountStatus.OK
    assert acc.session_cookies_encrypted is not None


async def test_test_endpoint_challenged(client, db_session, monkeypatch):
    r = await client.post("/linkedin-accounts/", json=_payload())
    aid = r.json()["id"]

    async def fake_test_connection(account):
        raise ChallengeRequired(
            "captcha required", challenge_url="https://www.linkedin.com/checkpoint/x",
        )

    from unittest.mock import MagicMock
    mock_prov = MagicMock()
    mock_prov.test_connection = fake_test_connection

    import app.routers.linkedin_accounts as _router
    monkeypatch.setattr(_router, "get_provider", lambda: mock_prov)

    rt = await client.post(f"/linkedin-accounts/{aid}/test")
    body = rt.json()
    assert body["ok"] is False
    assert body["status"] == "challenged"
    assert body["challenge_url"].startswith("https://www.linkedin.com/checkpoint")

    acc = await db_session.get(LinkedInAccount, uuid.UUID(aid))
    assert acc.status == LinkedInAccountStatus.CHALLENGED
    assert acc.pending_challenge_url is not None


async def test_resolve_challenge_flips_back_to_untested(client, db_session):
    r = await client.post("/linkedin-accounts/", json=_payload())
    aid = r.json()["id"]
    acc = await db_session.get(LinkedInAccount, uuid.UUID(aid))
    acc.status = LinkedInAccountStatus.CHALLENGED
    acc.pending_challenge_url = "https://example.com/check"
    acc.last_error = "captcha required"
    await db_session.commit()

    r2 = await client.post(f"/linkedin-accounts/{aid}/resolve-challenge", json={})
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "untested"
    assert body["pending_challenge_url"] is None
