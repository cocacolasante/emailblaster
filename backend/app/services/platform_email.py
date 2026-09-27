"""Platform mail: password resets and team invites.

These emails belong to the PLATFORM, not a workspace — they go out
before a user has a workspace (or to people outside it), so they can't
use a workspace's Brevo key.  ``PLATFORM_BREVO_API_KEY`` is the one
provider key that remains process-level, and this module is its only
consumer.

Unconfigured (typical local dev): nothing is sent; callers fall back
(reset links are logged at WARNING, invite links are returned to the
inviting admin to copy).  Never raises.
"""
from __future__ import annotations

import logging
import uuid

from app.config import settings
from app.services import brevo
from app.services.credentials import BrevoCreds

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.PLATFORM_BREVO_API_KEY and settings.PLATFORM_SENDER_EMAIL)


async def send_platform_email(
    *, to_email: str, subject: str, html_body: str, text_body: str,
) -> bool:
    """Send one platform email.  Returns True when handed to Brevo."""
    if not is_configured():
        return False
    creds = BrevoCreds(
        api_key=settings.PLATFORM_BREVO_API_KEY,
        sender_email=settings.PLATFORM_SENDER_EMAIL,
        sender_name=settings.PLATFORM_SENDER_NAME,
    )
    try:
        await brevo.send_email(
            to_email=to_email,
            to_name=None,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            sender_name=creds.sender_name,
            sender_email=creds.sender_email,
            campaign_id="platform",
            lead_id=str(uuid.uuid4()),
            creds=creds,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — auth flows must not 500 on mail
        logger.warning("platform email to %s failed: %s", to_email, exc)
        return False
