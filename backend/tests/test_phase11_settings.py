"""Phase 11: GET /settings/api-status."""
from app.config import settings


async def test_api_status_returns_all_four_keys(client):
    resp = await client.get("/settings/api-status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"anthropic", "brevo", "apollo", "hunter"}
    for v in body.values():
        assert isinstance(v, bool)


async def test_api_status_reflects_settings_values(set_creds, client, monkeypatch):
    set_creds("anthropic", api_key="set")
    set_creds("brevo", api_key="")
    set_creds("apollo", api_key="set")
    set_creds("hunter", api_key="")

    body = (await client.get("/settings/api-status")).json()
    assert body == {"anthropic": True, "brevo": False, "apollo": True, "hunter": False}


async def test_api_status_never_returns_actual_key_values(set_creds, client, monkeypatch):
    set_creds("anthropic", api_key="sk-ant-secret-xyz")
    set_creds("brevo", api_key="brevo-secret")
    set_creds("apollo", api_key="apollo-secret")
    set_creds("hunter", api_key="hunter-secret")

    text = (await client.get("/settings/api-status")).text
    assert "sk-ant-secret-xyz" not in text
    assert "brevo-secret" not in text
    assert "apollo-secret" not in text
    assert "hunter-secret" not in text
