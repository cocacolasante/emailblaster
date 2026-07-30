"""One-off backfill of Brevo transactional events over a date range.

Usage (inside the backend container):

    python scripts/backfill_brevo_events.py 2026-07-15 [2026-07-30]

Fetches every event Brevo still retains for the range and runs each
through ``brevo_events.process_event`` with ``apply_side_effects=False``
(record rows only — no re-suppression, no circuit-breaker re-trips).
Safe to re-run: terminal events dedup per lead, opens/clicks dedup per
``(lead, type, occurred_at)``.

Written for the 2026-07 recovery: the stack was down 07-15→07-26 (the
poller's 24h lookback floor never backfilled that window), and until the
watermark-filter fix the poller dropped nearly all late-exposed
``opened`` events.
"""
import asyncio
import sys
from datetime import date

sys.path.insert(0, ".")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import brevo  # noqa: E402
from app.services.brevo_events import process_event  # noqa: E402
from app.workers.brevo_events_poller import _parse_event_date  # noqa: E402


async def main(start: str, end: str) -> None:
    events = await brevo.fetch_events(start_date=start, end_date=end)
    counts = {"fetched": len(events), "recorded": 0, "deduped_or_unmatched": 0, "undated": 0}
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for ev in events:
                if _parse_event_date(ev.get("date")) is None:
                    counts["undated"] += 1
                    continue
                try:
                    recorded = await process_event(session, ev, apply_side_effects=False)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! failed for {ev.get('messageId')}: {exc}")
                    continue
                counts["recorded" if recorded else "deduped_or_unmatched"] += 1
            await session.commit()
    finally:
        await engine.dispose()
    print(counts)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: backfill_brevo_events.py <start YYYY-MM-DD> [end YYYY-MM-DD]")
    start_arg = sys.argv[1]
    end_arg = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
    asyncio.run(main(start_arg, end_arg))
