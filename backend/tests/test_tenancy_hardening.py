"""Static guards for multi-tenancy invariants.

Grep-style checks over ``app/`` that fail when someone reintroduces a
process-global credential read, a client singleton, or an engine built
outside the sanctioned factories.  Each allowlist is deliberately tiny.
"""
from __future__ import annotations

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _scan(pattern: str, allowed: set[str]) -> list[str]:
    rx = re.compile(pattern)
    offenders = []
    for path in APP.rglob("*.py"):
        rel = str(path.relative_to(APP))
        if rel in allowed:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "``" in stripped:
                continue  # comments / docstring prose
            if rx.search(line):
                offenders.append(f"{rel}:{i}: {stripped}")
    return offenders


def test_provider_keys_only_read_via_credentials():
    """Provider keys are per-workspace: nothing but ``services/credentials.py``
    may read them from process settings."""
    offenders = _scan(
        r"settings\.(ANTHROPIC_API_KEY|BREVO_API_KEY|BREVO_SENDER_EMAIL|"
        r"BREVO_SENDER_NAME|HUNTER_API_KEY|APOLLO_API_KEY|UNIPILE_DSN|"
        r"UNIPILE_API_KEY|ADZUNA_APP_ID|ADZUNA_APP_KEY)\b",
        {"services/credentials.py", "config.py"},
    )
    assert not offenders, "\n".join(offenders)


def test_anthropic_client_only_built_in_factory():
    offenders = _scan(r"AsyncAnthropic\(", {"services/_anthropic.py"})
    assert not offenders, "\n".join(offenders)


def test_no_engine_built_outside_factories():
    offenders = _scan(
        r"create_async_engine\(",
        {"database.py", "tenancy/worker_db.py"},
    )
    assert not offenders, "\n".join(offenders)
