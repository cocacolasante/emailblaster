from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ConnectedAccount, ConnectedAccountTestStatus
from app.schemas.connected_account import (
    ConnectedAccountCreate,
    ConnectedAccountResponse,
    ConnectedAccountStatus,
    ConnectedAccountUpdate,
    ImapTestResponse,
)
from app.services import encryption, imap_client  # encryption used for create/update only

router = APIRouter(prefix="/connected-accounts", tags=["connected-accounts"])


async def _get_or_404(db: AsyncSession, account_id: uuid.UUID) -> ConnectedAccount:
    acc = await db.get(ConnectedAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Connected account not found")
    return acc


@router.post("/", response_model=ConnectedAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: ConnectedAccountCreate,
    db: AsyncSession = Depends(get_db),
) -> ConnectedAccount:
    existing = await db.execute(
        select(ConnectedAccount).where(ConnectedAccount.email_address == payload.email_address)
    )
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"An inbox for {payload.email_address} is already connected.",
        )
    acc = ConnectedAccount(
        label=payload.label,
        email_address=payload.email_address,
        imap_host=payload.imap_host,
        imap_port=payload.imap_port,
        imap_use_ssl=payload.imap_use_ssl,
        username=payload.username,
        password_encrypted=encryption.encrypt(payload.password),
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


@router.get("/", response_model=list[ConnectedAccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)) -> list[ConnectedAccount]:
    result = await db.execute(select(ConnectedAccount).order_by(ConnectedAccount.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{account_id}", response_model=ConnectedAccountResponse)
async def get_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConnectedAccount:
    return await _get_or_404(db, account_id)


@router.patch("/{account_id}", response_model=ConnectedAccountResponse)
async def update_account(
    account_id: uuid.UUID,
    payload: ConnectedAccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> ConnectedAccount:
    acc = await _get_or_404(db, account_id)
    updates = payload.model_dump(exclude_unset=True)
    if "password" in updates:
        plaintext = updates.pop("password")
        acc.password_encrypted = encryption.encrypt(plaintext)
    for key, value in updates.items():
        setattr(acc, key, value)
    await db.commit()
    await db.refresh(acc)
    return acc


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    acc = await _get_or_404(db, account_id)
    # Campaigns referencing this account get connected_account_id set to NULL
    # automatically by the DB-level ON DELETE SET NULL FK.
    await db.delete(acc)
    await db.commit()


@router.post("/{account_id}/test", response_model=ImapTestResponse)
async def test_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ImapTestResponse:
    acc = await _get_or_404(db, account_id)
    # All decryption is confined to imap_client; plaintext never enters this
    # router's scope.
    result = await asyncio.to_thread(imap_client.test_imap_with_account, acc)

    acc.last_tested_at = datetime.now(timezone.utc)
    if result.get("ok"):
        acc.last_test_status = ConnectedAccountTestStatus.OK
        acc.last_test_error = None
    else:
        acc.last_test_status = ConnectedAccountTestStatus.FAILED
        acc.last_test_error = result.get("error")
    await db.commit()

    return ImapTestResponse(
        ok=bool(result.get("ok")),
        error=result.get("error"),
        message_count=result.get("message_count"),
    )


@router.get("/{account_id}/status", response_model=ConnectedAccountStatus)
async def get_account_status(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConnectedAccount:
    return await _get_or_404(db, account_id)
