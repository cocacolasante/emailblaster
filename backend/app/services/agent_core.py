"""Agent orchestration core.

Pure async functions the workers call, kept out of Celery task modules
so the logic is unit-testable without a broker.  This module is the
single place that enforces the agent's autonomy boundary:

  MAY autonomously:  log activities, create reminder TASKS, create
                     notifications (and email the OWNER), flag stale
                     opportunities.
  MAY NEVER:         convert a lead, send anything to a prospect,
                     change an opportunity stage, delete anything.

Anything in the second list must remain a human-triggered router
action; the agent only *prompts* it via reminders/notifications.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AGENT_SETTINGS_SINGLETON_ID, AgentSettings

logger = logging.getLogger(__name__)


async def get_agent_settings(session: AsyncSession) -> AgentSettings:
    """Return the singleton AgentSettings row, creating it with defaults
    on first access so a fresh install needs no seed step.

    Caller owns the transaction — on the create path the new row is
    flushed (so defaults/PK are live) but not committed.
    """
    row = await session.get(AgentSettings, AGENT_SETTINGS_SINGLETON_ID)
    if row is None:
        row = AgentSettings(id=AGENT_SETTINGS_SINGLETON_ID)
        session.add(row)
        await session.flush()
        # Re-read so server_default columns (the bool toggles, the
        # confidence threshold) carry real values instead of None.
        await session.refresh(row)
    return row
