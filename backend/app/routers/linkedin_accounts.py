from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import LinkedInAccount, LinkedInAccountStatus
from app.schemas.linkedin_account import (
    LinkedInAccountCreate,
    LinkedInAccountResponse,
    LinkedInAccountUpdate,
    LinkedInTestResponse,
    ResolveChallengeRequest,
)
from app.services import encryption
from app.services.linkedin import get_provider
from app.services.linkedin.base import ChallengeRequired, AccountRestricted

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
    acc = LinkedInAccount(
        label=payload.label,
        linkedin_email=payload.linkedin_email,
        password_encrypted=encryption.encrypt(payload.password),
        proxy_url=payload.proxy_url or None,
    )
    if payload.li_at_cookie and payload.li_at_cookie.strip():
        acc.session_cookies_encrypted = encryption.encrypt(
            _li_at_to_cookie_blob(payload.li_at_cookie.strip())
        )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


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
    if li_at and li_at.strip():
        acc.session_cookies_encrypted = encryption.encrypt(
            _li_at_to_cookie_blob(li_at.strip())
        )
        acc.status = LinkedInAccountStatus.UNTESTED
        acc.pending_challenge_url = None
        acc.last_error = None
    if "password" in updates and updates["password"]:
        acc.password_encrypted = encryption.encrypt(updates.pop("password"))
        if not li_at:
            # Wipe cookies so the next test forces a fresh login with the
            # new password — but only if we didn't just set them via li_at.
            acc.session_cookies_encrypted = None
        acc.status = LinkedInAccountStatus.UNTESTED
    else:
        updates.pop("password", None)
    for k, v in updates.items():
        setattr(acc, k, v)
    await db.commit()
    await db.refresh(acc)
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
    await db.delete(acc)
    await db.commit()


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
    await db.commit()
    await db.refresh(acc)
    return acc
