from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import LinkedInAccount, LinkedInAccountStatus
from app.schemas.linkedin_account import (
    ConnectViaUnipileRequest,
    ConnectViaUnipileResponse,
    LinkedInAccountCreate,
    LinkedInAccountResponse,
    LinkedInAccountUpdate,
    LinkedInTestResponse,
    ResolveChallengeRequest,
)
from app.services import encryption
from app.services.linkedin import get_provider
from app.services.linkedin.base import ChallengeRequired, AccountRestricted
from app.services.linkedin.playwright_impl import clear_profile
from app.services.linkedin.unipile_impl import UnipileError, UnipileLinkedInProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linkedin-accounts", tags=["linkedin-accounts"])


def _li_at_to_cookie_blob(li_at: str) -> str:
    """Wrap a bare li_at token into the JSON format used by session_cookies_encrypted."""
    import json
    return json.dumps([
        {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"},
    ])


async def _get_or_404(db: AsyncSession, account_id: uuid.UUID) -> LinkedInAccount:
    acc = await db.get(LinkedInAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="LinkedIn account not found")
    return acc


@router.post(
    "/", response_model=LinkedInAccountResponse, status_code=status.HTTP_201_CREATED,
)
async def create_account(
    payload: LinkedInAccountCreate, db: AsyncSession = Depends(get_db)
) -> LinkedInAccount:
    """Create a DIY (Playwright / linkedin-api) LinkedIn account row.

    The new Unipile flow uses POST /connect-via-unipile instead; this
    endpoint is the legacy create path that requires email + password.
    Kept for backward compat and dev/test workflows.
    """
    if not payload.linkedin_email or not payload.password:
        raise HTTPException(
            status_code=422,
            detail=(
                "DIY accounts require linkedin_email + password. "
                "For Unipile-managed accounts, use POST /linkedin-accounts/connect-via-unipile."
            ),
        )
    acc = LinkedInAccount(
        label=payload.label,
        linkedin_email=payload.linkedin_email,
        password_encrypted=encryption.encrypt(payload.password),
        proxy_url=payload.proxy_url or None,
        provider_kind="diy",
    )
    if payload.li_at_cookie and payload.li_at_cookie.strip():
        acc.session_cookies_encrypted = encryption.encrypt(
            _li_at_to_cookie_blob(payload.li_at_cookie.strip())
        )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


@router.post(
    "/connect-via-unipile",
    response_model=ConnectViaUnipileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def connect_via_unipile(
    payload: ConnectViaUnipileRequest, db: AsyncSession = Depends(get_db),
) -> ConnectViaUnipileResponse:
    """Begin a Unipile hosted-auth flow.

    Creates a placeholder ``LinkedInAccount`` row with ``provider_kind='unipile'``
    and asks Unipile for a hosted login URL.  The frontend opens that URL
    in a new tab; Unipile drives the LinkedIn login; on success Unipile
    fires the ``account.connected`` webhook with this account's local id
    encoded in the ``name`` field so our webhook handler can correlate.

    The placeholder row carries no ``linkedin_email`` until Unipile reports
    back — we pull it from the webhook payload and persist it then.
    """
    if not settings.UNIPILE_API_KEY or not settings.UNIPILE_DSN:
        raise HTTPException(
            status_code=503,
            detail="Unipile not configured — set UNIPILE_DSN + UNIPILE_API_KEY.",
        )

    acc = LinkedInAccount(
        label=payload.label,
        linkedin_email="(pending Unipile auth)",
        password_encrypted=None,
        provider_kind="unipile",
        status=LinkedInAccountStatus.UNTESTED,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)

    # The "name" we pass through to Unipile is our local account id —
    # Unipile echoes it back in webhook events so we can correlate which
    # Unipile account_id belongs to which row.
    provider = UnipileLinkedInProvider()
    try:
        notify_url = f"{settings.WEBHOOK_BASE_URL.rstrip('/')}/webhooks/unipile"
        link = await provider.create_hosted_auth_link(
            success_redirect_url=payload.success_redirect_url,
            failure_redirect_url=payload.failure_redirect_url,
            notify_url=notify_url,
            name=str(acc.id),
        )
    except UnipileError as exc:
        # Roll back the placeholder row so we don't leave orphans.
        await db.delete(acc)
        await db.commit()
        raise HTTPException(status_code=502, detail=f"Unipile error: {exc}") from exc

    hosted_url = link.get("url") or link.get("hosted_url") or ""
    if not hosted_url:
        await db.delete(acc)
        await db.commit()
        raise HTTPException(
            status_code=502, detail="Unipile returned no hosted URL",
        )
    return ConnectViaUnipileResponse(account_id=acc.id, hosted_url=hosted_url)


@router.post("/{account_id}/sync-unipile", response_model=LinkedInAccountResponse)
async def sync_unipile_status(
    account_id: uuid.UUID, db: AsyncSession = Depends(get_db),
) -> LinkedInAccount:
    """Polling fallback for the hosted-auth flow.

    If the user finishes Unipile's flow but the webhook hasn't reached us
    (dev without a public URL, transient delivery issue, etc.), the
    frontend can call this to pull the current account status straight
    from Unipile.  Idempotent: if the row already has a unipile_account_id
    we just refresh its status.
    """
    acc = await _get_or_404(db, account_id)
    if acc.provider_kind != "unipile":
        raise HTTPException(
            status_code=422, detail="This account is not Unipile-managed.",
        )
    if not acc.unipile_account_id:
        # Hosted flow hasn't completed yet — nothing to sync.  The frontend
        # should keep polling.
        return acc
    provider = UnipileLinkedInProvider()
    try:
        status_payload = await provider.fetch_account_status(acc.unipile_account_id)
    except UnipileError as exc:
        acc.last_error = str(exc)
        await db.commit()
        await db.refresh(acc)
        return acc
    _apply_unipile_status(acc, status_payload)
    await db.commit()
    await db.refresh(acc)
    return acc


def _apply_unipile_status(acc: LinkedInAccount, payload: dict) -> None:
    """Translate a Unipile account-status dict onto our model fields."""
    src = str(payload.get("status") or payload.get("connection_status") or "").upper()
    if src in {"OK", "CONNECTED", "ACTIVE"}:
        acc.status = LinkedInAccountStatus.OK
        acc.last_error = None
        acc.pending_challenge_url = None
    elif "CHECKPOINT" in src or "2FA" in src or "OTP" in src:
        acc.status = LinkedInAccountStatus.CHALLENGED
        acc.pending_challenge_url = "https://www.linkedin.com"
        acc.last_error = payload.get("detail") or payload.get("message") or src
    elif "BANNED" in src or "RESTRICTED" in src or "SUSPENDED" in src:
        acc.status = LinkedInAccountStatus.RESTRICTED
        acc.last_error = payload.get("detail") or payload.get("message") or src
    elif src in {"DISCONNECTED", "CREDENTIALS"}:
        acc.status = LinkedInAccountStatus.FAILED
        acc.last_error = payload.get("detail") or payload.get("message") or src
    # Pick up the email if Unipile resolved it.
    email = (
        payload.get("user_email")
        or payload.get("linkedin_email")
        or (payload.get("user") or {}).get("email")
    )
    if email:
        acc.linkedin_email = email


@router.get("/", response_model=list[LinkedInAccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)) -> list[LinkedInAccount]:
    rows = (await db.execute(
        select(LinkedInAccount).order_by(LinkedInAccount.created_at.desc())
    )).scalars().all()
    return list(rows)


@router.get("/{account_id}", response_model=LinkedInAccountResponse)
async def get_account(
    account_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> LinkedInAccount:
    return await _get_or_404(db, account_id)


@router.patch("/{account_id}", response_model=LinkedInAccountResponse)
async def update_account(
    account_id: uuid.UUID,
    payload: LinkedInAccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> LinkedInAccount:
    acc = await _get_or_404(db, account_id)
    updates = payload.model_dump(exclude_unset=True)
    li_at = updates.pop("li_at_cookie", None)
    profile_needs_wipe = False
    if li_at and li_at.strip():
        acc.session_cookies_encrypted = encryption.encrypt(
            _li_at_to_cookie_blob(li_at.strip())
        )
        acc.status = LinkedInAccountStatus.UNTESTED
        acc.pending_challenge_url = None
        acc.last_error = None
        profile_needs_wipe = True
    if "password" in updates and updates["password"]:
        acc.password_encrypted = encryption.encrypt(updates.pop("password"))
        if not li_at:
            # Wipe cookies so the next test forces a fresh login with the
            # new password — but only if we didn't just set them via li_at.
            acc.session_cookies_encrypted = None
        acc.status = LinkedInAccountStatus.UNTESTED
        profile_needs_wipe = True
    else:
        updates.pop("password", None)
    for k, v in updates.items():
        setattr(acc, k, v)
    await db.commit()
    await db.refresh(acc)
    # Drop the persistent Chrome profile if cookies/password changed — next
    # _run() reseeds from the encrypted blob the user just pasted.
    if profile_needs_wipe:
        clear_profile(acc.id)
    return acc


@router.delete(
    "/{account_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
)
async def delete_account(
    account_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    acc = await _get_or_404(db, account_id)
    # campaigns.linkedin_account_id is ON DELETE SET NULL at DB level, so
    # campaigns pointing at this account just lose the reference.
    aid = acc.id
    unipile_id = acc.unipile_account_id
    provider_kind = acc.provider_kind
    await db.delete(acc)
    await db.commit()
    # Clean up the persistent Chrome profile dir for this account so we
    # don't accumulate orphaned profiles on disk.  (No-op for Unipile rows
    # — they never created one.)
    clear_profile(aid)
    # Also free the Unipile-side resource so we don't leak a dangling
    # session there.  Best-effort: a failure here is logged but does not
    # block the local delete.
    if provider_kind == "unipile" and unipile_id:
        try:
            await UnipileLinkedInProvider().delete_account(unipile_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Unipile delete_account(%s) failed (non-fatal): %s",
                unipile_id, exc,
            )


@router.post("/{account_id}/test", response_model=LinkedInTestResponse)
async def test_account(
    account_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> LinkedInTestResponse:
    """Try to authenticate against LinkedIn. Persists fresh cookies on
    success; persists challenge_url + flips status on failure.
    """
    acc = await _get_or_404(db, account_id)
    provider = get_provider()
    acc.last_tested_at = datetime.now(timezone.utc)

    try:
        result = await provider.test_connection(acc)
    except ChallengeRequired as exc:
        acc.status = LinkedInAccountStatus.CHALLENGED
        acc.last_error = str(exc)
        acc.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
        await db.commit()
        return LinkedInTestResponse(
            ok=False, status=acc.status, error=str(exc),
            challenge_url=acc.pending_challenge_url,
        )
    except AccountRestricted as exc:
        acc.status = LinkedInAccountStatus.RESTRICTED
        acc.last_error = str(exc)
        await db.commit()
        return LinkedInTestResponse(ok=False, status=acc.status, error=str(exc))

    if result.ok:
        acc.status = LinkedInAccountStatus.OK
        acc.last_error = None
        acc.pending_challenge_url = None
    else:
        # The provider's test_connection encodes its own challenge case
        # in meta — surface it here for consistency.
        if result.meta and result.meta.get("challenged"):
            acc.status = LinkedInAccountStatus.CHALLENGED
        elif result.meta and result.meta.get("restricted"):
            acc.status = LinkedInAccountStatus.RESTRICTED
        else:
            acc.status = LinkedInAccountStatus.FAILED
        acc.last_error = result.error
    await db.commit()
    await db.refresh(acc)
    return LinkedInTestResponse(
        ok=result.ok,
        status=acc.status,
        error=result.error,
        challenge_url=acc.pending_challenge_url,
        meta=result.meta,
    )


@router.post("/{account_id}/resolve-challenge", response_model=LinkedInAccountResponse)
async def resolve_challenge(
    account_id: uuid.UUID,
    payload: ResolveChallengeRequest,  # noqa: ARG001 — kept for future use
    db: AsyncSession = Depends(get_db),
) -> LinkedInAccount:
    """User has manually completed the captcha/PIN in their own browser.

    We clear the pending challenge state and flip status back to UNTESTED;
    the user is expected to click "Test" again next.
    """
    acc = await _get_or_404(db, account_id)
    acc.pending_challenge_url = None
    acc.status = LinkedInAccountStatus.UNTESTED
    acc.last_error = None
    # Clear stale cookies so re-test does a fresh password login from a clean
    # state — required because the old cookies triggered the challenge and are
    # now invalid. The fresh password login will succeed once the user has
    # completed any verification in their own browser.
    acc.session_cookies_encrypted = None
    await db.commit()
    await db.refresh(acc)
    return acc
