"""Intent scoring, decay & tiering — the ``recompute_intent`` engine (Phase 3).

An org's intent is a time-decayed, ICP-weighted, org-fit-adjusted sum of its
recent signals:

    intent_score = ( Σ  score_i · 0.5^(age_i / half_life_i) · signal_weight_i )
                   · org_fit_multiplier

and the org's TIER is the most urgent tier among its live signals (Tier 1 =
"act now" wins over Tier 2 "warm" wins over Tier 3 "list").

Two design points worth keeping straight:

- **Per-signal-type half-life.**  990-derived signals (``rev_drop``,
  ``peer_funded``, ``new_501c3``) are intrinsically months-stale — IRS 990 and
  federal-award data lag reporting by 6–18 months — so a single 30-day global
  half-life would decay them to nothing the moment they arrive.  Each type
  therefore carries its own half-life + max-age in ``SIGNAL_PROFILE``: long
  fuses for the lagged sources, short ones for time-sensitive events (a new
  RFP, a fresh dev-role posting).  (Per-type half-life is currently hardcoded;
  a per-tenant override on ``IcpIntentProfile`` is a documented Phase-5
  follow-up — the profile's single ``half_life_days`` is only the fallback for
  any type not in the table.)

- **Org-fit multiplier.**  The ICP sweet spot is small/mid nonprofits showing
  activity without a big in-house development shop, so the size-band weights
  boost small/mid and penalize large/major.  ``IcpIntentProfile.size_band_weights``
  overrides ``DEFAULT_SIZE_BAND_WEIGHTS`` when present.

Lifecycle: a NEW signal within its max-age becomes SCORED; one past max-age
becomes EXPIRED and stops contributing; SUPPRESSED (manual) never contributes;
PROMOTED (Phase 4) still counts as a real event.  Nothing here promotes or
sends — Tier-3 orgs are never promotion-eligible (``is_promotable``), and the
outreach bridge is Phase 4.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    IcpIntentProfile, IntentSignalStatus, IntentSignalType, Org,
    OrgIntentScore, OrgSizeBand, Signal,
)

logger = logging.getLogger(__name__)

DEFAULT_TIER = 3
_FALLBACK_HALF_LIFE_DAYS = 30.0
_FALLBACK_MAX_AGE_DAYS = 180

# signal_type -> (tier, half_life_days, max_age_days).  See module docstring
# for why the half-lives differ so widely by type.
SIGNAL_PROFILE: dict[IntentSignalType, tuple[int, float, int]] = {
    # Tier 1 — act now.
    IntentSignalType.NEW_RFP:         (1,  30.0,  90),   # grant deadline — short fuse
    IntentSignalType.DEV_ROLE_POSTED: (1,  45.0, 120),   # active hiring window
    IntentSignalType.LAPSED_FUNDER:   (1, 120.0, 365),   # a funding gap stays relevant
    # Tier 2 — warm.  REV_DROP / PEER_FUNDED windows are LONG on purpose: the
    # signal is dated at the fiscal-year-end / award-action date, but 990s and
    # federal award records aren't published until ~1–2y later, so by the time
    # the event is knowable the event_date is already well in the past.  The
    # latest-known filing stays the best available evidence (and actionable)
    # through its natural publish-then-outreach lifecycle — ~3y from the dated
    # event — until a newer filing supersedes it.
    IntentSignalType.REV_DROP:        (2, 540.0, 1095),  # ~1.5y half-life, ~3y window
    IntentSignalType.NEW_PROGRAM:     (2, 120.0, 365),
    IntentSignalType.PEER_FUNDED:     (2, 365.0, 1095),
    # Tier 3 — cold list (low strength, never auto-promoted).
    IntentSignalType.NEW_501C3:       (3, 540.0, 1095),
    IntentSignalType.CAUSE_MATCH:     (3, 365.0, 1095),
}

# size band -> org-fit multiplier (sweet spot = small/mid).
DEFAULT_SIZE_BAND_WEIGHTS: dict[OrgSizeBand, float] = {
    OrgSizeBand.MICRO: 0.7,   # often too small for paid grant-writing help
    OrgSizeBand.SMALL: 1.2,   # sweet spot
    OrgSizeBand.MID:   1.2,   # sweet spot
    OrgSizeBand.LARGE: 0.6,   # likely has in-house development staff
    OrgSizeBand.MAJOR: 0.3,   # large dev shop — poor fit
}

# Signal statuses that still count toward the rolled-up score.
_LIVE_STATUSES = {
    IntentSignalStatus.NEW, IntentSignalStatus.SCORED, IntentSignalStatus.PROMOTED,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def tier_for_signal(signal_type: IntentSignalType) -> int:
    """The intent tier a signal type implies (1 = act now ... 3 = list)."""
    prof = SIGNAL_PROFILE.get(signal_type)
    return prof[0] if prof else DEFAULT_TIER


def _half_life_and_max_age(
    signal_type: IntentSignalType, profile: IcpIntentProfile | None,
) -> tuple[float, int]:
    prof = SIGNAL_PROFILE.get(signal_type)
    if prof:
        return prof[1], prof[2]
    # Unknown type — fall back to the profile's global knobs (or the defaults).
    hl = float(profile.half_life_days) if profile else _FALLBACK_HALF_LIFE_DAYS
    max_age = profile.max_signal_age_days if profile else _FALLBACK_MAX_AGE_DAYS
    return hl, max_age


def fit_multiplier_for(
    band: OrgSizeBand | None, profile: IcpIntentProfile | None,
) -> float:
    """Org-fit multiplier from the 990 size band; unknown band → neutral 1.0.
    A profile's ``size_band_weights`` overrides the defaults per band."""
    if band is None:
        return 1.0
    if profile and profile.size_band_weights:
        v = profile.size_band_weights.get(band.value)
        if v is not None:
            return float(v)
    return DEFAULT_SIZE_BAND_WEIGHTS.get(band, 1.0)


def _age_days(now: datetime, event_date: datetime) -> float:
    ed = event_date if event_date.tzinfo else event_date.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ed).total_seconds() / 86400.0)


def _signal_weight(signal_type: IntentSignalType, profile: IcpIntentProfile | None) -> float:
    if profile and profile.signal_weights:
        v = profile.signal_weights.get(signal_type.value)
        if v is not None:
            return float(v)
    return 1.0


def is_promotable(score_row: OrgIntentScore, profile: IcpIntentProfile | None) -> bool:
    """Whether an org is eligible for the (human-approved) outreach bridge.
    Tier 3 is NEVER auto-promotable; otherwise gated on the promotion
    threshold.  (Phase 4 consumes this — Phase 3 only computes it.)"""
    if score_row.tier >= DEFAULT_TIER:
        return False
    threshold = float(profile.promotion_threshold) if profile else 100.0
    return float(score_row.intent_score) >= threshold


async def get_active_profile(
    session: AsyncSession, tenant_id=None,
) -> IcpIntentProfile | None:
    """The active ICP intent profile for a tenant (newest wins), or None to
    score with the built-in defaults."""
    q = select(IcpIntentProfile).where(IcpIntentProfile.is_active.is_(True))
    q = (q.where(IcpIntentProfile.tenant_id.is_(None)) if tenant_id is None
         else q.where(IcpIntentProfile.tenant_id == tenant_id))
    return await session.scalar(q.order_by(IcpIntentProfile.created_at.desc()))


async def recompute_org_intent(
    session: AsyncSession, org: Org, *,
    profile: IcpIntentProfile | None = None, now: datetime | None = None,
) -> OrgIntentScore:
    """Recompute one org's rolled-up intent from its signals.  Decays each live
    signal by its per-type half-life, applies the ICP signal-weight + org-fit
    multiplier, sums, derives the tier (most urgent live signal), and flips
    NEW→SCORED / over-age→EXPIRED in place.  Upserts the ``OrgIntentScore``
    row.  Caller commits."""
    now = now or _now()
    signals = (await session.execute(
        select(Signal).where(Signal.org_id == org.id)
    )).scalars().all()

    contributions: list[tuple[Signal, float]] = []
    for s in signals:
        if s.status not in _LIVE_STATUSES:
            continue  # SUPPRESSED / already-EXPIRED never contribute
        half_life, max_age = _half_life_and_max_age(s.signal_type, profile)
        age = _age_days(now, s.event_date)
        if age > max_age:
            s.status = IntentSignalStatus.EXPIRED
            continue
        if s.status is IntentSignalStatus.NEW:
            s.status = IntentSignalStatus.SCORED
        weight = _signal_weight(s.signal_type, profile)
        decayed = float(s.score) * (0.5 ** (age / half_life)) * weight
        contributions.append((s, decayed))

    fit = fit_multiplier_for(org.size_band, profile)
    intent_score = sum(d for _, d in contributions) * fit
    if contributions:
        tier = min(tier_for_signal(s.signal_type) for s, _ in contributions)
        top_signal = max(contributions, key=lambda c: c[1])[0]
    else:
        tier = DEFAULT_TIER
        top_signal = None

    row = await session.get(OrgIntentScore, org.id)
    if row is None:
        row = OrgIntentScore(org_id=org.id, tenant_id=org.tenant_id)
        session.add(row)
    row.intent_score = Decimal(str(round(intent_score, 4)))
    row.tier = tier
    row.fit_multiplier = Decimal(str(round(fit, 3)))
    row.top_signal_id = top_signal.id if top_signal else None
    row.last_computed_at = now
    return row


async def recompute_all_intent(
    session: AsyncSession, *, now: datetime | None = None, tenant_id=None,
) -> dict[str, int]:
    """Recompute intent for every org that has at least one signal.  Per-org
    commit so one bad org can't roll back the batch.  Returns counts."""
    now = now or _now()
    profile = await get_active_profile(session, tenant_id=tenant_id)
    org_ids = (await session.execute(
        select(Signal.org_id).distinct()
    )).scalars().all()

    counts = {"orgs": 0, "tier1": 0, "tier2": 0, "tier3": 0}
    for oid in org_ids:
        org = await session.get(Org, oid)
        if org is None:
            continue
        row = await recompute_org_intent(session, org, profile=profile, now=now)
        counts["orgs"] += 1
        counts[f"tier{row.tier}"] += 1
        await session.commit()

    logger.info("recompute_all_intent: %s", counts)
    return counts
