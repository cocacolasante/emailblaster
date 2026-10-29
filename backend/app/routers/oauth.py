"""OAuth 2.1 for MCP clients — Claude, ChatGPT and anything else that only
connects with OAuth, not a pasted bearer key.

The flow is the one the MCP authorization spec prescribes:

1. ``POST /mcp`` without a token → 401 whose ``WWW-Authenticate`` points at
   ``/.well-known/oauth-protected-resource`` (RFC 9728).
2. That names this server as the authorization server, whose metadata
   (RFC 8414) lists the endpoints below.
3. The client registers itself (``/oauth/register``, RFC 7591) and sends the
   user's browser to ``/oauth/authorize`` with a PKCE challenge.
4. We park the request in Redis and redirect to the dashboard's
   ``/connect-agent`` page — the normal login (if needed), then a consent
   screen.  Approving issues a one-time code back to the client's
   ``redirect_uri``.
5. ``/oauth/token`` checks the code + PKCE verifier and returns an access
   token, which is simply a new workspace API key acting as the approving
   member in their active workspace.  From there it is the API-key path
   unchanged: revoke it in Settings → Agent access like any other key.

Tokens don't expire (like pasted keys), so no refresh tokens are issued.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import ratelimit
from app.auth.deps import Identity, require_session
from app.config import settings
from app.database import get_public_db, get_session_factory
from app.models.api_key import ApiKey
from app.models.oauth_client import OAuthClient
from app.routers.auth import _client_ip
from app.services import api_keys
from app.tenancy.context import tenant_scope

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

SCOPE = "mcp"
REQUEST_TTL_SECONDS = 15 * 60  # time to log in + approve
CODE_TTL_SECONDS = 5 * 60
_MAX_REDIRECT_URIS = 10
# Schemes a client may never redirect to (custom app schemes like
# ``cursor://`` are fine; these would run code or read files).
_FORBIDDEN_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob"}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def public_origin() -> str:
    """The HTTPS origin agents reach us at (issuer + resource base)."""
    return (settings.PUBLIC_ORIGIN or settings.WEBHOOK_BASE_URL).rstrip("/")


def resource_url() -> str:
    return f"{public_origin()}/mcp"


def resource_metadata_url() -> str:
    return f"{public_origin()}/.well-known/oauth-protected-resource/mcp"


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers={"Cache-Control": "no-store"},
    )


def _redirect_with(redirect_uri: str, params: dict[str, str]) -> str:
    sep = "&" if urlsplit(redirect_uri).query else "?"
    return f"{redirect_uri}{sep}{urlencode({k: v for k, v in params.items() if v is not None})}"


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _protected_resource() -> dict:
    return {
        "resource": resource_url(),
        "authorization_servers": [public_origin()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [SCOPE],
        "resource_name": "Email Blaster",
    }


def _authorization_server() -> dict:
    origin = public_origin()
    return {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/oauth/authorize",
        "token_endpoint": f"{origin}/oauth/token",
        "registration_endpoint": f"{origin}/oauth/register",
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
    }


# RFC 9728 inserts the resource path after the well-known prefix; clients
# also try the bare form, so serve both.
@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata() -> dict:
    return _protected_resource()


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
@router.get("/.well-known/openid-configuration")
async def authorization_server_metadata() -> dict:
    return _authorization_server()


# --------------------------------------------------------------------------
# Dynamic client registration (RFC 7591)
# --------------------------------------------------------------------------


def _valid_redirect_uri(uri: str) -> bool:
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if not scheme or parts.fragment or scheme in _FORBIDDEN_SCHEMES:
        return False
    if scheme == "https":
        return bool(parts.hostname)
    if scheme == "http":  # only for native apps listening on loopback
        return (parts.hostname or "") in _LOOPBACK_HOSTS
    return True  # private-use scheme for a native app (e.g. cursor://)


@router.post("/oauth/register", status_code=201)
async def register_client(request: Request, db: AsyncSession = Depends(get_public_db)) -> JSONResponse:
    await ratelimit.hit(f"oauth-register:{_client_ip(request)}", limit=30, window_seconds=3600)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _oauth_error("invalid_client_metadata", "body must be JSON")
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata", "body must be a JSON object")

    uris = body.get("redirect_uris")
    if (not isinstance(uris, list) or not uris or len(uris) > _MAX_REDIRECT_URIS
            or not all(isinstance(u, str) and _valid_redirect_uri(u) for u in uris)):
        return _oauth_error("invalid_redirect_uri",
                            "redirect_uris must be https URLs (or loopback / app-scheme URIs for native apps)")
    method = body.get("token_endpoint_auth_method") or "none"
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        return _oauth_error("invalid_client_metadata", f"unsupported token_endpoint_auth_method {method!r}")
    grants = body.get("grant_types") or ["authorization_code"]
    if "authorization_code" not in grants:
        return _oauth_error("invalid_client_metadata", "only the authorization_code grant is supported")
    name = str(body.get("client_name") or "").strip()[:80] or urlsplit(uris[0]).hostname or "MCP client"

    client_id = "ebc_" + secrets.token_urlsafe(18)
    secret = "ebs_" + secrets.token_urlsafe(32) if method != "none" else None
    db.add(OAuthClient(
        client_id=client_id, client_secret_hash=_sha256(secret) if secret else None,
        client_name=name, redirect_uris=uris, token_endpoint_auth_method=method,
    ))
    await db.commit()
    out: dict[str, Any] = {
        "client_id": client_id,
        "client_id_issued_at": int(datetime.now(timezone.utc).timestamp()),
        "client_name": name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
        "scope": SCOPE,
    }
    if secret:
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    return JSONResponse(status_code=201, content=out, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------
# Authorization (browser) → consent page in the dashboard
# --------------------------------------------------------------------------


@router.get("/oauth/authorize")
async def authorize(request: Request, db: AsyncSession = Depends(get_public_db)):
    q = request.query_params
    client = await db.get(OAuthClient, q.get("client_id") or "")
    redirect_uri = q.get("redirect_uri") or ""
    # Until client + redirect_uri check out, errors can't go back to the
    # client (that would make us an open redirector) — show them here.
    if client is None:
        return _oauth_error("invalid_client", "unknown client_id — reconnect the app to register it again")
    if not redirect_uri and len(client.redirect_uris) == 1:
        redirect_uri = client.redirect_uris[0]
    if redirect_uri not in client.redirect_uris:
        return _oauth_error("invalid_request", "redirect_uri does not match the registered redirect URIs")

    state = q.get("state")

    def back(error: str, description: str) -> RedirectResponse:
        return RedirectResponse(
            _redirect_with(redirect_uri, {"error": error, "error_description": description, "state": state}),
            status_code=302,
        )

    if q.get("response_type") != "code":
        return back("unsupported_response_type", "only response_type=code is supported")
    challenge = q.get("code_challenge") or ""
    if not challenge or q.get("code_challenge_method", "plain") != "S256":
        return back("invalid_request", "PKCE with code_challenge_method=S256 is required")
    resource = q.get("resource")
    if resource and not resource.rstrip("/").startswith(public_origin()):
        return back("invalid_target", f"this server only issues tokens for {resource_url()}")

    request_id = secrets.token_urlsafe(24)
    pending = {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "resource": resource,
    }
    r = _redis()
    try:
        await r.set(f"oauth:req:{request_id}", json.dumps(pending), ex=REQUEST_TTL_SECONDS)
    finally:
        await r.aclose()
    return RedirectResponse(
        f"{settings.FRONTEND_URL.rstrip('/')}/connect-agent?{urlencode({'request': request_id})}",
        status_code=302,
    )


async def _load_request(request_id: str) -> dict:
    r = _redis()
    try:
        raw = await r.get(f"oauth:req:{request_id}")
    finally:
        await r.aclose()
    if not raw:
        raise HTTPException(status_code=404, detail="This connection request expired. Start connecting again from the app.")
    return json.loads(raw)


class ConnectRequestOut(BaseModel):
    client_name: str
    redirect_host: str
    workspace_name: str
    user_email: str


@router.get("/oauth/requests/{request_id}", response_model=ConnectRequestOut)
async def connect_request(request_id: str, identity: Identity = Depends(require_session)) -> ConnectRequestOut:
    pending = await _load_request(request_id)
    return ConnectRequestOut(
        client_name=pending["client_name"],
        redirect_host=urlsplit(pending["redirect_uri"]).hostname or pending["redirect_uri"],
        workspace_name=identity.tenant.name,
        user_email=identity.user.email,
    )


class Decision(BaseModel):
    approve: bool


@router.post("/oauth/requests/{request_id}/decision")
async def decide(
    request_id: str, body: Decision, identity: Identity = Depends(require_session),
) -> dict:
    """Approve or deny; returns where to send the browser next."""
    pending = await _load_request(request_id)
    r = _redis()
    try:
        await r.delete(f"oauth:req:{request_id}")  # one decision per request
        if not body.approve:
            return {"redirect_to": _redirect_with(pending["redirect_uri"], {
                "error": "access_denied", "error_description": "The user declined.",
                "state": pending["state"],
            })}
        code = secrets.token_urlsafe(32)
        await r.set(f"oauth:code:{_sha256(code)}", json.dumps({
            **pending,
            "tenant_id": str(identity.tenant_id),
            "user_id": str(identity.user_id),
        }), ex=CODE_TTL_SECONDS)
    finally:
        await r.aclose()
    logger.info("oauth: %s approved %s for workspace %s",
                identity.user.email, pending["client_name"], identity.tenant_id)
    return {"redirect_to": _redirect_with(pending["redirect_uri"], {"code": code, "state": pending["state"]})}


# --------------------------------------------------------------------------
# Token exchange
# --------------------------------------------------------------------------


def _client_credentials(request: Request, form) -> tuple[str | None, str | None]:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("basic "):
        try:
            cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
            return cid, secret
        except Exception:  # noqa: BLE001
            return None, None
    return form.get("client_id"), form.get("client_secret")


def _pkce_matches(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(expected, challenge)


@router.post("/oauth/token")
async def token(
    request: Request,
    db: AsyncSession = Depends(get_public_db),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> JSONResponse:
    await ratelimit.hit(f"oauth-token:{_client_ip(request)}", limit=60, window_seconds=600)
    form = await request.form()
    if form.get("grant_type") != "authorization_code":
        return _oauth_error("unsupported_grant_type", "only grant_type=authorization_code is supported")

    client_id, secret = _client_credentials(request, form)
    client = await db.get(OAuthClient, client_id or "")
    if client is None:
        return _oauth_error("invalid_client", "unknown client", status=401)
    if client.client_secret_hash and not (
        secret and secrets.compare_digest(_sha256(secret), client.client_secret_hash)
    ):
        return _oauth_error("invalid_client", "client authentication failed", status=401)

    code = form.get("code") or ""
    r = _redis()
    try:
        raw = await r.getdel(f"oauth:code:{_sha256(code)}")  # single use
    finally:
        await r.aclose()
    if not raw:
        return _oauth_error("invalid_grant", "authorization code is invalid, expired or already used")
    grant = json.loads(raw)
    if grant["client_id"] != client.client_id:
        return _oauth_error("invalid_grant", "code was issued to a different client")
    if (form.get("redirect_uri") or grant["redirect_uri"]) != grant["redirect_uri"]:
        return _oauth_error("invalid_grant", "redirect_uri does not match the authorization request")
    if not _pkce_matches(form.get("code_verifier") or "", grant["code_challenge"]):
        return _oauth_error("invalid_grant", "PKCE verification failed")

    access_token, prefix, token_hash = api_keys.mint()
    tenant_id, user_id = uuid.UUID(grant["tenant_id"]), uuid.UUID(grant["user_id"])
    label = f"{client.client_name} (OAuth, {datetime.now(timezone.utc):%b %-d})"
    with tenant_scope(tenant_id, user_id):
        async with factory() as tdb:
            tdb.add(ApiKey(name=label, prefix=prefix, token_hash=token_hash,
                           created_by=user_id, oauth_client_id=client.client_id))
            await tdb.commit()
    return JSONResponse(
        {"access_token": access_token, "token_type": "Bearer", "scope": SCOPE},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
