"""Auth, sessions, team management, and the route-level auth gate."""
from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import select, text

from app.auth import passwords
from app.config import settings
from app.models.identity import Invitation, Membership, User
from tests.conftest import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_EMAIL,
    DEFAULT_USER_ID,
    create_test_user,
)


async def _set_password(db_session, user_id, pw="correct-horse-battery"):
    user = await db_session.get(User, user_id)
    user.password_hash = passwords.hash_password(pw)
    await db_session.commit()
    return pw


# --- The gate ---------------------------------------------------------------


async def test_feature_routes_require_auth(anon_client):
    for path in ("/campaigns/", "/leads", "/crm/opportunities", "/settings/api-status"):
        resp = await anon_client.get(path)
        assert resp.status_code == 401, path


async def test_revoked_or_expired_session_rejected(client_factory, _engine):
    uid, tid, token = await create_test_user(_engine, tenant_id=DEFAULT_TENANT_ID)
    c = client_factory(token)
    assert (await c.get("/campaigns/")).status_code == 200
    async with _engine.begin() as conn:
        await conn.execute(text("UPDATE user_sessions SET revoked_at = now() WHERE user_id = :u"), {"u": uid})
    assert (await c.get("/campaigns/")).status_code == 401


async def test_removed_member_session_rejected(client_factory, _engine):
    uid, tid, token = await create_test_user(_engine, tenant_id=DEFAULT_TENANT_ID)
    c = client_factory(token)
    async with _engine.begin() as conn:
        await conn.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": uid})
    assert (await c.get("/campaigns/")).status_code == 401


async def test_suspended_workspace_forbidden(client, _engine):
    async with _engine.begin() as conn:
        await conn.execute(text("UPDATE tenants SET status = 'suspended'"))
    assert (await client.get("/campaigns/")).status_code == 403


def test_every_route_is_authenticated_or_allowlisted():
    """Walk the app's routes: each must depend on get_db / get_identity
    (directly or transitively) unless it's a known public path."""
    from fastapi.routing import APIRoute

    from app.database import get_db, get_identity, get_tenant_context
    from app.main import app

    public_prefixes = ("/auth/", "/webhooks/", "/unsubscribe/", "/health", "/docs", "/openapi", "/redoc")

    def deps(dependant):
        for d in dependant.dependencies:
            yield d.call
            yield from deps(d)

    offenders = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path.startswith(public_prefixes):
            continue
        calls = set(deps(route.dependant))
        if not ({get_db, get_identity, get_tenant_context} & calls):
            offenders.append(f"{sorted(route.methods)} {route.path}")
    assert not offenders, "unauthenticated routes:\n" + "\n".join(offenders)


def test_public_db_only_used_by_allowlisted_modules():
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    allowed = {"database.py", "routers/auth.py", "routers/webhooks.py"}
    offenders = [
        str(p.relative_to(app_dir))
        for p in app_dir.rglob("*.py")
        if str(p.relative_to(app_dir)) not in allowed and "get_public_db" in p.read_text()
    ]
    assert not offenders, offenders


# --- Login / logout / me ----------------------------------------------------


async def test_login_me_logout(anon_client, db_session):
    pw = await _set_password(db_session, DEFAULT_USER_ID)
    resp = await anon_client.post("/auth/login", json={"email": DEFAULT_USER_EMAIL.upper(), "password": pw})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == DEFAULT_USER_EMAIL
    assert body["workspace"]["id"] == str(DEFAULT_TENANT_ID)
    assert body["role"] == "owner"
    assert "eb_session" in resp.cookies

    assert (await anon_client.get("/auth/me")).status_code == 200
    assert (await anon_client.get("/campaigns/")).status_code == 200

    assert (await anon_client.post("/auth/logout")).status_code == 200
    anon_client.cookies.clear()
    assert (await anon_client.get("/auth/me")).status_code == 401


async def test_login_wrong_password_and_unknown_email_same_error(anon_client, db_session):
    await _set_password(db_session, DEFAULT_USER_ID)
    bad = await anon_client.post("/auth/login", json={"email": DEFAULT_USER_EMAIL, "password": "nope-nope"})
    unknown = await anon_client.post("/auth/login", json={"email": "ghost@x.com", "password": "nope-nope"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()


async def test_passwordless_bootstrap_user_cannot_login(anon_client):
    resp = await anon_client.post("/auth/login", json={"email": DEFAULT_USER_EMAIL, "password": "anything1"})
    assert resp.status_code == 401


async def test_change_password_requires_current(client, db_session):
    pw = await _set_password(db_session, DEFAULT_USER_ID)
    bad = await client.patch("/auth/me", json={"current_password": "wrong", "new_password": "brand-new-pass"})
    assert bad.status_code == 400
    ok = await client.patch("/auth/me", json={
        "current_password": pw, "new_password": "brand-new-pass", "name": "Renamed",
    })
    assert ok.status_code == 200 and ok.json()["user"]["name"] == "Renamed"
    # The current session survives a password change.
    assert (await client.get("/auth/me")).status_code == 200


# --- Register ---------------------------------------------------------------


async def test_register_disabled_by_default(anon_client, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SIGNUP", False)
    resp = await anon_client.post("/auth/register", json={"email": "new@x.com", "password": "longenough"})
    assert resp.status_code == 403


async def test_register_creates_workspace_and_logs_in(anon_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SIGNUP", True)
    resp = await anon_client.post("/auth/register", json={
        "email": "Founder@NewCo.com", "password": "longenough", "name": "Fay",
        "workspace_name": "NewCo",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["workspace"]["name"] == "NewCo"
    assert body["role"] == "owner"
    assert body["workspace"]["id"] != str(DEFAULT_TENANT_ID)
    # Logged in to the NEW workspace.
    me = await anon_client.get("/auth/me")
    assert me.json()["workspace"]["name"] == "NewCo"
    dup = await anon_client.post("/auth/register", json={"email": "founder@newco.com", "password": "longenough"})
    assert dup.status_code == 409


# --- Workspace switching ----------------------------------------------------


async def test_switch_workspace(client, _engine, db_session):
    other_tid = uuid.uuid4()
    async with _engine.begin() as conn:
        await conn.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:t, 'Second', 'second')"), {"t": other_tid})
        await conn.execute(text(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'member')"
        ), {"t": other_tid, "u": DEFAULT_USER_ID})
    me = (await client.get("/auth/me")).json()
    assert {m["tenant_name"] for m in me["memberships"]} == {"Test Workspace", "Second"}

    resp = await client.post("/auth/switch-workspace", json={"tenant_id": str(other_tid)})
    assert resp.status_code == 200 and resp.json()["role"] == "member"
    assert (await client.get("/auth/me")).json()["workspace"]["name"] == "Second"

    stranger = uuid.uuid4()
    assert (await client.post("/auth/switch-workspace", json={"tenant_id": str(stranger)})).status_code == 404


# --- Forgot / reset ---------------------------------------------------------


async def test_forgot_and_reset(anon_client, db_session, monkeypatch):
    await _set_password(db_session, DEFAULT_USER_ID)
    captured = {}

    async def fake_send(**kw):
        captured.update(kw)
        return True

    monkeypatch.setattr("app.routers.auth.send_platform_email", fake_send)
    assert (await anon_client.post("/auth/forgot", json={"email": "ghost@x.com"})).status_code == 200
    assert not captured  # unknown email: no mail, same response
    assert (await anon_client.post("/auth/forgot", json={"email": DEFAULT_USER_EMAIL})).status_code == 200
    token = captured["text_body"].split("token=")[1].split()[0]

    resp = await anon_client.post("/auth/reset", json={"token": token, "password": "reset-password-1"})
    assert resp.status_code == 200
    # Single use.
    again = await anon_client.post("/auth/reset", json={"token": token, "password": "reset-password-2"})
    assert again.status_code == 400
    login = await anon_client.post("/auth/login", json={"email": DEFAULT_USER_EMAIL, "password": "reset-password-1"})
    assert login.status_code == 200


async def test_reset_revokes_existing_sessions(client, anon_client, db_session, monkeypatch):
    captured = {}

    async def fake_send(**kw):
        captured.update(kw)
        return True

    monkeypatch.setattr("app.routers.auth.send_platform_email", fake_send)
    await anon_client.post("/auth/forgot", json={"email": DEFAULT_USER_EMAIL})
    token = captured["text_body"].split("token=")[1].split()[0]
    await anon_client.post("/auth/reset", json={"token": token, "password": "reset-password-1"})
    assert (await client.get("/auth/me")).status_code == 401


# --- Team / invites ---------------------------------------------------------


async def test_invite_new_user_flow(client, anon_client, db_session):
    resp = await client.post("/team/invites", json={"email": "New.Hire@X.com", "role": "member"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "new.hire@x.com"
    token = body["invite_url"].split("token=")[1]

    preview = await anon_client.get(f"/auth/invite/{token}")
    assert preview.status_code == 200
    assert preview.json() == {
        "workspace_name": "Test Workspace", "email": "new.hire@x.com",
        "role": "member", "user_exists": False,
    }
    accept = await anon_client.post("/auth/invite/accept", json={
        "token": token, "password": "welcome-aboard", "name": "Nia",
    })
    assert accept.status_code == 200, accept.text
    assert accept.json()["workspace"]["id"] == str(DEFAULT_TENANT_ID)
    assert accept.json()["role"] == "member"
    # Now a member, sees the team, and the invite is consumed.
    members = (await client.get("/team/members")).json()
    assert {m["email"] for m in members} == {DEFAULT_USER_EMAIL, "new.hire@x.com"}
    assert (await anon_client.get(f"/auth/invite/{token}")).status_code == 404


async def test_invite_existing_user_requires_their_password(client, client_factory, _engine, db_session):
    uid, other_tid, _ = await create_test_user(_engine, email="exists@x.com", role="owner")
    await _set_password(db_session, uid, "their-own-pass")
    token = (await client.post("/team/invites", json={"email": "exists@x.com"})).json()["invite_url"].split("token=")[1]
    anon = client_factory(None)
    assert (await anon.get(f"/auth/invite/{token}")).json()["user_exists"] is True
    bad = await anon.post("/auth/invite/accept", json={"token": token, "password": "wrong"})
    assert bad.status_code == 401
    ok = await anon.post("/auth/invite/accept", json={"token": token, "password": "their-own-pass"})
    assert ok.status_code == 200
    names = {m["tenant_name"] for m in ok.json()["memberships"]}
    assert names == {"Other Workspace", "Test Workspace"}


async def test_member_cannot_manage_team(client_factory, _engine):
    _, _, token = await create_test_user(_engine, tenant_id=DEFAULT_TENANT_ID, role="member")
    member = client_factory(token)
    assert (await member.get("/team/members")).status_code == 200
    assert (await member.post("/team/invites", json={"email": "a@b.co"})).status_code == 403
    assert (await member.get("/team/invites")).status_code == 403
    assert (await member.patch("/team/workspace", json={"name": "x"})).status_code == 403
    assert (await member.patch(f"/team/members/{DEFAULT_USER_ID}", json={"role": "member"})).status_code == 403


async def test_admin_cannot_touch_owner_role(client_factory, _engine):
    _, _, token = await create_test_user(_engine, tenant_id=DEFAULT_TENANT_ID, role="admin")
    admin = client_factory(token)
    assert (await admin.patch(f"/team/members/{DEFAULT_USER_ID}", json={"role": "member"})).status_code == 403
    assert (await admin.delete(f"/team/members/{DEFAULT_USER_ID}")).status_code == 403
    assert (await admin.post("/team/invites", json={"email": "o@x.co", "role": "owner"})).status_code == 403


async def test_last_owner_protected(client):
    assert (await client.patch(f"/team/members/{DEFAULT_USER_ID}", json={"role": "admin"})).status_code == 409
    assert (await client.delete(f"/team/members/{DEFAULT_USER_ID}")).status_code == 409


async def test_remove_member_revokes_access(client, client_factory, _engine):
    uid, _, token = await create_test_user(_engine, tenant_id=DEFAULT_TENANT_ID)
    member = client_factory(token)
    assert (await member.get("/campaigns/")).status_code == 200
    assert (await client.delete(f"/team/members/{uid}")).status_code == 204
    assert (await member.get("/campaigns/")).status_code == 401


async def test_member_can_leave(client_factory, _engine, db_session):
    uid, _, token = await create_test_user(_engine, tenant_id=DEFAULT_TENANT_ID)
    member = client_factory(token)
    assert (await member.delete(f"/team/members/{uid}")).status_code == 204
    left = (await db_session.execute(select(Membership).where(Membership.user_id == uid))).first()
    assert left is None


async def test_team_endpoints_scoped_to_active_workspace(client_factory, _engine):
    _, _, token = await create_test_user(_engine, role="owner", email="boss@elsewhere.com")
    other = client_factory(token)
    members = (await other.get("/team/members")).json()
    assert [m["email"] for m in members] == ["boss@elsewhere.com"]
    assert (await other.delete(f"/team/members/{DEFAULT_USER_ID}")).status_code == 404


async def test_reinvite_revokes_previous_link(client, anon_client, db_session):
    first = (await client.post("/team/invites", json={"email": "twice@x.com"})).json()["invite_url"].split("token=")[1]
    await client.post("/team/invites", json={"email": "twice@x.com"})
    assert (await anon_client.get(f"/auth/invite/{first}")).status_code == 404
    live = (await db_session.execute(
        select(Invitation).where(Invitation.email == "twice@x.com", Invitation.revoked_at.is_(None))
    )).scalars().all()
    assert len(live) == 1


# --- CSRF -------------------------------------------------------------------


async def test_cross_origin_state_change_blocked(client):
    resp = await client.post("/campaigns/", json={}, headers={"origin": "https://evil.example"})
    assert resp.status_code == 403
    ok = await client.patch("/team/workspace", json={"name": "Renamed"}, headers={"origin": settings.FRONTEND_URL})
    assert ok.status_code == 200


@pytest.mark.parametrize("path", ["/auth/config"])
async def test_auth_config_is_public(anon_client, path):
    resp = await anon_client.get(path)
    assert resp.status_code == 200 and "allow_signup" in resp.json()
