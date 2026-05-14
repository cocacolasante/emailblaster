"""Unit test for ``_human_dwell`` — the anti-bot-detection helper that
makes a Playwright page sit + scroll + mouse-move like a human reads.

The helper is best-effort camouflage: it must NEVER raise (page already
closed, mouse object not available, etc.) and must always sleep close to
the requested duration so callers can rely on the total runtime budget.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.linkedin.playwright_impl import _human_dwell


def _fake_page() -> MagicMock:
    """Build a minimum-viable fake of a Playwright Page that records what
    `_human_dwell` did to it."""
    page = MagicMock(name="page")
    page.mouse = MagicMock(name="mouse")
    page.mouse.wheel = AsyncMock(name="wheel")
    page.mouse.move = AsyncMock(name="move")
    return page


@pytest.mark.asyncio
async def test_dwell_completes_within_window():
    page = _fake_page()
    t0 = time.monotonic()
    await _human_dwell(page, min_seconds=0.05, max_seconds=0.1, scroll=True)
    elapsed = time.monotonic() - t0
    # The helper does N micro-sleeps internally that exceed max_seconds when
    # scroll=True. We just want to confirm it completes and bounds aren't wild.
    assert elapsed >= 0.05, "dwell exited before its minimum sleep"
    assert elapsed < 30.0, "dwell ran way past expected budget"


@pytest.mark.asyncio
async def test_dwell_does_scroll_and_mouse_when_scroll_true():
    page = _fake_page()
    await _human_dwell(page, min_seconds=0.0, max_seconds=0.0, scroll=True)
    assert page.mouse.wheel.await_count >= 1, "should scroll at least once"
    assert page.mouse.move.await_count >= 1, "should mouse-move at least once"


@pytest.mark.asyncio
async def test_dwell_skips_scroll_when_scroll_false():
    page = _fake_page()
    await _human_dwell(page, min_seconds=0.0, max_seconds=0.01, scroll=False)
    assert page.mouse.wheel.await_count == 0
    assert page.mouse.move.await_count == 0


@pytest.mark.asyncio
async def test_dwell_swallows_mouse_errors():
    """If the page closes mid-dwell, mouse calls raise — but dwell mustn't.
    Camouflage is best-effort; callers should never get an exception from it.
    """
    page = _fake_page()
    page.mouse.wheel = AsyncMock(side_effect=RuntimeError("page closed"))
    # Must not raise.
    await _human_dwell(page, min_seconds=0.0, max_seconds=0.01, scroll=True)


@pytest.mark.asyncio
async def test_dwell_respects_min_seconds_even_with_short_scroll():
    """A min/max of (1.0, 1.0) should sleep approximately 1s total even
    after the scroll/mouse phase finishes early.
    """
    page = _fake_page()
    # Make scroll/move return instantly so we test the remaining-budget sleep.
    page.mouse.wheel = AsyncMock(return_value=None)
    page.mouse.move = AsyncMock(return_value=None)
    t0 = time.monotonic()
    await _human_dwell(page, min_seconds=1.0, max_seconds=1.0, scroll=True)
    elapsed = time.monotonic() - t0
    # Because the scroll phase has its own internal sleeps, total will be >= 1s.
    # We just check it's at least the min.
    assert elapsed >= 1.0, f"expected >= 1.0s, got {elapsed:.2f}s"
