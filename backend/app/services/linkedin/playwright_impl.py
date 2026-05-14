"""Playwright-backed LinkedIn provider.

Uses a real headless Chromium browser so LinkedIn sees fingerprints
identical to a normal user — solving the IP/User-Agent mismatch that
causes ``li_at`` session invalidation when using raw HTTP.

Auth strategy:
- Playwright ``storage_state`` (cookies + localStorage) is stored
  encrypted in the same ``session_cookies_encrypted`` column used by
  the HTTP impl.  Detected by the presence of an ``"origins"`` key.
- If the stored blob is the old HTTP cookie-list format, we extract the
  ``li_at`` value and inject it as a browser cookie to bootstrap the
  session without a fresh password login.
- If neither format is present, the browser does a full password login.

Voyager API calls (react, connect, DM, etc.) are made via
``page.evaluate()`` fetch() calls **from within the browser's JS
context**, so they automatically carry the authenticated session
cookies and the correct CSRF token.  LinkedIn cannot distinguish these
from actions taken by a human in Chrome.

SECURITY INVARIANT: ``encryption.decrypt`` is called only in
``_login_with_password`` (to read the password in local scope, which is
deleted before the function returns) and in ``_run`` (to decrypt the
stored session state).  Plaintext never leaves local scope.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import redis.asyncio as aioredis

from app.config import settings
from app.services import encryption
from app.services.linkedin.base import (
    AccountLockBusy,
    ActionResult,
    ChallengeRequired,
    InboundEvent,
    LinkedInProvider,
    LinkedInProviderError,
    ProfileRef,
)

logger = logging.getLogger(__name__)

_LI_ROOT = "https://www.linkedin.com"
_LI_FEED = "https://www.linkedin.com/feed/"
_LI_LOGIN = "https://www.linkedin.com/login"

# Ephemeral server-side cookies that must NOT be carried across browser sessions.
# JSESSIONID is a ~30-min server-side token; injecting a stale one causes LinkedIn
# to redirect-loop trying to re-issue a fresh one.
_EPHEMERAL_COOKIES = {"JSESSIONID"}

# Chromium args safe for Docker / root / single-process containers.
_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-setuid-sandbox",
]


# --------------------------------------------------------------------------
# Session state helpers
# --------------------------------------------------------------------------


def _is_playwright_state(blob: str) -> bool:
    try:
        d = json.loads(blob)
        return isinstance(d, dict) and "origins" in d
    except Exception:
        return False


def _extract_li_at(blob: str) -> str | None:
    """Pull li_at value out of either storage format."""
    try:
        d = json.loads(blob)
        cookies = d.get("cookies") if isinstance(d, dict) else d
        if isinstance(cookies, list):
            for c in cookies:
                if c.get("name") == "li_at":
                    return c.get("value")
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Browser / context helpers
# --------------------------------------------------------------------------


async def _launch_browser(pw):
    return await pw.chromium.launch(headless=True, args=_BROWSER_ARGS)


async def _new_context(browser, state: dict | None = None):
    kwargs: dict = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1280, "height": 800},
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }
    if state:
        kwargs["storage_state"] = state

    context = await browser.new_context(**kwargs)

    try:
        from playwright_stealth import stealth_async
        await stealth_async(context)
    except ImportError:
        logger.warning("playwright-stealth not installed — running without stealth patches")
    except Exception as exc:
        logger.warning("stealth_async failed (non-fatal): %s", exc)

    return context


# --------------------------------------------------------------------------
# Auth helpers
# --------------------------------------------------------------------------


_SESSION_EXPIRED_MSG = (
    "LinkedIn session expired — go to Settings → LinkedIn Accounts, "
    "paste a fresh li_at cookie, and save."
)


async def _goto_safe(page, url: str, **kwargs) -> None:
    """Navigate to *url*, converting redirect loops into a clear session-expiry error."""
    try:
        await page.goto(url, **kwargs)
    except Exception as exc:
        if "ERR_TOO_MANY_REDIRECTS" in str(exc):
            raise ChallengeRequired(_SESSION_EXPIRED_MSG, challenge_url="https://www.linkedin.com")
        raise


async def _check_for_challenge(page) -> None:
    url = page.url
    blocked = ("/checkpoint", "/challenge", "/authwall", "/uas/login", "/uas/authenticate")
    if any(seg in url for seg in blocked):
        raise ChallengeRequired(
            "LinkedIn challenge detected — complete verification in your browser.",
            challenge_url="https://www.linkedin.com",
        )


async def _is_logged_in(page) -> bool:
    url = page.url
    return (
        "linkedin.com" in url
        and "/login" not in url
        and "/checkpoint" not in url
        and "/challenge" not in url
        and "/authwall" not in url
        and "/uas/login" not in url
    )


async def _login_with_password(page, account: Any) -> None:
    """Drive the LinkedIn login form. Plaintext password stays in local scope."""
    password = encryption.decrypt(account.password_encrypted)
    try:
        await page.goto(_LI_LOGIN, wait_until="domcontentloaded", timeout=30_000)
        await _check_for_challenge(page)
        await page.fill("#username", account.linkedin_email, timeout=10_000)
        await page.fill("#password", password, timeout=5_000)
        # LinkedIn uses two different submit selectors across regions
        try:
            await page.click('[data-litms-control-urn="login-submit"]', timeout=5_000)
        except Exception:
            await page.click('[type="submit"]', timeout=5_000)
    finally:
        del password

    try:
        await page.wait_for_url("**/feed/**", timeout=20_000)
    except Exception:
        pass
    await _check_for_challenge(page)

    if not await _is_logged_in(page):
        raise LinkedInProviderError(
            "LinkedIn login failed — wrong password, account locked, or challenge required."
        )


async def _human_dwell(
    page,
    min_seconds: float = 6.0,
    max_seconds: float = 12.0,
    *,
    scroll: bool = True,
) -> None:
    """Pause on the current page like a human reading content.

    LinkedIn's bot detection looks at the *pattern* of activity across a
    session, not just individual requests.  A headless browser that loads
    the feed and then immediately jumps to a stranger's profile in <2s is
    a near-perfect bot signal — every legitimate user scrolls, hovers,
    reads, before clicking through.  This helper does:

      1. random idle (~6-12s default)
      2. a couple of small downward scrolls with pauses
      3. a scroll back partway up
      4. a few random mouse moves

    Tuned to be invisible to LinkedIn's bot scorer while keeping per-step
    runtime under ~20s.  Failure modes (e.g. page closes mid-scroll) are
    swallowed — this is best-effort camouflage, never load-bearing.
    """
    total = random.uniform(min_seconds, max_seconds)
    start = asyncio.get_event_loop().time()

    try:
        if scroll:
            # Initial sit-and-read before any motion.
            await asyncio.sleep(random.uniform(1.0, 2.5))
            # Two or three down-scrolls with reading pauses in between.
            for _ in range(random.randint(2, 3)):
                dy = random.randint(220, 520)
                await page.mouse.wheel(0, dy)
                await asyncio.sleep(random.uniform(0.9, 2.1))
            # Sometimes scroll partway back up like a human re-reading.
            if random.random() < 0.5:
                await page.mouse.wheel(0, -random.randint(150, 350))
                await asyncio.sleep(random.uniform(0.6, 1.4))
            # A couple of random mouse moves.
            for _ in range(random.randint(2, 4)):
                x = random.randint(120, 1160)
                y = random.randint(140, 720)
                await page.mouse.move(x, y, steps=random.randint(5, 15))
                await asyncio.sleep(random.uniform(0.2, 0.7))
    except Exception as exc:  # noqa: BLE001
        logger.debug("human_dwell scroll/move failed (non-fatal): %s", exc)

    # Sleep out whatever time remains of the target dwell.
    remaining = total - (asyncio.get_event_loop().time() - start)
    if remaining > 0:
        await asyncio.sleep(remaining)


async def _ensure_authenticated(page, account: Any) -> None:
    """Navigate to LinkedIn root then feed.  Raises ChallengeRequired if the
    stored session is dead and no automatic recovery is safe.

    We navigate to the root URL first so LinkedIn can issue a fresh JSESSIONID
    + bcookie for this browser.  Going directly to /feed/ with only li_at can
    trigger a redirect storm.

    After landing on /feed/ we do a `_human_dwell` (scroll + mouse + idle)
    so LinkedIn sees feed-reading behaviour BEFORE any profile navigation —
    going feed → profile in <2 seconds is the bot pattern that gets you
    redirected to /checkpoint.

    Recovery rules:
    - If cookies are stored but we end up not-logged-in: those cookies are dead.
      Password login from a headless server browser reliably trips LinkedIn's
      bot-detection challenge, so we DO NOT retry — we raise ChallengeRequired
      and ask the user to paste a fresh li_at.
    - If no cookies are stored (first-time setup): password login is the only
      option, so we try it once. It may still get challenged.
    """
    had_stored_session = bool(getattr(account, "session_cookies_encrypted", None))

    # Step 1: root — lets LinkedIn issue bcookie + JSESSIONID for this browser.
    await _goto_safe(page, _LI_ROOT, wait_until="domcontentloaded", timeout=30_000)
    await _check_for_challenge(page)

    if not await _is_logged_in(page):
        if had_stored_session:
            # Stored cookies didn't authenticate us — they're expired or LinkedIn
            # has flagged them.  Don't burn an attempt on a server-side password
            # login (which always triggers a challenge from headless browsers).
            raise ChallengeRequired(
                _SESSION_EXPIRED_MSG,
                challenge_url="https://www.linkedin.com",
            )
        # First-time setup with no stored cookies → try password login.
        await _login_with_password(page, account)
        return

    # Step 2: navigate to feed and *act human there* before any other nav.
    await asyncio.sleep(random.uniform(0.5, 1.5))
    await _goto_safe(page, _LI_FEED, wait_until="domcontentloaded", timeout=30_000)
    await _check_for_challenge(page)
    # Read the feed for 8-15s like a human — without this dwell, jumping
    # straight to a stranger's profile trips LinkedIn's bot detection.
    await _human_dwell(page, 8.0, 15.0, scroll=True)


# --------------------------------------------------------------------------
# Voyager API via browser JS context
#
# fetch() runs inside the page — same IP, same TLS fingerprint, same
# cookies as a human session.  No raw HTTP from the server.
# --------------------------------------------------------------------------

_VOYAGER_JS = """
async (args) => {
    const [method, url, body] = args;
    const jsessionid = document.cookie
        .split('; ')
        .find(c => c.trim().startsWith('JSESSIONID='))
        ?.split('=').slice(1).join('=')
        .replace(/^"|"$/g, '') || '';
    const opts = {
        method,
        headers: {
            'csrf-token':                jsessionid,
            'content-type':             'application/json',
            'x-restli-protocol-version': '2.0.0',
            'accept':                   'application/vnd.linkedin.normalized+json+2.1',
        },
        credentials: 'include',
    };
    if (body != null) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    let text = '';
    try { text = await res.text(); } catch(e) {}
    // Surface response headers — especially Location on 30x — so the
    // Python layer can react when LinkedIn moves an endpoint.
    const headers = {};
    try {
        res.headers.forEach((v, k) => { headers[k] = v; });
    } catch(e) {}
    return {
        status: res.status,
        body: text,
        headers,
        type: res.type,    // 'basic' | 'opaqueredirect' | 'error' | etc.
        url: res.url || '',
    };
}
"""


async def _voyager(page, method: str, url: str, body=None) -> tuple[int, str]:
    """Run a Voyager API call from the browser page.

    Returns ``(status, body)``.  On non-2xx responses, logs status + type +
    body + response headers + the FINAL response URL.  ``res.url`` differing
    from the request URL means LinkedIn 30x-redirected — usually a sign the
    endpoint has been deprecated and silently moved.
    """
    result = await page.evaluate(_VOYAGER_JS, [method, url, body])
    status, body_text = result["status"], result["body"]
    final_url = result.get("url") or ""
    if status < 200 or status >= 300:
        logger.warning(
            "Voyager %s %s -> status=%s final_url=%s type=%s body=%r headers=%r",
            method, url, status, final_url, result.get("type"),
            body_text[:500], result.get("headers"),
        )
    elif final_url and final_url != url and "?" not in url:
        # 2xx but redirected — LinkedIn likely moved the endpoint.  Only flag
        # bare URLs (query-string variants are normal redirects on action= URLs).
        logger.info(
            "Voyager %s %s -> 2xx but redirected to %s (possible endpoint move)",
            method, url, final_url,
        )
    return status, body_text


def _parse_li_error(body: str) -> str | None:
    """Pull a human-readable error out of a LinkedIn Voyager error response.

    LinkedIn error bodies are typically JSON like::

        {"status":422,"code":"CANT_INVITE_MEMBER","message":"..."}
        {"errorDetails":{"inputErrors":[{"description":{"value":"..."}}]}}

    Returns a short ``"CODE: message"`` string when one is extractable,
    otherwise None so the caller can fall back to the raw body.
    """
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    code = data.get("code") or (data.get("serviceErrorCode") and f"err{data['serviceErrorCode']}")
    message = data.get("message")
    if code or message:
        return f"{code or '?'}: {message or '(no message)'}"
    # Nested input-validation errors.
    details = data.get("errorDetails") or {}
    input_errors = details.get("inputErrors") or []
    if input_errors:
        descs = [
            (e.get("description") or {}).get("value")
            or e.get("description") or e.get("code")
            for e in input_errors
        ]
        return "input_errors: " + "; ".join(str(d) for d in descs if d)
    return None


def _parse_json(text: str) -> dict | list:
    try:
        return json.loads(text)
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Profile URN resolution
# --------------------------------------------------------------------------


# JS that extracts a fsd_profile URN from the currently-loaded profile page.
# Tries multiple sources because LinkedIn changes its DOM frequently:
#   1. data-urn / data-entity-urn attributes (newer pages)
#   2. <code id="bpr-guid-*"> JSON blobs embedded by the SPA
#   3. Plain regex over the whole page source as last resort
_URN_SCRAPE_JS = """
() => {
    // 1) Data attributes — most reliable when present.
    for (const attr of ['data-urn', 'data-entity-urn', 'data-member-id']) {
        const el = document.querySelector(`[${attr}*="fsd_profile:"]`);
        if (el) {
            const v = el.getAttribute(attr);
            const m = v && v.match(/urn:li:fsd_profile:[A-Za-z0-9_-]+/);
            if (m) return m[0];
        }
    }
    // 2) Embedded JSON in <code> tags (used by LinkedIn's SPA bootstrap).
    const codes = document.querySelectorAll('code');
    for (const c of codes) {
        const t = c.textContent || '';
        if (t.indexOf('fsd_profile') === -1) continue;
        const m = t.match(/urn:li:fsd_profile:[A-Za-z0-9_-]+/);
        if (m) return m[0];
    }
    // 3) Whole-document regex fallback.
    const html = document.documentElement.outerHTML;
    const m = html.match(/urn:li:fsd_profile:[A-Za-z0-9_-]+/);
    return m ? m[0] : null;
}
"""


async def _scrape_urn_from_page(page, public_id: str) -> str | None:
    """Navigate to the public profile page (if not already there) and extract
    the FSD profile URN from the rendered DOM.  More reliable than the Voyager
    API endpoint, which sometimes returns non-200 or a shape that doesn't
    include the URN we need.
    """
    if f"/in/{public_id}" not in page.url:
        try:
            await _goto_safe(
                page,
                f"https://www.linkedin.com/in/{public_id}/",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await _check_for_challenge(page)
            # Dwell on the profile like a human so LinkedIn doesn't flag
            # the navigation as bot behaviour AND so the SPA fully hydrates
            # before we scrape the URN out of the DOM.
            await _human_dwell(page, 5.0, 9.0, scroll=True)
        except ChallengeRequired:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("URN scrape: profile navigation failed for %s: %s", public_id, exc)
            return None
    try:
        return await page.evaluate(_URN_SCRAPE_JS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("URN scrape: page.evaluate failed for %s: %s", public_id, exc)
        return None


async def _resolve_urn(page, public_id: str) -> str | None:
    """Resolve a profile URN, preferring DOM scrape over the Voyager API.

    Older code path: GET /voyager/api/identity/profiles/{public_id} — this is
    flaky from a browser context (sometimes returns non-200, sometimes returns
    a response shape without entityUrn).  Kept as a fallback only.
    """
    # Primary: DOM scrape of the profile page (works as long as the page loads).
    urn = await _scrape_urn_from_page(page, public_id)
    if urn:
        return urn

    # Fallback: legacy Voyager API.  Keeping in case scrape misses (e.g. page
    # was blocked or the user is on a slow connection).
    status, body = await _voyager(
        page, "GET",
        f"https://www.linkedin.com/voyager/api/identity/profiles/{public_id}",
    )
    if status != 200:
        logger.warning(
            "URN fallback: voyager returned %s for %s (body=%s)",
            status, public_id, body[:200],
        )
        return None
    data = _parse_json(body)
    return (
        data.get("entityUrn")
        or (data.get("miniProfile") or {}).get("entityUrn")
        or data.get("plainId")
    )


def _normalize_urn(urn: str | None) -> str | None:
    if not urn:
        return None
    s = str(urn)
    return s if s.startswith("urn:") else f"urn:li:fsd_profile:{s}"


# --------------------------------------------------------------------------
# Per-account serialization lock (Redis)
# --------------------------------------------------------------------------
#
# Two playwright browser instances hitting LinkedIn for the same account
# within a few seconds — e.g. the inbox poller and the sequencer firing on
# adjacent beat ticks — look to LinkedIn like the same ``li_at`` being used
# from two different browser sessions (each fresh Chromium launch has a
# slightly different canvas/WebGL fingerprint despite playwright-stealth).
# LinkedIn treats that as a bot signal and invalidates the cookie.
#
# This Redis lock serializes ALL playwright operations for a single
# LinkedIn account so only one browser is live at a time. The lock TTL is
# long enough to cover the slowest expected action; the acquire timeout is
# generous enough that the poller can sit and wait for the sequencer rather
# than failing.

_LOCK_TTL_SECONDS = 180        # max time any single playwright op should take
_LOCK_ACQUIRE_TIMEOUT = 90     # how long callers will wait for the lock
_LOCK_POLL_INTERVAL = 0.5      # busy-wait interval


def _new_redis() -> aioredis.Redis:
    """Return a FRESH redis client bound to the current event loop.

    Celery prefork tasks each call ``asyncio.run(...)`` which spawns a new
    event loop; a module-level cached client carries connections bound to
    whichever loop first created it and explodes with "Event loop is
    closed" on the second task. So we don't cache — building a redis
    client is cheap (no eager TCP connect; connection pool is internal
    and lazy).
    """
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


# CAS release script: only delete if the value still matches our token.
# Prevents accidental release of someone else's lock if ours timed out.
_RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""


class _AccountLock:
    """Async context manager for the per-account Redis lock."""

    def __init__(self, account_id: Any):
        self.key = f"linkedin-acct-lock:{account_id}"
        self.token = secrets.token_hex(8)
        self._held = False

    async def __aenter__(self) -> "_AccountLock":
        rc = _new_redis()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _LOCK_ACQUIRE_TIMEOUT
        first = True
        try:
            while True:
                got = await rc.set(self.key, self.token, nx=True, ex=_LOCK_TTL_SECONDS)
                if got:
                    self._held = True
                    return self
                if first:
                    logger.info(
                        "LinkedIn account %s busy; waiting up to %ds for the lock",
                        self.key, _LOCK_ACQUIRE_TIMEOUT,
                    )
                    first = False
                if loop.time() >= deadline:
                    raise AccountLockBusy(
                        f"timed out waiting {_LOCK_ACQUIRE_TIMEOUT}s for "
                        f"LinkedIn account lock {self.key}"
                    )
                await asyncio.sleep(_LOCK_POLL_INTERVAL)
        finally:
            await _safe_close(rc)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self._held:
            return
        rc = _new_redis()
        try:
            await rc.eval(_RELEASE_LUA, 1, self.key, self.token)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LinkedIn account lock release failed (%s): %s", self.key, exc,
            )
        finally:
            await _safe_close(rc)


async def _safe_close(rc: aioredis.Redis) -> None:
    try:
        await rc.aclose()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# Per-account persistent Chrome profile
# --------------------------------------------------------------------------
#
# launch_persistent_context keeps cookies, localStorage, fonts cache, and
# Chrome's internal fingerprint state on disk between runs. Without this,
# every playwright launch drifts the canvas/WebGL/etc. fingerprint a little
# bit, and LinkedIn flags the same `li_at` being reused from "different
# browsers" within a short window. With a persistent profile, the same
# Chrome user-data-dir is reopened each run — same fingerprint, same
# stored state, no drift.


def _profile_dir_for(account_id: Any) -> Path:
    return Path(settings.LINKEDIN_PROFILES_DIR) / str(account_id)


def _profile_is_seeded(profile_dir: Path) -> bool:
    """A persistent profile is 'seeded' once Chrome has written its
    initial files into the user-data-dir. We use the presence of either
    the Default subdir or Local State file as the signal — both are written
    on first launch.
    """
    if not profile_dir.exists():
        return False
    return any(profile_dir.iterdir())


def clear_profile(account_id: Any) -> None:
    """Wipe an account's persistent profile dir. Called by the LinkedIn-
    accounts router when the user pastes a fresh li_at cookie — the next
    playwright launch will reseed from session_cookies_encrypted.
    """
    profile_dir = _profile_dir_for(account_id)
    if profile_dir.exists():
        logger.info("Clearing persistent LinkedIn profile for acct %s", account_id)
        shutil.rmtree(profile_dir, ignore_errors=True)


def _extract_seed_cookies(account: Any) -> list[dict]:
    """Read the encrypted session blob and return cookies suitable for
    ``context.add_cookies()``. Handles all three on-disk formats:
      1. Full Playwright storage_state (``{"cookies":[...],"origins":[...]}``)
      2. Bare cookie list ``[{name, value, ...}, ...]``
      3. The user-pasted bare li_at (wrapped by the router as
         ``[{name: "li_at", value: ...}]``)
    Strips ``JSESSIONID`` — LinkedIn issues a fresh one when the browser hits
    the root URL; injecting a stale one trips redirect loops.
    """
    stored = getattr(account, "session_cookies_encrypted", None)
    if not stored:
        return []
    try:
        blob = encryption.decrypt(stored)
        data = json.loads(blob)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LinkedIn seed decrypt failed for %s: %s", account.id, exc)
        return []

    if isinstance(data, dict) and "origins" in data:
        raw = data.get("cookies", []) or []
    elif isinstance(data, dict):
        raw = data.get("cookies", []) or []
    elif isinstance(data, list):
        raw = data
    else:
        return []

    out: list[dict] = []
    for c in raw:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        if name in _EPHEMERAL_COOKIES:
            continue
        out.append({
            "name": name,
            "value": value,
            "domain": c.get("domain") or ".linkedin.com",
            "path": c.get("path") or "/",
            "secure": c.get("secure", True),
            "httpOnly": c.get("httpOnly", True),
            "sameSite": c.get("sameSite", "None"),
        })
    return out


# --------------------------------------------------------------------------
# Provider class
# --------------------------------------------------------------------------


class PlaywrightLinkedInProvider(LinkedInProvider):
    """LinkedIn provider that drives a real headless Chromium browser."""

    async def _run(self, account: Any, fn, *args, **kwargs) -> Any:
        """Launch browser, restore session, run fn(page, account, ...), persist session.

        Wrapped in a per-account Redis lock so only one playwright browser is
        ever live per LinkedIn account. See ``_AccountLock`` for the reasoning.
        """
        async with _AccountLock(getattr(account, "id", "unknown")):
            return await self._run_locked(account, fn, *args, **kwargs)

    async def _run_locked(self, account: Any, fn, *args, **kwargs) -> Any:
        """Run ``fn(page, account, ...)`` inside a persistent Chrome profile
        so cookies + fingerprint stay stable across runs.

        On first launch for an account (profile dir empty), inject ``li_at``
        from ``account.session_cookies_encrypted`` as a one-time seed. After
        that, the persistent profile is authoritative and we don't touch the
        encrypted column. If the user re-pastes a cookie via the UI, the
        router calls ``clear_profile()`` to wipe the dir and force a reseed.
        """
        from playwright.async_api import async_playwright

        profile_dir = _profile_dir_for(account.id)
        is_fresh_profile = not _profile_is_seeded(profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Seed cookies for a fresh profile from the encrypted blob. After
        # the first run, the profile owns the cookies and this is unused.
        seed_cookies: list[dict] = []
        if is_fresh_profile:
            seed_cookies = _extract_seed_cookies(account)
            if seed_cookies:
                logger.info(
                    "LinkedIn _run: seeding fresh profile for acct %s with %d cookies",
                    account.id, len(seed_cookies),
                )
            else:
                logger.info(
                    "LinkedIn _run: fresh profile for acct %s with no seed cookies "
                    "(will require password login)",
                    account.id,
                )

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                args=_BROWSER_ARGS,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="America/New_York",
            )
            # Apply stealth patches to the context.
            try:
                from playwright_stealth import stealth_async
                await stealth_async(context)
            except ImportError:
                logger.warning(
                    "playwright-stealth not installed — running without stealth patches",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("stealth_async failed (non-fatal): %s", exc)

            if seed_cookies:
                await context.add_cookies(seed_cookies)

            try:
                page = await context.new_page()
                try:
                    result = await fn(page, account, *args, **kwargs)

                    # Mirror the post-run state into session_cookies_encrypted
                    # as a backup. Persistent profile is source of truth;
                    # this column lets us reseed if the profile is wiped.
                    try:
                        state = await context.storage_state()
                        account.session_cookies_encrypted = encryption.encrypt(json.dumps(state))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "post-run storage_state mirror failed for %s: %s",
                            account.id, exc,
                        )

                    # Success → clear any stale challenge state.
                    try:
                        from app.models import LinkedInAccountStatus
                        if account.status in (
                            LinkedInAccountStatus.CHALLENGED,
                            LinkedInAccountStatus.FAILED,
                            LinkedInAccountStatus.UNTESTED,
                        ):
                            account.status = LinkedInAccountStatus.OK
                    except Exception:  # noqa: BLE001
                        pass
                    account.pending_challenge_url = None
                    account.last_error = None
                    return result
                finally:
                    await page.close()
            finally:
                await context.close()


    # ------------------------------------------------------------------ #
    # test_connection
    # ------------------------------------------------------------------ #

    async def test_connection(self, account: Any) -> ActionResult:
        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            return {}

        try:
            await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            return ActionResult(ok=False, error=str(exc), meta={"challenged": True})
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True)

    # ------------------------------------------------------------------ #
    # view_profile — navigates so LinkedIn registers a profile view
    # ------------------------------------------------------------------ #

    async def view_profile(self, account: Any, profile: ProfileRef) -> ActionResult:
        if not profile.public_id:
            return ActionResult(ok=False, error="view_profile requires a public_id")

        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            # _ensure_authenticated has already dwelled on the feed.  Now go
            # to the profile page and dwell there too — LinkedIn registers
            # the ghost view only if we stay on the page long enough for
            # the SPA to fully hydrate, and the dwell-then-navigate
            # pattern is what differentiates this from a bot script.
            await _goto_safe(
                page,
                f"https://www.linkedin.com/in/{profile.public_id}/",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await _check_for_challenge(page)
            # Read the profile like a human (scroll, mouse, idle 6-12s).
            await _human_dwell(page, 6.0, 12.0, scroll=True)
            urn = await _resolve_urn(page, profile.public_id)
            return {"urn": _normalize_urn(urn)}

        try:
            result = await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("urn"), meta=result)

    # ------------------------------------------------------------------ #
    # follow_profile
    # ------------------------------------------------------------------ #

    async def follow_profile(self, account: Any, profile: ProfileRef) -> ActionResult:
        if not profile.public_id and not profile.urn:
            return ActionResult(ok=False, error="follow_profile requires a public_id or urn")

        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            await asyncio.sleep(random.uniform(1.0, 3.0))
            urn = _normalize_urn(
                profile.urn or await _resolve_urn(page, profile.public_id)
            )
            if not urn:
                raise LinkedInProviderError("could not resolve profile URN for follow")

            status, body = await _voyager(
                page, "POST",
                "https://www.linkedin.com/voyager/api/feed/dash/followingStates",
                {"patch": {"$set": {"following": True}}, "entityUrn": urn},
            )
            if status not in (200, 201, 204):
                raise LinkedInProviderError(
                    f"follow_profile returned {status}: {_parse_li_error(body) or body[:300]}"
                )
            return {"urn": urn}

        try:
            result = await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("urn"), meta=result)

    # ------------------------------------------------------------------ #
    # react_to_post
    # ------------------------------------------------------------------ #

    async def react_to_post(
        self, account: Any, post_urn: str, reaction: str = "LIKE"
    ) -> ActionResult:
        reaction = (reaction or "LIKE").upper()

        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            status, body = await _voyager(
                page, "POST",
                f"https://www.linkedin.com/voyager/api/feed/reactions?threadUrn={post_urn}",
                {"reactionType": reaction},
            )
            if status not in (200, 201, 204):
                raise LinkedInProviderError(
                    f"react_to_post returned {status}: {_parse_li_error(body) or body[:300]}"
                )
            return {}

        try:
            await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=post_urn)

    # ------------------------------------------------------------------ #
    # latest_post_urn
    # ------------------------------------------------------------------ #

    async def latest_post_urn(
        self, account: Any, profile: ProfileRef
    ) -> str | None:
        if not profile.public_id:
            return None

        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            urn = _normalize_urn(
                profile.urn or await _resolve_urn(page, profile.public_id)
            )
            if not urn:
                return {"post_urn": None}

            status, body = await _voyager(
                page, "GET",
                f"https://www.linkedin.com/voyager/api/feed/updates"
                f"?profileId={urn}&q=memberShareFeed&moduleKey=member-share&count=1&start=0",
            )
            if status != 200:
                return {"post_urn": None}
            data = _parse_json(body)
            elements = data.get("elements") or []
            if not elements:
                return {"post_urn": None}
            first = elements[0]
            post_urn = (
                first.get("entityUrn")
                or first.get("urn")
                or first.get("updateKey")
            )
            return {"post_urn": post_urn}

        try:
            result = await self._run(account, _fn)
        except Exception as exc:
            logger.warning("latest_post_urn failed for %s: %s", account.id, exc)
            return None
        return result.get("post_urn")

    # ------------------------------------------------------------------ #
    # send_connect_request
    # ------------------------------------------------------------------ #

    async def send_connect_request(
        self, account: Any, profile: ProfileRef, note: str | None = None
    ) -> ActionResult:
        if not profile.public_id and not profile.urn:
            return ActionResult(ok=False, error="send_connect_request requires a public_id or urn")

        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            await asyncio.sleep(random.uniform(1.0, 3.0))

            # Modern endpoint wants the full URN in ``inviteeProfileUrn``.
            # _resolve_urn scrapes the profile DOM (falls back to Voyager
            # /api/identity/profiles).  Bare public_id is NOT accepted.
            urn = profile.urn or await _resolve_urn(page, profile.public_id)
            if not urn:
                raise LinkedInProviderError(
                    f"send_connect could not resolve profile URN for "
                    f"{profile.public_id or '<unknown>'}"
                )
            profile_id = urn.split(":")[-1]  # urn:li:fsd_profile:ABC -> ABC
            logger.info(
                "send_connect: account=%s prospect=%s urn=%s note=%s",
                account.id, profile.public_id, urn, bool(note and note.strip()),
            )

            # LinkedIn deprecated /voyager/api/growth/normInvitations in 2024.
            # Current endpoint: voyagerRelationshipsDashMemberRelationships
            # with ?action=verifyQuotaAndCreate.  Payload field is
            # ``inviteeProfileUrn`` and the optional note is ``customMessage``.
            # LinkedIn caps notes at 200 chars for Free and 300 for Premium;
            # we trim to 200 to be safe (Premium will accept this, Free won't
            # 422 on length).
            payload: dict = {"inviteeProfileUrn": urn}
            if note and note.strip():
                payload["customMessage"] = note.strip()[:200]

            status, body = await _voyager(
                page, "POST",
                "https://www.linkedin.com/voyager/api/voyagerRelationshipsDashMemberRelationships"
                "?action=verifyQuotaAndCreate",
                payload,
            )
            if status not in (200, 201, 204):
                # Surface LinkedIn's structured error code if present so the
                # user sees e.g. "CANT_INVITE_MEMBER: Daily limit reached"
                # instead of an opaque 422 body.
                pretty = _parse_li_error(body) or body[:300]
                raise LinkedInProviderError(
                    f"send_connect returned {status}: {pretty}"
                )
            return {"profile_id": profile_id, "urn": urn}

        try:
            result = await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("profile_id"), meta=result)

    # ------------------------------------------------------------------ #
    # send_dm
    # ------------------------------------------------------------------ #

    async def send_dm(
        self, account: Any, profile: ProfileRef, text: str
    ) -> ActionResult:
        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            await asyncio.sleep(random.uniform(1.0, 3.0))
            urn = _normalize_urn(
                profile.urn
                or (await _resolve_urn(page, profile.public_id) if profile.public_id else None)
            )
            if not urn:
                raise LinkedInProviderError("send_dm could not resolve profile URN")

            payload = {
                "message": {
                    "body": {"text": text, "attributes": []},
                    "renderContentUnions": [],
                },
                "subtype": "MEMBER_TO_MEMBER",
                "recipientUrns": [urn],
            }
            status, body = await _voyager(
                page, "POST",
                "https://www.linkedin.com/voyager/api/voyagerMessagingDashMessengerMessages"
                "?action=createMessage",
                payload,
            )
            if status not in (200, 201, 204):
                raise LinkedInProviderError(
                    f"send_dm returned {status}: {_parse_li_error(body) or body[:300]}"
                )
            return {"urn": urn}

        try:
            result = await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("urn"), meta=result)

    # ------------------------------------------------------------------ #
    # invite_to_page
    # ------------------------------------------------------------------ #

    async def invite_to_page(
        self, account: Any, profile: ProfileRef, page_id: str
    ) -> ActionResult:
        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            await asyncio.sleep(random.uniform(1.0, 3.0))
            urn = _normalize_urn(
                profile.urn
                or (await _resolve_urn(page, profile.public_id) if profile.public_id else None)
            )
            if not urn:
                raise LinkedInProviderError("invite_to_page could not resolve profile URN")

            status, body = await _voyager(
                page, "POST",
                "https://www.linkedin.com/voyager/api/growth/normInvitations",
                {
                    "inviteeUnion": {"memberProfile": urn},
                    "inviterOrganization": f"urn:li:fsd_company:{page_id}",
                },
            )
            if status not in (200, 201, 204):
                raise LinkedInProviderError(
                    f"invite_to_page returned {status}: {_parse_li_error(body) or body[:300]}"
                )
            return {"urn": urn}

        try:
            result = await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("urn"), meta=result)

    # ------------------------------------------------------------------ #
    # send_inmail
    # ------------------------------------------------------------------ #

    async def send_inmail(
        self, account: Any, profile: ProfileRef, subject: str, body: str,
    ) -> ActionResult:
        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            await asyncio.sleep(random.uniform(1.0, 3.0))
            urn = _normalize_urn(
                profile.urn
                or (await _resolve_urn(page, profile.public_id) if profile.public_id else None)
            )
            if not urn:
                raise LinkedInProviderError("send_inmail could not resolve profile URN")

            payload = {
                "message": {
                    "body": {"text": body, "attributes": []},
                    "renderContentUnions": [],
                },
                "subtype": "INMAIL",
                "subject": subject,
                "recipientUrns": [urn],
            }
            status, resp_body = await _voyager(
                page, "POST",
                "https://www.linkedin.com/voyager/api/voyagerMessagingDashMessengerMessages"
                "?action=createMessage",
                payload,
            )
            if status in (200, 201, 204):
                return {"ok": True, "urn": urn}
            lower = resp_body.lower()
            if "inmail" in lower or "premium" in lower or status == 402:
                return {
                    "ok": False,
                    "premium_required": True,
                    "error": f"InMail unavailable ({status})",
                }
            raise LinkedInProviderError(
                f"send_inmail returned {status}: {_parse_li_error(resp_body) or resp_body[:300]}"
            )

        try:
            result = await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        if not result.get("ok"):
            return ActionResult(
                ok=False, meta=result, error=result.get("error", "InMail failed"),
            )
        return ActionResult(ok=True, external_id=result.get("urn"), meta=result)

    # ------------------------------------------------------------------ #
    # comment_on_post
    # ------------------------------------------------------------------ #

    async def comment_on_post(
        self, account: Any, post_urn: str, comment: str,
    ) -> ActionResult:
        async def _fn(page, account):
            await _ensure_authenticated(page, account)

            # Resolve the viewer URN for the actor field
            status, body = await _voyager(page, "GET", "https://www.linkedin.com/voyager/api/me")
            viewer_urn = None
            if status == 200:
                data = _parse_json(body)
                viewer_urn = _normalize_urn(
                    (data.get("miniProfile") or {}).get("entityUrn")
                    or data.get("entityUrn")
                )

            status2, body2 = await _voyager(
                page, "POST",
                f"https://www.linkedin.com/voyager/api/feed/dash/socialActions"
                f"/{quote(post_urn, safe='')}/comments",
                {
                    "actor": viewer_urn,
                    "value": {
                        "com.linkedin.voyager.feed.shared.AnnotatedText": {
                            "values": [], "text": comment,
                        },
                    },
                },
            )
            if status2 not in (200, 201, 204):
                raise LinkedInProviderError(
                    f"comment_on_post returned {status2}: {_parse_li_error(body2) or body2[:300]}"
                )
            return {}

        try:
            await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=post_urn)

    # ------------------------------------------------------------------ #
    # inbox_recent_events
    # ------------------------------------------------------------------ #

    async def inbox_recent_events(
        self, account: Any, since: datetime
    ) -> list[InboundEvent]:
        since_ms = int(since.timestamp() * 1000)

        async def _fn(page, account):
            await _ensure_authenticated(page, account)
            status, body = await _voyager(
                page, "GET",
                "https://www.linkedin.com/voyager/api/messaging/conversations"
                "?keyVersion=LEGACY_INBOX&q=tags&tags=INBOX&count=20",
            )
            if status != 200:
                return {"events": []}

            data = _parse_json(body)
            events: list[InboundEvent] = []

            for conv in (data.get("elements") or []):
                last_evt = (conv.get("events") or [{}])[-1]
                ts = last_evt.get("createdAt") or conv.get("lastActivityAt") or 0
                if ts <= since_ms:
                    continue

                from_p: dict = {}
                for p in (conv.get("participants") or []):
                    mp = (
                        p.get("com.linkedin.voyager.messaging.MessagingMember") or p
                    )
                    entity = mp.get("miniProfile") or {}
                    if entity.get("publicIdentifier"):
                        from_p = entity
                        break
                if not from_p:
                    continue

                msg_evt = (
                    (last_evt.get("eventContent") or {})
                    .get("com.linkedin.voyager.messaging.event.MessageEvent") or {}
                )
                body_text = (
                    (msg_evt.get("attributedBody") or {}).get("text", "")
                    or msg_evt.get("body", "")
                )
                events.append(InboundEvent(
                    kind="message_received",
                    from_public_id=from_p.get("publicIdentifier"),
                    from_urn=from_p.get("entityUrn") or from_p.get("objectUrn"),
                    occurred_at=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    message_text=body_text or None,
                ))
            return {"events": events}

        try:
            result = await self._run(account, _fn)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:
            logger.warning("inbox_recent_events failed for %s: %s", account.id, exc)
            return []
        return result.get("events", [])
