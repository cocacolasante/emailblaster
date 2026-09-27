"""Workspace integrations: field specs, save/remove, and live "Test" calls.

The storage + resolution primitives live in ``services/credentials.py``;
this module is the management layer behind Settings → Integrations.
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.tenant_keys import TenantProviderKey
from app.services import credentials
from app.tenancy.context import require_tenant_id

_TIMEOUT = 15.0


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    description: str
    fields: tuple[FieldSpec, ...]
    webhook: bool = False   # has an inbound webhook (auto-generated secret)
    docs_url: str = ""


PROVIDERS: dict[str, ProviderSpec] = {p.key: p for p in (
    ProviderSpec(
        "anthropic", "Anthropic",
        "Claude — lead research and AI-written emails, replies and signals.",
        (FieldSpec("api_key", "API key", secret=True, placeholder="sk-ant-…"),),
        docs_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderSpec(
        "brevo", "Brevo",
        "Sends campaign email and reports opens, clicks, bounces and unsubscribes.",
        (
            FieldSpec("api_key", "API key", secret=True, placeholder="xkeysib-…"),
            FieldSpec("sender_email", "Default sender email", placeholder="you@yourdomain.com",
                      help="Must be a verified sender in Brevo."),
            FieldSpec("sender_name", "Default sender name", required=False),
        ),
        webhook=True,
        docs_url="https://app.brevo.com/settings/keys/api",
    ),
    ProviderSpec(
        "hunter", "Hunter",
        "Finds and verifies contact email addresses.",
        (FieldSpec("api_key", "API key", secret=True),),
        docs_url="https://hunter.io/api-keys",
    ),
    ProviderSpec(
        "apollo", "Apollo",
        "Person + company enrichment for deep research and ICP lookalikes.",
        (FieldSpec("api_key", "API key", secret=True),),
        docs_url="https://app.apollo.io/#/settings/integrations/api",
    ),
    ProviderSpec(
        "unipile", "Unipile (LinkedIn)",
        "Runs LinkedIn steps (views, connects, DMs) through hosted browsers.",
        (
            FieldSpec("dsn", "DSN", placeholder="api12.unipile.com:13443",
                      help="Shown at the top of your Unipile dashboard."),
            FieldSpec("api_key", "Access token", secret=True),
        ),
        webhook=True,
        docs_url="https://dashboard.unipile.com",
    ),
    ProviderSpec(
        "adzuna", "Adzuna",
        "Job-posting data for the intent engine's hiring signals.",
        (
            FieldSpec("app_id", "App ID"),
            FieldSpec("app_key", "App key", secret=True),
        ),
        docs_url="https://developer.adzuna.com",
    ),
)}


class IntegrationError(ValueError):
    pass


def webhook_url(provider: str, tenant_id: uuid.UUID) -> str:
    return f"{settings.WEBHOOK_BASE_URL.rstrip('/')}/webhooks/{provider}/{tenant_id}"


def webhook_header(provider: str) -> str:
    return (settings.UNIPILE_WEBHOOK_AUTH_HEADER if provider == "unipile"
            else settings.BREVO_WEBHOOK_AUTH_HEADER)


async def get_row(db: AsyncSession, provider: str) -> TenantProviderKey | None:
    return await db.scalar(
        select(TenantProviderKey).where(
            TenantProviderKey.tenant_id == require_tenant_id(),
            TenantProviderKey.provider == provider,
        )
    )


def stored_fields(row: TenantProviderKey | None) -> dict[str, str]:
    return credentials.decrypt_fields(row.encrypted_credentials) if row else {}


def public_fields(provider: str, data: dict[str, str]) -> dict[str, str]:
    """Non-secret field values, safe to return to the UI."""
    spec = PROVIDERS[provider]
    return {f.key: data.get(f.key, "") for f in spec.fields if not f.secret}


async def save(
    db: AsyncSession, provider: str, incoming: dict[str, Any], *, user_id: uuid.UUID | None,
) -> TenantProviderKey:
    """Create/update a workspace's credentials.  Blank secret fields keep
    the stored value (so the UI never has to echo secrets back)."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise IntegrationError(f"unknown provider {provider!r}")
    row = await get_row(db, provider)
    data = stored_fields(row)
    for f in spec.fields:
        if f.key not in incoming:
            continue
        value = str(incoming.get(f.key) or "").strip()
        if f.secret and not value:
            continue  # keep existing secret
        data[f.key] = value
    missing = [f.label for f in spec.fields if f.required and not data.get(f.key)]
    if missing:
        raise IntegrationError(f"missing: {', '.join(missing)}")
    if spec.webhook and not data.get("webhook_secret"):
        data["webhook_secret"] = secrets.token_hex(32)
    token = credentials.encrypt_fields(data)
    preview = credentials.preview_for(provider, data)
    if row is None:
        row = TenantProviderKey(
            provider=provider, encrypted_credentials=token, preview=preview, updated_by=user_id,
        )
        db.add(row)
    else:
        row.encrypted_credentials = token
        row.preview = preview
        row.updated_by = user_id
        row.last_test_status = None
        row.last_tested_at = None
        row.last_test_error = None
    await db.flush()
    return row


async def rotate_webhook_secret(db: AsyncSession, provider: str) -> str:
    row = await get_row(db, provider)
    if row is None or not PROVIDERS[provider].webhook:
        raise IntegrationError("not configured")
    data = stored_fields(row)
    data["webhook_secret"] = secrets.token_hex(32)
    row.encrypted_credentials = credentials.encrypt_fields(data)
    await db.flush()
    return data["webhook_secret"]


# ---------------------------------------------------------------------------
# Live connection tests — one cheap authenticated call per provider
# ---------------------------------------------------------------------------


async def _probe(provider: str, data: dict[str, str]) -> None:
    """Raise with a human-readable message when the credentials don't work."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if provider == "anthropic":
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": data["api_key"], "anthropic-version": "2023-06-01"},
                params={"limit": 1},
            )
        elif provider == "brevo":
            resp = await client.get(
                "https://api.brevo.com/v3/account",
                headers={"api-key": data["api_key"], "accept": "application/json"},
            )
        elif provider == "hunter":
            resp = await client.get("https://api.hunter.io/v2/account",
                                    params={"api_key": data["api_key"]})
        elif provider == "apollo":
            resp = await client.get("https://api.apollo.io/v1/auth/health",
                                    params={"api_key": data["api_key"]},
                                    headers={"X-Api-Key": data["api_key"]})
            if resp.status_code < 400 and resp.json().get("is_logged_in") is False:
                raise IntegrationError("Apollo rejected the API key")
        elif provider == "unipile":
            dsn = data["dsn"] if "://" in data["dsn"] else f"https://{data['dsn']}"
            resp = await client.get(
                f"{dsn.rstrip('/')}/api/v1/accounts",
                headers={"X-API-KEY": data["api_key"], "accept": "application/json"},
                params={"limit": 1},
            )
        elif provider == "adzuna":
            resp = await client.get(
                f"https://api.adzuna.com/v1/api/jobs/{settings.ADZUNA_COUNTRY}/search/1",
                params={"app_id": data["app_id"], "app_key": data["app_key"],
                        "results_per_page": 1},
            )
        else:  # pragma: no cover — guarded by PROVIDERS
            raise IntegrationError(f"unknown provider {provider!r}")
    if resp.status_code in (401, 403):
        raise IntegrationError(f"{PROVIDERS[provider].label} rejected the credentials "
                               f"(HTTP {resp.status_code})")
    if resp.status_code >= 400:
        raise IntegrationError(f"{PROVIDERS[provider].label} returned HTTP {resp.status_code}")


async def test_connection(db: AsyncSession, provider: str) -> TenantProviderKey:
    """Probe the saved credentials and persist the outcome on the row."""
    row = await get_row(db, provider)
    if row is None:
        raise IntegrationError("not configured")
    error: str | None = None
    try:
        await _probe(provider, stored_fields(row))
    except IntegrationError as exc:
        error = str(exc)
    except httpx.HTTPError as exc:
        error = f"couldn't reach {PROVIDERS[provider].label}: {exc.__class__.__name__}"
    row.last_tested_at = datetime.now(timezone.utc)
    row.last_test_status = "failed" if error else "ok"
    row.last_test_error = error
    await db.flush()
    return row


async def register_unipile_webhooks(db: AsyncSession) -> list[str]:
    """Create the three Unipile webhooks (messaging / account_status /
    users) pointing at this workspace's URL with its secret header.
    Existing webhooks pointing at the same URL are replaced."""
    row = await get_row(db, "unipile")
    if row is None:
        raise IntegrationError("Unipile isn't configured")
    data = stored_fields(row)
    tid = require_tenant_id()
    url = webhook_url("unipile", tid)
    base = data["dsn"] if "://" in data["dsn"] else f"https://{data['dsn']}"
    base = base.rstrip("/")
    headers = {"X-API-KEY": data["api_key"], "accept": "application/json",
               "content-type": "application/json"}
    created: list[str] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        listing = await client.get(f"{base}/api/v1/webhooks", headers=headers)
        if listing.status_code in (401, 403):
            raise IntegrationError("Unipile rejected the credentials")
        items = (listing.json() or {}).get("items", []) if listing.status_code < 400 else []
        for wh in items:
            if wh.get("request_url") == url and wh.get("id"):
                await client.delete(f"{base}/api/v1/webhooks/{wh['id']}", headers=headers)
        for source in ("messaging", "account_status", "users"):
            resp = await client.post(f"{base}/api/v1/webhooks", headers=headers, json={
                "name": f"emailblaster {tid} - {source}",
                "request_url": url,
                "source": source,
                "headers": [
                    {"key": "Content-Type", "value": "application/json"},
                    {"key": settings.UNIPILE_WEBHOOK_AUTH_HEADER, "value": data["webhook_secret"]},
                ],
            })
            if resp.status_code >= 400:
                raise IntegrationError(f"Unipile refused webhook {source!r} (HTTP {resp.status_code})")
            created.append(source)
    return created
