"""Per-workspace provider credentials: storage, resolution, isolation,
the Integrations API, and per-workspace webhook secrets."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import TenantProviderKey
from app.services import credentials, integrations
from app.tenancy.worker import tenant_context
from tests.conftest import DEFAULT_TENANT_ID, create_test_user


async def _save(client, provider, **fields):
    resp = await client.put(f"/settings/integrations/{provider}", json=fields)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_saved_key_resolves_in_requests_and_workers(client):
    await _save(client, "hunter", api_key="hunter-secret-1234")
    status = (await client.get("/settings/api-status")).json()
    assert status["hunter"] is True and status["anthropic"] is False
    async with tenant_context(DEFAULT_TENANT_ID):
        creds = credentials.get("hunter")
    assert creds is not None and creds.api_key == "hunter-secret-1234"


async def test_no_env_fallback(client, monkeypatch):
    """Even with provider keys in the process env, an unconfigured
    workspace has none."""
    monkeypatch.setitem(os.environ, "ANTHROPIC_API_KEY", "sk-ant-from-env")
    async with tenant_context(DEFAULT_TENANT_ID):
        assert credentials.get("anthropic") is None
    assert credentials.get("anthropic") is None  # no context at all
    assert (await client.get("/settings/api-status")).json()["anthropic"] is False


async def test_missing_credential_maps_to_409(client):
    from app.services import _anthropic

    async with tenant_context(DEFAULT_TENANT_ID):
        try:
            _anthropic.get_client()
            raise AssertionError("expected MissingCredential")
        except credentials.MissingCredential as exc:
            assert exc.provider == "anthropic"


async def test_responses_never_contain_secrets(client, db_session):
    body = await _save(client, "brevo", api_key="xkeysib-super-secret-9876",
                       sender_email="me@acme.com", sender_name="Me")
    assert "super-secret" not in str(body)
    assert body["preview"].startswith("••••9876")
    assert body["values"] == {"sender_email": "me@acme.com", "sender_name": "Me"}
    listing = await client.get("/settings/integrations")
    assert "super-secret" not in listing.text
    row = await db_session.scalar(select(TenantProviderKey))
    assert "super-secret" not in row.encrypted_credentials  # encrypted at rest


async def test_blank_secret_keeps_stored_value(client):
    await _save(client, "brevo", api_key="xkeysib-original-1111", sender_email="a@acme.com")
    await _save(client, "brevo", api_key="", sender_email="b@acme.com")
    async with tenant_context(DEFAULT_TENANT_ID):
        creds = credentials.get("brevo")
    assert creds.api_key == "xkeysib-original-1111" and creds.sender_email == "b@acme.com"


async def test_required_fields_validated(client):
    resp = await client.put("/settings/integrations/brevo", json={"api_key": "k"})
    assert resp.status_code == 422 and "sender" in resp.json()["detail"].lower()
    assert (await client.put("/settings/integrations/nope", json={})).status_code == 404


async def test_webhook_secret_generated_and_revealable(client):
    body = await _save(client, "unipile", dsn="api1.unipile.com:1", api_key="tok-abcdef")
    assert body["webhook_url"].endswith(f"/webhooks/unipile/{DEFAULT_TENANT_ID}")
    secret = (await client.get("/settings/integrations/unipile/webhook-secret")).json()
    assert len(secret["webhook_secret"]) == 64
    rotated = (await client.post("/settings/integrations/unipile/rotate-webhook-secret")).json()
    assert rotated["webhook_secret"] != secret["webhook_secret"]


async def test_workspaces_cannot_see_each_others_keys(client, client_factory, _engine):
    await _save(client, "hunter", api_key="workspace-a-key")
    _, b_tid, b_token = await create_test_user(_engine, role="owner")
    b = client_factory(b_token)
    listing = (await b.get("/settings/integrations")).json()
    assert all(not i["configured"] for i in listing)
    assert (await b.get("/settings/api-status")).json()["hunter"] is False
    async with tenant_context(b_tid):
        assert credentials.get("hunter") is None
    await _save(b, "hunter", api_key="workspace-b-key")
    async with tenant_context(DEFAULT_TENANT_ID):
        assert credentials.get("hunter").api_key == "workspace-a-key"
    async with tenant_context(b_tid):
        assert credentials.get("hunter").api_key == "workspace-b-key"


async def test_members_can_view_but_not_manage(client_factory, _engine):
    _, _, token = await create_test_user(_engine, tenant_id=DEFAULT_TENANT_ID, role="member")
    member = client_factory(token)
    assert (await member.get("/settings/integrations")).status_code == 200
    assert (await member.put("/settings/integrations/hunter", json={"api_key": "x"})).status_code == 403
    assert (await member.delete("/settings/integrations/hunter")).status_code == 403
    assert (await member.get("/settings/integrations/brevo/webhook-secret")).status_code == 403


async def test_delete_removes_key(client):
    await _save(client, "apollo", api_key="apollo-key-1")
    assert (await client.delete("/settings/integrations/apollo")).status_code == 204
    assert (await client.get("/settings/api-status")).json()["apollo"] is False


async def test_connection_test_persists_outcome(client):
    await _save(client, "hunter", api_key="hunter-key")
    with patch.object(integrations, "_probe", AsyncMock(return_value=None)):
        ok = (await client.post("/settings/integrations/hunter/test")).json()
    assert ok["last_test_status"] == "ok" and ok["last_tested_at"]
    with patch.object(integrations, "_probe",
                      AsyncMock(side_effect=integrations.IntegrationError("Hunter rejected the credentials"))):
        bad = (await client.post("/settings/integrations/hunter/test")).json()
    assert bad["last_test_status"] == "failed" and "rejected" in bad["last_test_error"]
    # Re-saving clears the stale test result.
    again = await _save(client, "hunter", api_key="hunter-key-2")
    assert again["last_test_status"] is None


async def test_unipile_webhook_uses_workspace_secret(client, anon_client, _engine):
    await _save(client, "unipile", dsn="api1.unipile.com:1", api_key="tok")
    secret = (await client.get("/settings/integrations/unipile/webhook-secret")).json()["webhook_secret"]
    url = f"/webhooks/unipile/{DEFAULT_TENANT_ID}"
    body = {"type": "something.else", "id": "evt-1"}
    assert (await anon_client.post(url, json=body, headers={"X-Unipile-Auth": "wrong"})).status_code == 401
    ok = await anon_client.post(url, json=body, headers={"X-Unipile-Auth": secret})
    assert ok.status_code == 200 and ok.json().get("ignored") is True
    # Workspace B's URL with A's secret is rejected.
    _, b_tid, b_token = await create_test_user(_engine, role="owner")
    other = await anon_client.post(f"/webhooks/unipile/{b_tid}", json=body,
                                   headers={"X-Unipile-Auth": secret})
    assert other.status_code == 401


async def test_legacy_unipile_url_resolves_workspace_from_account(client, anon_client, db_session):
    from app.models import LinkedInAccount, LinkedInAccountStatus

    await _save(client, "unipile", dsn="api1.unipile.com:1", api_key="tok")
    secret = (await client.get("/settings/integrations/unipile/webhook-secret")).json()["webhook_secret"]
    acc = LinkedInAccount(label="x", linkedin_email="x@x.com", provider_kind="unipile",
                          unipile_account_id="acct-legacy", status=LinkedInAccountStatus.OK)
    db_session.add(acc)
    await db_session.commit()
    body = {"type": "account.checkpoint", "id": "evt-legacy", "account_id": "acct-legacy"}
    resp = await anon_client.post("/webhooks/unipile", json=body, headers={"X-Unipile-Auth": secret})
    assert resp.status_code == 200, resp.text
    await db_session.refresh(acc)
    assert acc.status == LinkedInAccountStatus.CHALLENGED
    unknown = await anon_client.post("/webhooks/unipile", json={**body, "account_id": "nobody", "id": "e2"},
                                     headers={"X-Unipile-Auth": secret})
    assert unknown.status_code == 401
