"""IRS Exempt-Organizations Business Master File (EO BMF) feed.

Per-state CSV extracts at https://www.irs.gov/pub/irs-soi/eo_<state>.csv
(verified against the IRS EO BMF download page).  Columns include EIN,
NAME, STATE, SUBSECTION, RULING, NTEE_CD, CLASSIFICATION.  We keep rows
where SUBSECTION == 3 (501(c)(3)) AND RULING (YYYYMM) >= since_ruling.

The file is refreshed monthly (2nd Tuesday); the cumulative extract
means a re-poll re-sees old rulings — bounded by ``since_ruling`` and
de-duped downstream on ``new_501c3:<ein>``.
"""
from __future__ import annotations

import csv
import io
import logging

import httpx

from app.services.funding_sources.base import DiscoveredOrg

logger = logging.getLogger(__name__)

EO_BMF_BASE = "https://www.irs.gov/pub/irs-soi"
_TIMEOUT = 120.0
# Subsection code 3 = 501(c)(3).  Files store it unpadded ("3") but be lenient.
_C3_SUBSECTIONS = {"3", "03"}


def _parse_state_csv(text: str, state: str, since_ruling: str) -> list[DiscoveredOrg]:
    orgs: list[DiscoveredOrg] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        subsection = (row.get("SUBSECTION") or "").strip()
        ruling = (row.get("RULING") or "").strip()
        if subsection not in _C3_SUBSECTIONS:
            continue
        # RULING is fixed-width YYYYMM, so a string compare is a date
        # compare.  "000000"/blank rulings never pass the window.
        if not ruling or len(ruling) != 6 or ruling < since_ruling:
            continue
        ein = (row.get("EIN") or "").strip()
        name = (row.get("NAME") or "").strip()
        if not ein or not name:
            continue
        ntee = (row.get("NTEE_CD") or "").strip() or None
        classification = (row.get("CLASSIFICATION") or "").strip() or None
        orgs.append(DiscoveredOrg(
            signal_type="new_501c3",
            summary=f"New 501(c)(3): {name} ({state.upper()}) — IRS ruling {ruling}"[:300],
            dedup_key=f"new_501c3:{ein}",
            org_name=name,
            state=state.upper(),
            ein=ein,
            ntee_code=ntee,
            detail={
                "ruling_date": ruling,
                "ntee_code": ntee,
                "state": state.upper(),
                "classification": classification,
            },
        ))
    return orgs


async def fetch_new_501c3(states: list[str], since_ruling: str) -> list[DiscoveredOrg]:
    """New 501(c)(3) orgs in the configured states with RULING >=
    ``since_ruling`` (YYYYMM).  Never raises — a download failure for one
    state logs and is skipped."""
    orgs: list[DiscoveredOrg] = []
    seen_keys: set[str] = set()
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        for raw_state in states:
            state = (raw_state or "").strip()
            if not state:
                continue
            url = f"{EO_BMF_BASE}/eo_{state.lower()}.csv"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                text = resp.text
            except Exception as exc:  # noqa: BLE001 — one bad state must not crash
                logger.warning("IRS BMF fetch failed for %s (%s): %s", state, url, exc)
                continue
            for org in _parse_state_csv(text, state, since_ruling):
                if org.dedup_key in seen_keys:
                    continue
                seen_keys.add(org.dedup_key)
                orgs.append(org)
    return orgs
