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
import random
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.services import encryption
from app.services.linkedin.base import (
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


async def _ensure_authenticated(page, account: Any) -> None:
    """Navigate to LinkedIn root then feed.  Raises ChallengeRequired if the
    stored session is dead and no automatic recovery is safe.

    We navigate to the root URL first so LinkedIn can issue a fresh JSESSIONID
    + bcookie for this browser.  Going directly to /feed/ with only li_at can
    trigger a redirect storm.

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

    # Step 2: navigate to feed to confirm the session is active.
    await asyncio.sleep(random.uniform(0.5, 1.5))
    await _goto_safe(page, _LI_FEED, wait_until="domcontentloaded", timeout=30_000)
    await _check_for_challenge(page)
    # Human-like pause after the feed loads.
    await asyncio.sleep(random.uniform(1.5, 4.0))


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
    return { status: res.status, body: text };
}
"""


async def _voyager(page, method: str, url: str, body=None) -> tuple[int, str]:
    result = await page.evaluate(_VOYAGER_JS, [method, url, body])
    return result["status"], result["body"]


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
            # Let LinkedIn's SPA hydrate so embedded URNs land in the DOM.
            await asyncio.sleep(random.uniform(1.5, 3.0))
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
# Provider class
# --------------------------------------------------------------------------


class PlaywrightLinkedInProvider(LinkedInProvider):
    """LinkedIn provider that drives a real headless Chromium browser."""

    async def _run(self, account: Any, fn, *args, **kwargs) -> Any:
        """Launch browser, restore session, run fn(page, account, ...), persist session."""
        from playwright.async_api import async_playwright

        stored = getattr(account, "session_cookies_encrypted", None)
        # init_state is passed to new_context(storage_state=...) — preserves
        # bcookie/bscookie (browser identity) so LinkedIn recognises the session.
        # We strip only JSESSIONID, which is ephemeral server-side state (~30 min)
        # that causes redirect loops when injected into a fresh browser session.
        init_state: dict | None = None
        # extra_cookies is used when we only have a bare li_at (no full state yet).
        extra_cookies: list[dict] = []

        if stored:
            try:
                blob = encryption.decrypt(stored)
                data = json.loads(blob)

                if isinstance(data, dict) and "origins" in data:
                    # Full Playwright storage_state — filter ephemeral cookies.
                    cookies = [c for c in data.get("cookies", [])
                               if c.get("name") not in _EPHEMERAL_COOKIES]
                    init_state = {"cookies": cookies, "origins": data.get("origins", [])}
                    logger.debug(
                        "LinkedIn _run: loading full state for acct %s — %d cookies (%s)",
                        account.id,
                        len(cookies),
                        ", ".join(c["name"] for c in cookies),
                    )
                else:
                    # Bare cookie list (HTTP provider / user-pasted li_at).
                    raw = data.get("cookies", data) if isinstance(data, dict) else data
                    li_at = next(
                        (c.get("value") for c in (raw or []) if c.get("name") == "li_at"),
                        None,
                    )
                    if li_at:
                        extra_cookies = [{
                            "name": "li_at", "value": li_at,
                            "domain": ".linkedin.com", "path": "/",
                            "secure": True, "httpOnly": True, "sameSite": "None",
                        }]
                        logger.debug("LinkedIn _run: bare li_at for acct %s", account.id)
            except Exception as exc:
                logger.warning("LinkedIn session decrypt failed for %s: %s", account.id, exc)

        async with async_playwright() as pw:
            browser = await _launch_browser(pw)
            try:
                context = await _new_context(browser, state=init_state)
                if extra_cookies:
                    await context.add_cookies(extra_cookies)
                page = await context.new_page()
                try:
                    result = await fn(page, account, *args, **kwargs)
                    state = await context.storage_state()
                    account.session_cookies_encrypted = encryption.encrypt(json.dumps(state))
                    # Success → clear any stale challenge state. Status reset
                    # is best-effort (avoids circular import on model enum).
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
                await browser.close()

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
            await _goto_safe(
                page,
                f"https://www.linkedin.com/in/{profile.public_id}/",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await _check_for_challenge(page)
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
                raise LinkedInProviderError(f"follow_profile returned {status}: {body[:200]}")
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
                raise LinkedInProviderError(f"react_to_post returned {status}: {body[:200]}")
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

            # The growth/normInvitations endpoint accepts the public_id (URL slug)
            # directly as `profileId` — this is what the linkedin-api package
            # has used reliably for years.  Going through URN resolution is
            # unnecessary and was the source of "could not resolve profile URN
            # for connect" errors when the Voyager profiles API returned
            # non-200 from a browser context.
            profile_id = profile.public_id
            if not profile_id and profile.urn:
                # Best-effort fallback: extract the FSD ID from a URN.
                profile_id = profile.urn.split(":")[-1]
            if not profile_id:
                raise LinkedInProviderError("send_connect_request requires a public_id")

            payload: dict = {
                "invitee": {
                    "com.linkedin.voyager.growth.invitation.InviteeProfile": {
                        "profileId": profile_id,
                    }
                },
            }
            if note and note.strip():
                payload["message"] = note.strip()[:300]

            status, body = await _voyager(
                page, "POST",
                "https://www.linkedin.com/voyager/api/growth/normInvitations",
                payload,
            )
            if status not in (200, 201, 204):
                raise LinkedInProviderError(f"send_connect returned {status}: {body[:200]}")
            return {"profile_id": profile_id}

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
                raise LinkedInProviderError(f"send_dm returned {status}: {body[:200]}")
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
                raise LinkedInProviderError(f"invite_to_page returned {status}: {body[:200]}")
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
            raise LinkedInProviderError(f"send_inmail returned {status}: {resp_body[:200]}")

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
                    f"comment_on_post returned {status2}: {body2[:200]}"
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
