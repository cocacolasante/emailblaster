"""Hybrid LinkedIn provider.

Routes actions by what actually works from a server IP:

BROWSER (Playwright) — any action where LinkedIn must see a real browser
session, OR where raw HTTP calls fail to get JSESSIONID from server IP:
  - test_connection   — establishes the trusted browser session
  - view_profile      — ghost view only registers if a real browser visits
  - follow_profile    — Voyager POST; browser fingerprint prevents flagging
  - react_to_post     — same
  - send_connect_request, send_dm, invite_to_page, send_inmail,
    comment_on_post   — high-stakes writes

HTTP (linkedin-api) — lightweight data fetches that fail silently if the
server IP is blocked, so the sequence can keep moving:
  - latest_post_urn   — GET, skips gracefully if unavailable
  - inbox_recent_events — polling, returns [] on failure

If you add a residential proxy (LINKEDIN_PROXY_URL) the HTTP bucket can be
expanded again; without one, LinkedIn typically won't issue a JSESSIONID to
server/datacenter IPs even with a valid li_at.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.linkedin.base import (
    ActionResult,
    InboundEvent,
    LinkedInProvider,
    ProfileRef,
)
from app.services.linkedin.linkedin_api_impl import LinkedinApiProvider
from app.services.linkedin.playwright_impl import PlaywrightLinkedInProvider


class HybridLinkedInProvider(LinkedInProvider):
    """Browser for all user-visible LinkedIn actions; HTTP for silent background polls."""

    def __init__(self) -> None:
        self._http = LinkedinApiProvider()
        self._browser = PlaywrightLinkedInProvider()

    # ---- Auth + all interactive actions: Playwright ----

    async def test_connection(self, account: Any) -> ActionResult:
        return await self._browser.test_connection(account)

    async def view_profile(self, account: Any, profile: ProfileRef) -> ActionResult:
        # Ghost views require a real browser visit to register in the
        # lead's "Who viewed your profile" feed.
        return await self._browser.view_profile(account, profile)

    async def follow_profile(self, account: Any, profile: ProfileRef) -> ActionResult:
        return await self._browser.follow_profile(account, profile)

    async def react_to_post(
        self, account: Any, post_urn: str, reaction: str = "LIKE"
    ) -> ActionResult:
        return await self._browser.react_to_post(account, post_urn, reaction)

    async def send_connect_request(
        self, account: Any, profile: ProfileRef, note: str | None = None
    ) -> ActionResult:
        return await self._browser.send_connect_request(account, profile, note)

    async def send_dm(
        self, account: Any, profile: ProfileRef, text: str
    ) -> ActionResult:
        return await self._browser.send_dm(account, profile, text)

    async def invite_to_page(
        self, account: Any, profile: ProfileRef, page_id: str
    ) -> ActionResult:
        return await self._browser.invite_to_page(account, profile, page_id)

    async def send_inmail(
        self, account: Any, profile: ProfileRef, subject: str, body: str,
    ) -> ActionResult:
        return await self._browser.send_inmail(account, profile, subject, body)

    async def comment_on_post(
        self, account: Any, post_urn: str, comment: str,
    ) -> ActionResult:
        return await self._browser.comment_on_post(account, post_urn, comment)

    # ---- Background data fetches: HTTP (fails silently) ----

    async def latest_post_urn(
        self, account: Any, profile: ProfileRef
    ) -> str | None:
        # HTTP: silent failure → sequencer skips react-to-post gracefully.
        # Add LINKEDIN_PROXY_URL to make this reliable from server IP.
        return await self._http.latest_post_urn(account, profile)

    async def inbox_recent_events(
        self, account: Any, since: datetime
    ) -> list[InboundEvent]:
        # HTTP: returns [] on failure — poller just tries again next cycle.
        return await self._http.inbox_recent_events(account, since)
