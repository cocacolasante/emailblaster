"""Workspace settings: integration credentials + provider status.

Secrets are write-only through this API: responses carry a masked
preview and non-secret fields only.  Members can view status; owners and
admins manage keys.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Identity, get_identity, require_manager
from app.database import get_db
from app.services import agent_core, brevo_blocklist, credentials, integrations

router = APIRouter(prefix="/settings", tags=["settings"])


class ApiStatusResponse(BaseModel):
    anthropic: bool
    brevo: bool
    apollo: bool
    hunter: bool


@router.get("/api-status", response_model=ApiStatusResponse)
async def get_api_status() -> ApiStatusResponse:
    """Returns a bool per integration: True if the key is configured (non-empty)."""
    return ApiStatusResponse(
        anthropic=credentials.is_configured("anthropic"),
        brevo=credentials.is_configured("brevo"),
        apollo=credentials.is_configured("apollo"),
        hunter=credentials.is_configured("hunter"),
    )


class BlocklistSyncResponse(BaseModel):
    fetched: int
    newly_suppressed: int
    already_suppressed: int
    leads_halted: int
    leads_removed: int


@router.post("/brevo/sync-blocklist", response_model=BlocklistSyncResponse)
async def sync_brevo_blocklist(db: AsyncSession = Depends(get_db)) -> BlocklistSyncResponse:
    """Pull Brevo's blocked-contacts list (hard bounces / unsubscribes / spam /
    admin-blocked) and suppress each: add to the ignore list, halt them in
    current campaigns, and block them from future ones.  Idempotent — safe to
    re-run.  Runs the same logic as the scheduled backstop, on demand."""
    if not credentials.is_configured("brevo"):
        raise HTTPException(status_code=400, detail=str(credentials.MissingCredential("brevo")))
    result = await brevo_blocklist.sync_blocklist(db)
    return BlocklistSyncResponse(
        fetched=result.fetched,
        newly_suppressed=result.newly_suppressed,
        already_suppressed=result.already_suppressed,
        leads_halted=result.leads_halted,
        leads_removed=result.leads_removed,
    )


# ---------------------------------------------------------------------------
# Integrations (per-workspace provider credentials)
# ---------------------------------------------------------------------------


class FieldOut(BaseModel):
    key: str
    label: str
    secret: bool
    required: bool
    placeholder: str
    help: str


class IntegrationOut(BaseModel):
    provider: str
    label: str
    description: str
    docs_url: str
    fields: list[FieldOut]
    configured: bool
    preview: str | None = None
    values: dict[str, str] = {}          # non-secret field values only
    last_test_status: str | None = None
    last_tested_at: datetime | None = None
    last_test_error: str | None = None
    updated_at: datetime | None = None
    webhook_url: str | None = None
    webhook_header: str | None = None


class IntegrationSecretOut(BaseModel):
    webhook_secret: str
    webhook_url: str
    webhook_header: str


def _integration_out(provider: str, row, tenant_id: uuid.UUID) -> IntegrationOut:
    spec = integrations.PROVIDERS[provider]
    data = integrations.stored_fields(row) if row is not None else {}
    return IntegrationOut(
        provider=provider,
        label=spec.label,
        description=spec.description,
        docs_url=spec.docs_url,
        fields=[FieldOut(**f.__dict__) for f in spec.fields],
        configured=row is not None,
        preview=row.preview if row is not None else None,
        values=integrations.public_fields(provider, data),
        last_test_status=row.last_test_status if row is not None else None,
        last_tested_at=row.last_tested_at if row is not None else None,
        last_test_error=row.last_test_error if row is not None else None,
        updated_at=row.updated_at if row is not None else None,
        webhook_url=integrations.webhook_url(provider, tenant_id) if spec.webhook else None,
        webhook_header=integrations.webhook_header(provider) if spec.webhook else None,
    )


def _provider_or_404(provider: str) -> str:
    if provider not in integrations.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
    return provider


@router.get("/integrations", response_model=list[IntegrationOut])
async def list_integrations(
    identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db),
) -> list[IntegrationOut]:
    out = []
    for provider in integrations.PROVIDERS:
        row = await integrations.get_row(db, provider)
        out.append(_integration_out(provider, row, identity.tenant_id))
    return out


@router.put("/integrations/{provider}", response_model=IntegrationOut)
async def save_integration(
    provider: str,
    body: dict[str, Any],
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> IntegrationOut:
    """Save credentials.  Omitted or blank secret fields keep the stored
    value.  Webhook secrets (Brevo, Unipile) are generated automatically."""
    _provider_or_404(provider)
    try:
        row = await integrations.save(db, provider, body, user_id=identity.user_id)
    except integrations.IntegrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(row)
    return _integration_out(provider, row, identity.tenant_id)


@router.delete("/integrations/{provider}", status_code=204, response_model=None)
async def delete_integration(
    provider: str,
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    _provider_or_404(provider)
    row = await integrations.get_row(db, provider)
    if row is not None:
        await db.delete(row)
        await db.commit()


@router.post("/integrations/{provider}/test", response_model=IntegrationOut)
async def test_integration(
    provider: str,
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> IntegrationOut:
    _provider_or_404(provider)
    try:
        row = await integrations.test_connection(db, provider)
    except integrations.IntegrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(row)
    return _integration_out(provider, row, identity.tenant_id)


@router.get("/integrations/{provider}/webhook-secret", response_model=IntegrationSecretOut)
async def reveal_webhook_secret(
    provider: str,
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> IntegrationSecretOut:
    """The inbound-webhook secret, for pasting into the provider's
    dashboard (managers only)."""
    _provider_or_404(provider)
    if not integrations.PROVIDERS[provider].webhook:
        raise HTTPException(status_code=404, detail="this provider has no webhook")
    row = await integrations.get_row(db, provider)
    if row is None:
        raise HTTPException(status_code=409, detail="not configured")
    return IntegrationSecretOut(
        webhook_secret=integrations.stored_fields(row).get("webhook_secret", ""),
        webhook_url=integrations.webhook_url(provider, identity.tenant_id),
        webhook_header=integrations.webhook_header(provider),
    )


@router.post("/integrations/{provider}/rotate-webhook-secret", response_model=IntegrationSecretOut)
async def rotate_webhook_secret(
    provider: str,
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> IntegrationSecretOut:
    _provider_or_404(provider)
    try:
        secret = await integrations.rotate_webhook_secret(db, provider)
    except integrations.IntegrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return IntegrationSecretOut(
        webhook_secret=secret,
        webhook_url=integrations.webhook_url(provider, identity.tenant_id),
        webhook_header=integrations.webhook_header(provider),
    )


@router.post("/integrations/unipile/register-webhooks")
async def register_unipile_webhooks(
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create this workspace's three Unipile webhooks via the Unipile API."""
    try:
        sources = await integrations.register_unipile_webhooks(db)
    except integrations.IntegrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"registered": sources, "url": integrations.webhook_url("unipile", identity.tenant_id)}


# --------------------------------------------------------------------------
# Deliverability breaker (per workspace)
# --------------------------------------------------------------------------

# Bounds for the workspace's hard-bounce trip rate.  Below 1% one unlucky
# list trips constantly; above 30% the breaker no longer protects anything.
MIN_BOUNCE_THRESHOLD = 0.01
MAX_BOUNCE_THRESHOLD = 0.30


class DeliverabilitySettingsOut(BaseModel):
    enabled: bool
    bounce_threshold: float
    default_bounce_threshold: float
    custom: bool
    spam_threshold: float
    min_sample: int
    window_hours: int


class DeliverabilitySettingsUpdate(BaseModel):
    # A fraction (0.06 = 6%); null resets to the default.
    bounce_threshold: float | None = Field(default=None, ge=MIN_BOUNCE_THRESHOLD, le=MAX_BOUNCE_THRESHOLD)


async def _deliverability_out(db: AsyncSession) -> DeliverabilitySettingsOut:
    from app.config import settings as app_settings

    row = await agent_core.get_agent_settings(db)
    custom = row.breaker_bounce_rate
    return DeliverabilitySettingsOut(
        enabled=app_settings.CIRCUIT_BREAKER_ENABLED,
        bounce_threshold=float(custom) if custom is not None else app_settings.CIRCUIT_BREAKER_BOUNCE_RATE,
        default_bounce_threshold=app_settings.CIRCUIT_BREAKER_BOUNCE_RATE,
        custom=custom is not None,
        spam_threshold=app_settings.CIRCUIT_BREAKER_SPAM_RATE,
        min_sample=app_settings.CIRCUIT_BREAKER_MIN_SAMPLE,
        window_hours=app_settings.CIRCUIT_BREAKER_WINDOW_HOURS,
    )


@router.get("/deliverability", response_model=DeliverabilitySettingsOut)
async def get_deliverability_settings(db: AsyncSession = Depends(get_db)) -> DeliverabilitySettingsOut:
    out = await _deliverability_out(db)
    await db.commit()  # persists the lazily created settings row
    return out


@router.put("/deliverability", response_model=DeliverabilitySettingsOut)
async def update_deliverability_settings(
    body: DeliverabilitySettingsUpdate,
    _: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> DeliverabilitySettingsOut:
    """Owners/admins set the bounce rate that auto-pauses this workspace's
    campaigns.  Applies on the next health check; campaigns already paused
    stay paused until someone resumes them."""
    row = await agent_core.get_agent_settings(db)
    row.breaker_bounce_rate = (
        None if body.bounce_threshold is None else Decimal(str(round(body.bounce_threshold, 4)))
    )
    await db.commit()
    return await _deliverability_out(db)
