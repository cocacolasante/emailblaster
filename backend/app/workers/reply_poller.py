"""Periodic IMAP reply polling.

A single Celery beat task fans out per connected account, calling the IMAP
client to fetch new unseen messages and matching them to outbound leads.
Accounts whose last credential test failed are skipped so we don't hammer
broken inboxes.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models import (
    Campaign,
    CampaignStatus,
    ConnectedAccount,
    ConnectedAccountTestStatus,
    EmailEvent,
    EmailEventType,
)
from app.services import imap_client  # decryption stays inside imap_client
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# When an account has never been polled, search back this far on the first run.
INITIAL_LOOKBACK_DAYS = 7


async def poll_account_for_replies(
    account: ConnectedAccount,
    campaign_ids: list[uuid.UUID],
    session: AsyncSession,
) -> dict[str, Any]:
    """Pull new replies for one account and link them to leads.

    Updates account.last_polled_at on success. On IMAP/auth failure, marks the
    account's last_test_status=FAILED so the beat task skips it next cycle.
    Caller is responsible for committing the session.
    """
    since = account.last_polled_at or (
        datetime.now(timezone.utc) - timedelta(days=INITIAL_LOOKBACK_DAYS)
    )

    try:
        # Decryption + IMAP call live in imap_client; no plaintext touches
        # this function. Raises on decrypt failure or any IMAP error.
        messages = await asyncio.to_thread(
            imap_client.fetch_unseen_with_account, account, since, True,
        )
    except Exception as e:  # noqa: BLE001 — auth, network, ssl, decrypt
        account.last_test_status = ConnectedAccountTestStatus.FAILED
        account.last_test_error = str(e)
        logger.warning(
            "IMAP poll failed for %s: %s", account.email_address, e
        )
        return {"ok": False, "error": str(e), "replies_found": 0}

    replies_found = 0
    for msg in messages:
        lead = await imap_client.match_message_to_lead(session, msg, campaign_ids)
        if lead is None:
            continue
        session.add(EmailEvent(
            lead_id=lead.id,
            campaign_id=lead.campaign_id,
            event_type=EmailEventType.REPLIED,
            event_data={
                "from": msg["from_email"],
                "subject": msg["subject"],
                "in_reply_to": msg["in_reply_to"],
                "uid": msg["uid"],
            },
        ))
        replies_found += 1

    account.last_polled_at = datetime.now(timezone.utc)
    return {"ok": True, "replies_found": replies_found}


async def poll_all_replies_async() -> dict[str, Any]:
    engine = create_async_engine(settings.DATABASE_URL)
    total_replies = 0
    per_account: list[dict[str, Any]] = []

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            accounts = list((await session.execute(
                select(ConnectedAccount).where(
                    ConnectedAccount.last_test_status != ConnectedAccountTestStatus.FAILED
                )
            )).scalars().all())

            for account in accounts:
                campaign_ids = list((await session.execute(
                    select(Campaign.id).where(
                        Campaign.connected_account_id == account.id,
                        Campaign.status.in_([
                            CampaignStatus.RUNNING,
                            CampaignStatus.PAUSED,
                            CampaignStatus.COMPLETE,
                        ]),
                    )
                )).scalars().all())

                if not campaign_ids:
                    per_account.append({
                        "account_id": str(account.id),
                        "skipped": "no_active_campaigns",
                    })
                    continue

                result = await poll_account_for_replies(account, campaign_ids, session)
                try:
                    await session.commit()
                except Exception:  # noqa: BLE001
                    await session.rollback()
                    raise

                if result.get("ok"):
                    total_replies += int(result.get("replies_found", 0))
                per_account.append({
                    "account_id": str(account.id),
                    "email": account.email_address,
                    **result,
                })
    finally:
        await engine.dispose()

    logger.info(
        "poll_all_replies finished: %d accounts checked, %d replies found",
        len(per_account), total_replies,
    )
    return {"accounts_checked": len(per_account), "replies_found": total_replies, "details": per_account}


@celery_app.task(name="reply_poller.poll_all_replies")
def poll_all_replies() -> dict[str, Any]:
    return asyncio.run(poll_all_replies_async())
