"""One-off backfill of Brevo transactional events over a date range.

Usage (inside the backend container):

    python scripts/backfill_brevo_events.py 2026-07-15 [2026-07-30] [--dry-run]

Runs once per active workspace, with that workspace's Brevo key, under
the RLS runtime role (``tenant_context`` + ``worker_engine``).

Fetches every event Brevo still retains for the range and runs each
through ``brevo_events.process_event`` with ``apply_side_effects=False``
(record rows only — no re-suppression, no circuit-breaker re-trips).
Safe to re-run: terminal events dedup per email (messageId), opens/clicks dedup per
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

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.services import brevo, credentials  # noqa: E402
from app.services.brevo_events import process_event  # noqa: E402
from app.tenancy.worker import active_tenant_ids, tenant_context  # noqa: E402
from app.tenancy.worker_db import worker_engine  # noqa: E402
from app.workers.brevo_events_poller import _parse_event_date  # noqa: E402


async def backfill_current_tenant(start: str, end: str, *, dry_run: bool) -> dict[str, int]:
    events = await brevo.fetch_events(start_date=start, end_date=end)
    counts = {"fetched": len(events), "recorded": 0, "deduped_or_unmatched": 0, "undated": 0}
    engine = worker_engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for ev in events:
                if _parse_event_date(ev.get("date")) is None:
                    counts["undated"] += 1
                    continue
                try:
                    recorded = await process_event(session, ev, apply_side_effects=False)
                    # Flush so a later copy of the same event in this batch dedupes.
                    await session.flush()
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! failed for {ev.get('messageId')}: {exc}")
                    continue
                counts["recorded" if recorded else "deduped_or_unmatched"] += 1
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
    finally:
        await engine.dispose()
    return counts


async def main(start: str, end: str, dry_run: bool) -> None:
    for tenant_id in await active_tenant_ids():
        async with tenant_context(tenant_id):
            if not credentials.is_configured("brevo"):
                print(f"{tenant_id}: no Brevo key, skipped")
                continue
            counts = await backfill_current_tenant(start, end, dry_run=dry_run)
        print(f"{tenant_id}: {counts}{'  (dry run — nothing saved)' if dry_run else ''}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if not args:
        raise SystemExit("usage: backfill_brevo_events.py <start YYYY-MM-DD> [end YYYY-MM-DD] [--dry-run]")
    asyncio.run(main(args[0], args[1] if len(args) > 1 else date.today().isoformat(),
                     "--dry-run" in sys.argv))
