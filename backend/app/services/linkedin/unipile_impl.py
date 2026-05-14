"""LinkedIn provider backed by Unipile's hosted API.

Unipile runs real desktop Chrome browsers in cloud VMs on residential IPs.
We never handle LinkedIn credentials or run a browser ourselves — the user
links their LinkedIn account once through Unipile's hosted-auth flow and
we just store the resulting ``unipile_account_id``.  All write actions
(connect, DM, follow, etc.) become straightforward HTTPS calls to Unipile.

This replaces the older Playwright/hybrid/HTTP impls which were
permanently fighting LinkedIn's bot-detection.  Real browser + residential
IP defeats the detection layer because LinkedIn sees a genuine human-shaped
session.

SECURITY: this module never touches ``encryption.decrypt`` — there is no
LinkedIn password to decrypt.  The Unipile API key is read from
``settings.UNIPILE_API_KEY`` and sent in an ``X-API-KEY`` header.

API SHAPE NOTES:
Unipile's REST surface uses ``/api/v1/...`` paths and is rooted at
``https://<DSN>`` where DSN is the customer's tenant host (e.g.
``api12.unipile.com:13443``).  Some endpoint paths and payload shapes
below are documented as the canonical form Unipile publishes — they
occasionally evolve.  All of them are confined to this module, so when
Unipile changes one we only change one place.  When a method maps to
multiple Unipile endpoints (e.g. send_dm: find chat → send message), the
sequence is spelled out in each method.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.services.linkedin.base import (
    AccountRestricted,
    ActionResult,
    ChallengeRequired,
    InboundEvent,
    LinkedInProvider,
    LinkedInProviderError,
    ProfileRef,
)

logger = logging.getLogger(__name__)

# -- HTTP timeouts --------------------------------------------------------
# Reads can be fast; writes can include LinkedIn's slow Voyager calls
# behind Unipile's proxy.  Account creation polls until LinkedIn either
# returns a session or asks for a checkpoint, which can take 30-60s.
_REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_LONG_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class UnipileError(LinkedInProviderError):
    """Unipile API returned a non-2xx response we can't recover from.

    Attributes ``status``, ``code``, and ``body`` carry the raw signal so
    upstream callers (sequencer, router) can branch on them.
    """

    def __init__(self, status: int, code: str | None, body: str, message: str | None = None):
        self.status = status
        self.code = code
        self.body = body
        super().__init__(message or f"unipile {status} {code or ''}: {body[:200]}")


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


class UnipileLinkedInProvider(LinkedInProvider):
    """LinkedInProvider impl that delegates every action to Unipile."""

    PROVIDER = "LINKEDIN"

    def __init__(
        self,
        *,
        dsn: str | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._dsn = dsn or settings.UNIPILE_DSN
        self._api_key = api_key or settings.UNIPILE_API_KEY
        # Tests inject httpx.MockTransport here.
        self._transport = transport

    # ----- HTTP plumbing -------------------------------------------------

    def _base_url(self) -> str:
        if not self._dsn:
            raise UnipileError(
                0, "config_missing",
                "UNIPILE_DSN not set — required when LINKEDIN_PROVIDER=unipile.",
                message="Unipile is not configured (UNIPILE_DSN missing).",
            )
        # DSN already includes the host (and usually a non-443 port).
        # If the caller supplied a bare host we still want https.
        if "://" in self._dsn:
            return self._dsn.rstrip("/")
        return f"https://{self._dsn}".rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise UnipileError(
                0, "config_missing",
                "UNIPILE_API_KEY not set — required when LINKEDIN_PROVIDER=unipile.",
                message="Unipile is not configured (UNIPILE_API_KEY missing).",
            )
        return {
            "X-API-KEY": self._api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _client(self, *, long: bool = False) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "base_url": self._base_url(),
            "headers": self._headers(),
            "timeout": _LONG_TIMEOUT if long else _REQUEST_TIMEOUT,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        long: bool = False,
    ) -> dict[str, Any] | list[Any]:
        """Issue a single request and normalize Unipile's error envelope."""
        async with self._client(long=long) as client:
            try:
                resp = await client.request(method, path, params=params, json=json)
            except httpx.RequestError as exc:
                logger.warning("Unipile %s %s network error: %s", method, path, exc)
                raise UnipileError(
                    0, "network",
                    str(exc),
                    message=f"unipile network error: {exc}",
                ) from exc

        if resp.status_code == 204:
            return {}

        text = resp.text
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = None

        if 200 <= resp.status_code < 300:
            if isinstance(body, (dict, list)):
                return body
            return {"raw": text}

        # Error path — Unipile envelopes typically look like:
        #   {"status": 422, "type": "errors/checkpoint", "title": "...", "detail": "..."}
        # or
        #   {"code": 422, "type": "...", "title": "...", "message": "..."}
        code: str | None = None
        message: str | None = None
        if isinstance(body, dict):
            code = body.get("type") or body.get("code") or body.get("error")
            message = body.get("detail") or body.get("title") or body.get("message")
        if not message:
            message = text[:300]
        logger.warning(
            "Unipile %s %s -> %s code=%s body=%r",
            method, path, resp.status_code, code, text[:300],
        )
        self._raise_for_special_codes(resp.status_code, code, text)
        raise UnipileError(resp.status_code, code, text, message=message)

    @staticmethod
    def _raise_for_special_codes(status: int, code: str | None, body: str) -> None:
        """Map Unipile error codes to our domain exceptions.

        Unipile signals:
          - checkpoint required: status 4xx + type/code mentioning "checkpoint"
          - account disconnected / credentials invalid: type mentioning
            "disconnected" or "credentials"
          - account banned by provider: type/title mentioning "restricted"
            or "banned"
        """
        haystack = " ".join(filter(None, [str(code or ""), body[:300].lower()]))
        haystack = haystack.lower()
        if "checkpoint" in haystack or "captcha" in haystack or "2fa" in haystack:
            raise ChallengeRequired(
                f"Unipile reports LinkedIn checkpoint required ({status})",
                challenge_url="https://www.linkedin.com",
            )
        if "restricted" in haystack or "banned" in haystack or "suspended" in haystack:
            raise AccountRestricted(
                f"Unipile reports LinkedIn account restricted ({status})"
            )

    # ----- Account-level helpers (used by router + provider methods) -----

    @staticmethod
    def _account_id(account: Any) -> str:
        aid = getattr(account, "unipile_account_id", None)
        if not aid:
            raise UnipileError(
                0, "no_account",
                "LinkedInAccount has no unipile_account_id; finish the hosted login flow first.",
                message="LinkedIn account has not completed the Unipile hosted login.",
            )
        return aid

    async def create_hosted_auth_link(
        self,
        *,
        success_redirect_url: str,
        failure_redirect_url: str | None = None,
        notify_url: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Ask Unipile for a hosted-auth URL the user can open in their browser.

        Used by the linkedin-accounts router when the user clicks
        "Connect via Unipile".  Unipile returns ``{ "url": "...", "id": "..." }``
        — the URL we open in a new tab, the ID we'll later match against
        webhook ``account.connected`` events to persist
        ``unipile_account_id``.
        """
        payload: dict[str, Any] = {
            "type": "create",
            "providers": [self.PROVIDER],
            "api_url": self._base_url(),
            "success_redirect_url": success_redirect_url,
        }
        if failure_redirect_url:
            payload["failure_redirect_url"] = failure_redirect_url
        if notify_url:
            payload["notify_url"] = notify_url
        if name:
            payload["name"] = name
        result = await self._request("POST", "/api/v1/hosted/accounts/link", json=payload)
        return result if isinstance(result, dict) else {"raw": result}

    async def fetch_account_status(self, unipile_account_id: str) -> dict[str, Any]:
        """Return Unipile's view of the account (status, provider, last error)."""
        result = await self._request("GET", f"/api/v1/accounts/{unipile_account_id}")
        return result if isinstance(result, dict) else {"raw": result}

    async def delete_account(self, unipile_account_id: str) -> None:
        """Tear down a linked account on Unipile's side (user removed it locally)."""
        await self._request("DELETE", f"/api/v1/accounts/{unipile_account_id}")

    # ----- LinkedInProvider abstract methods -----------------------------

    async def test_connection(self, account: Any) -> ActionResult:
        """Verify the linked Unipile account is still authenticated.

        We just hit Unipile's account-status endpoint.  Unipile owns the
        LinkedIn session; if it can't reach LinkedIn (checkpoint, expired
        cookies, etc.) it surfaces that through the account.status field.
        """
        try:
            aid = self._account_id(account)
            status = await self.fetch_account_status(aid)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            return ActionResult(ok=False, error=str(exc), meta={"challenged": True})
        except UnipileError as exc:
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})

        # Unipile statuses we care about:
        #   "OK" / "CONNECTED" / "ACTIVE"        — good
        #   "CREDENTIALS" / "DISCONNECTED"       — re-auth needed
        #   "CHECKPOINT"                         — challenge
        #   "BANNED" / "RESTRICTED"              — penalty box
        srcstatus = str(status.get("status") or status.get("connection_status") or "").upper()
        if srcstatus in {"OK", "CONNECTED", "ACTIVE"}:
            return ActionResult(ok=True, meta=status)
        if "CHECKPOINT" in srcstatus or "2FA" in srcstatus:
            account.pending_challenge_url = "https://www.linkedin.com"
            return ActionResult(
                ok=False, error=f"unipile status={srcstatus}", meta={"challenged": True, **status},
            )
        if "BANNED" in srcstatus or "RESTRICTED" in srcstatus or "SUSPENDED" in srcstatus:
            return ActionResult(
                ok=False, error=f"unipile status={srcstatus}",
                meta={"restricted": True, **status},
            )
        return ActionResult(ok=False, error=f"unipile status={srcstatus or 'unknown'}", meta=status)

    async def view_profile(self, account: Any, profile: ProfileRef) -> ActionResult:
        """Register a profile view (ghost view) via Unipile's user-fetch endpoint.

        Unipile's ``GET /users/{provider_id}?account_id=X`` fetches the
        profile through the user's session, which surfaces in LinkedIn's
        "Who viewed your profile" feed the same way a manual visit does.
        """
        try:
            aid = self._account_id(account)
            provider_id = profile.public_id or profile.urn
            if not provider_id:
                return ActionResult(ok=False, error="view_profile requires a public_id or urn")
            data = await self._request(
                "GET", f"/api/v1/users/{provider_id}",
                params={"account_id": aid},
            )
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except UnipileError as exc:
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})

        urn = None
        if isinstance(data, dict):
            urn = data.get("provider_id") or data.get("urn") or data.get("id")
        return ActionResult(ok=True, external_id=urn, meta=data if isinstance(data, dict) else None)

    async def follow_profile(self, account: Any, profile: ProfileRef) -> ActionResult:
        try:
            aid = self._account_id(account)
            provider_id = profile.public_id or profile.urn
            if not provider_id:
                return ActionResult(ok=False, error="follow_profile requires a public_id or urn")
            data = await self._request(
                "POST", f"/api/v1/users/{provider_id}/follow",
                params={"account_id": aid},
            )
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except UnipileError as exc:
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})
        return ActionResult(ok=True, external_id=provider_id, meta=data if isinstance(data, dict) else None)

    async def react_to_post(
        self, account: Any, post_urn: str, reaction: str = "LIKE"
    ) -> ActionResult:
        try:
            aid = self._account_id(account)
            data = await self._request(
                "POST", f"/api/v1/posts/{post_urn}/reactions",
                params={"account_id": aid},
                json={"type": (reaction or "LIKE").upper()},
            )
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except UnipileError as exc:
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})
        return ActionResult(ok=True, external_id=post_urn, meta=data if isinstance(data, dict) else None)

    async def latest_post_urn(self, account: Any, profile: ProfileRef) -> str | None:
        try:
            aid = self._account_id(account)
            provider_id = profile.public_id or profile.urn
            if not provider_id:
                return None
            data = await self._request(
                "GET", f"/api/v1/users/{provider_id}/posts",
                params={"account_id": aid, "limit": 1},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unipile latest_post_urn failed: %s", exc)
            return None
        items: list[Any] = []
        if isinstance(data, dict):
            items = data.get("items") or data.get("posts") or []
        elif isinstance(data, list):
            items = data
        if not items:
            return None
        first = items[0] if isinstance(items[0], dict) else {}
        return first.get("urn") or first.get("provider_id") or first.get("id")

    async def send_connect_request(
        self, account: Any, profile: ProfileRef, note: str | None = None
    ) -> ActionResult:
        try:
            aid = self._account_id(account)
            provider_id = profile.public_id or profile.urn
            if not provider_id:
                return ActionResult(ok=False, error="send_connect_request requires a public_id or urn")
            payload: dict[str, Any] = {
                "provider_id": provider_id,
                "account_id": aid,
            }
            if note and note.strip():
                # LinkedIn enforces 200 char cap for Free, 300 for Premium.
                # 200 is the safe floor.
                payload["message"] = note.strip()[:200]
            data = await self._request(
                "POST", "/api/v1/users/invite",
                json=payload,
                long=True,
            )
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except UnipileError as exc:
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})
        return ActionResult(ok=True, external_id=provider_id, meta=data if isinstance(data, dict) else None)

    async def send_dm(self, account: Any, profile: ProfileRef, text: str) -> ActionResult:
        try:
            aid = self._account_id(account)
            provider_id = profile.public_id or profile.urn
            if not provider_id:
                return ActionResult(ok=False, error="send_dm requires a public_id or urn")
            # Unipile's "start chat" endpoint takes the attendee provider_id and
            # the message body in one call (no need to look up an existing chat).
            data = await self._request(
                "POST", "/api/v1/chats",
                json={
                    "account_id": aid,
                    "attendees_ids": [provider_id],
                    "text": text,
                },
                long=True,
            )
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except UnipileError as exc:
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})

        msg_id = None
        if isinstance(data, dict):
            msg_id = data.get("message_id") or data.get("chat_id") or data.get("id")
        return ActionResult(ok=True, external_id=msg_id, meta=data if isinstance(data, dict) else None)

    async def invite_to_page(
        self, account: Any, profile: ProfileRef, page_id: str
    ) -> ActionResult:
        try:
            aid = self._account_id(account)
            provider_id = profile.public_id or profile.urn
            if not provider_id:
                return ActionResult(ok=False, error="invite_to_page requires a public_id or urn")
            data = await self._request(
                "POST", f"/api/v1/companies/{page_id}/invite",
                params={"account_id": aid},
                json={"provider_id": provider_id},
            )
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except UnipileError as exc:
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})
        return ActionResult(ok=True, external_id=provider_id, meta=data if isinstance(data, dict) else None)

    async def send_inmail(
        self, account: Any, profile: ProfileRef, subject: str, body: str
    ) -> ActionResult:
        try:
            aid = self._account_id(account)
            provider_id = profile.public_id or profile.urn
            if not provider_id:
                return ActionResult(ok=False, error="send_inmail requires a public_id or urn")
            data = await self._request(
                "POST", "/api/v1/chats",
                json={
                    "account_id": aid,
                    "attendees_ids": [provider_id],
                    "text": body,
                    "subject": subject,
                    "inmail": True,
                },
                long=True,
            )
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except UnipileError as exc:
            # Unipile signals "no InMail credits / not Premium" via a specific
            # error code; surface that distinctly so the sequencer can route
            # it to a clean "premium_required" skip rather than a hard failure.
            if exc.code and ("premium" in str(exc.code).lower() or "inmail" in str(exc.code).lower()):
                return ActionResult(
                    ok=False, error=str(exc),
                    meta={"premium_required": True, "code": exc.code},
                )
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})
        return ActionResult(ok=True, external_id=provider_id, meta=data if isinstance(data, dict) else None)

    async def comment_on_post(
        self, account: Any, post_urn: str, comment: str
    ) -> ActionResult:
        try:
            aid = self._account_id(account)
            data = await self._request(
                "POST", f"/api/v1/posts/{post_urn}/comments",
                params={"account_id": aid},
                json={"text": comment},
            )
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except UnipileError as exc:
            return ActionResult(ok=False, error=str(exc), meta={"code": exc.code})
        return ActionResult(ok=True, external_id=post_urn, meta=data if isinstance(data, dict) else None)

    async def inbox_recent_events(
        self, account: Any, since: datetime
    ) -> list[InboundEvent]:
        """Used as a polling fallback when webhooks aren't reaching us.

        With Unipile, the preferred path is the /webhooks/unipile endpoint —
        Unipile pushes events to us in real time so we don't need to poll.
        This method stays available for backfill / dev / one-shot resync.
        """
        try:
            aid = self._account_id(account)
            since_iso = since.astimezone(timezone.utc).isoformat()
            data = await self._request(
                "GET", "/api/v1/chats",
                params={"account_id": aid, "limit": 20, "after": since_iso},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unipile inbox_recent_events failed: %s", exc)
            return []

        chats: list[Any] = []
        if isinstance(data, dict):
            chats = data.get("items") or data.get("chats") or []
        elif isinstance(data, list):
            chats = data

        events: list[InboundEvent] = []
        for chat in chats:
            if not isinstance(chat, dict):
                continue
            last_msg = chat.get("last_message") or {}
            ts_raw = last_msg.get("timestamp") or chat.get("updated_at") or chat.get("created_at")
            occurred_at = _parse_iso(ts_raw) or since
            if occurred_at <= since:
                continue
            sender = last_msg.get("sender") or {}
            from_public_id = sender.get("provider_id") or sender.get("public_identifier")
            from_urn = sender.get("urn") or sender.get("provider_urn")
            events.append(InboundEvent(
                kind="message_received",
                from_public_id=from_public_id,
                from_urn=from_urn,
                occurred_at=occurred_at,
                message_text=last_msg.get("text") or last_msg.get("body"),
                meta={"chat_id": chat.get("id") or chat.get("chat_id")},
            ))
        return events


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value)
        # Some providers emit Z; datetime.fromisoformat needs +00:00
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None
