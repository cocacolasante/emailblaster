"""DIY LinkedIn provider — wraps the `linkedin-api` package.

SECURITY INVARIANT: This is one of two modules allowed to call
``encryption.decrypt(`` (the other is `app/services/imap_client.py`).
The plaintext password / cookies live in local scope only and are
``del``'d before the wrapper returns.

The upstream library is synchronous (uses `requests`). We wrap every call
in ``asyncio.to_thread`` so it doesn't block the FastAPI event loop.

Coverage in M2:
- view_profile       → linkedin_api.Linkedin.get_profile(public_id)
- follow_profile     → raw Voyager call (linkedin-api lacks a follow helper)
- react_to_post      → raw Voyager call
- latest_post_urn    → get_profile_posts → first URN
- inbox_recent_events → get_invitations + get_conversations

Cookie persistence:
- On first login we instantiate Linkedin(email, password). After success,
  the .client.session.cookies has the auth tokens; we serialize them as
  JSON and Fernet-encrypt them onto the account row.
- On subsequent calls we pass the cookies in via a RequestsCookieJar so
  we don't re-login.
- On 401/expired we wipe the stored cookies and re-login once.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from requests.cookies import RequestsCookieJar

from app.config import settings
from app.services import encryption
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


# --------------------------------------------------------------------------
# Helpers for cookie marshaling
# --------------------------------------------------------------------------


def _cookies_to_json(jar: RequestsCookieJar) -> str:
    return json.dumps(
        [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
         for c in jar]
    )


def _cookies_from_json(blob: str) -> RequestsCookieJar:
    jar = RequestsCookieJar()
    for c in json.loads(blob):
        jar.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    return jar


def _proxies_for(account: Any) -> dict[str, str]:
    """Build a `requests`-style proxies dict.

    Per-account override takes priority over the global setting. Empty
    string ⇒ no proxy.
    """
    url = getattr(account, "proxy_url", None) or settings.LINKEDIN_PROXY_URL
    return {"http": url, "https": url} if url else {}


# --------------------------------------------------------------------------
# Sync core — runs inside `to_thread`. All plaintext lives here.
# --------------------------------------------------------------------------


def _build_client_sync(account: Any) -> tuple[Any, bool]:
    """Construct a ``linkedin_api.Linkedin`` client for the account.

    Returns ``(client, did_relogin)`` — did_relogin=True means we created
    a fresh session and the caller must persist the new cookies.

    Raises ``ChallengeRequired`` / ``AccountRestricted`` on the obvious
    failure modes.
    """
    # Late import so missing dep doesn't break the whole package at module-load.
    from linkedin_api import Linkedin
    from linkedin_api.client import ChallengeException

    proxies = _proxies_for(account)
    stored_cookies = getattr(account, "session_cookies_encrypted", None)

    # Try cookies-only path first.
    if stored_cookies:
        try:
            cookie_jar = _cookies_from_json(encryption.decrypt(stored_cookies))
        except Exception as exc:  # noqa: BLE001 — corrupt/wrong key
            logger.warning("LinkedIn cookie decrypt failed for %s: %s", account.id, exc)
            cookie_jar = None
        else:
            try:
                client = Linkedin(
                    account.linkedin_email, "",
                    authenticate=False,
                    proxies=proxies,
                    cookies=cookie_jar,
                )
                # Cheap sanity request — this raises on bad session.
                client.get_user_profile(use_cache=False)
                return client, False
            except ChallengeException as ce:
                raise ChallengeRequired(str(ce)) from ce
            except Exception as exc:  # noqa: BLE001 — fall through to fresh login
                logger.info(
                    "LinkedIn cookie auth failed for account %s, will re-login: %s",
                    account.id, exc,
                )

    # Fresh login. Password is decrypted here, lives in `password`, and is
    # deleted at the end of this function regardless of outcome.
    password = encryption.decrypt(account.password_encrypted)
    try:
        try:
            client = Linkedin(
                account.linkedin_email, password,
                authenticate=True, proxies=proxies,
            )
        except ChallengeException as ce:
            raise ChallengeRequired(str(ce)) from ce
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "challenge" in msg or "checkpoint" in msg or "captcha" in msg:
                raise ChallengeRequired(str(exc)) from exc
            if "restricted" in msg or "suspended" in msg:
                raise AccountRestricted(str(exc)) from exc
            raise LinkedInProviderError(f"LinkedIn login failed: {exc}") from exc
    finally:
        del password

    return client, True


def _profile_view_sync(account: Any, profile: ProfileRef) -> dict[str, Any]:
    client, did_relogin = _build_client_sync(account)
    if not profile.public_id:
        raise LinkedInProviderError("view_profile requires a public_id (or a URL we can parse)")
    data = client.get_profile(public_id=profile.public_id)
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "urn": data.get("profile_id") or data.get("urn_id"),
    }


def _follow_profile_sync(account: Any, profile: ProfileRef) -> dict[str, Any]:
    """Follow a profile via raw Voyager call.

    The linkedin-api package doesn't ship a follow_profile helper, so we
    call the underlying session directly. The endpoint mirrors what the
    web client uses; it can break if LinkedIn changes it.
    """
    client, did_relogin = _build_client_sync(account)
    profile_urn = profile.urn
    if not profile_urn:
        data = client.get_profile(public_id=profile.public_id)
        profile_urn = data.get("profile_id") or data.get("urn_id")
    if not profile_urn:
        raise LinkedInProviderError("follow_profile could not resolve profile URN")

    # Normalize bare ID into a full URN if needed.
    if not profile_urn.startswith("urn:"):
        profile_urn = f"urn:li:fsd_profile:{profile_urn}"

    res = client.client.session.post(
        "https://www.linkedin.com/voyager/api/feed/dash/followingStates",
        data=json.dumps({
            "patch": {"$set": {"following": True}},
            "entityUrn": profile_urn,
        }),
        headers={"content-type": "application/json"},
    )
    if res.status_code not in (200, 201, 204):
        raise LinkedInProviderError(f"follow_profile returned {res.status_code}: {res.text[:200]}")
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "urn": profile_urn,
    }


def _react_to_post_sync(account: Any, post_urn: str, reaction: str) -> dict[str, Any]:
    client, did_relogin = _build_client_sync(account)
    reaction = (reaction or "LIKE").upper()
    res = client.client.session.post(
        f"https://www.linkedin.com/voyager/api/feed/reactions?threadUrn={post_urn}",
        data=json.dumps({"reactionType": reaction}),
        headers={"content-type": "application/json"},
    )
    if res.status_code not in (200, 201, 204):
        raise LinkedInProviderError(f"react_to_post returned {res.status_code}: {res.text[:200]}")
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "urn": post_urn,
        "reaction": reaction,
    }


def _latest_post_urn_sync(account: Any, profile: ProfileRef) -> dict[str, Any]:
    client, did_relogin = _build_client_sync(account)
    posts = client.get_profile_posts(
        public_id=profile.public_id, post_count=1,
    )
    urn = None
    if posts:
        first = posts[0]
        urn = first.get("urn") or first.get("entityUrn")
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "urn": urn,
    }


def _send_connect_sync(account: Any, profile: ProfileRef, note: str | None) -> dict[str, Any]:
    """Send a connection request. ``note`` is optional and capped at 300 chars
    by LinkedIn — caller is expected to validate; we truncate defensively.
    """
    client, did_relogin = _build_client_sync(account)
    if not profile.public_id:
        raise LinkedInProviderError("send_connect_request requires a public_id")
    safe_note = (note or "").strip()[:300]
    # linkedin-api's add_connection signature: (profile_public_id, message='')
    res = client.add_connection(profile.public_id, message=safe_note or "")
    # Library returns True on failure (yes really, see upstream README). We
    # invert here so True = success in our wrapper.
    sent = res is False
    if not sent:
        raise LinkedInProviderError("send_connect_request rejected by LinkedIn")
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "profile_public_id": profile.public_id,
        "note_sent": bool(safe_note),
    }


def _send_dm_sync(account: Any, profile: ProfileRef, text: str) -> dict[str, Any]:
    """Send a DM to a 1st-degree connection. Resolves the URN if needed.

    Caller (the sequencer) is responsible for the 1st-degree check based on
    ``lead.linkedin_connection_status`` — we don't re-check here because a
    failed send doesn't always mean "not connected" (could be many things).
    """
    client, did_relogin = _build_client_sync(account)
    profile_urn = profile.urn
    if not profile_urn and profile.public_id:
        data = client.get_profile(public_id=profile.public_id)
        profile_urn = data.get("profile_id") or data.get("urn_id")
    if not profile_urn:
        raise LinkedInProviderError("send_dm could not resolve profile URN")
    if not profile_urn.startswith("urn:"):
        profile_urn = f"urn:li:fsd_profile:{profile_urn}"

    # linkedin-api's send_message takes recipient URNs in `recipients=`.
    res = client.send_message(message_body=text, recipients=[profile_urn])
    # Same as add_connection — True = error, False = success in upstream.
    sent = res is False
    if not sent:
        raise LinkedInProviderError("send_dm rejected by LinkedIn")
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "profile_urn": profile_urn,
    }


def _invite_to_page_sync(account: Any, profile: ProfileRef, page_id: str) -> dict[str, Any]:
    """Invite a 1st-degree connection to follow a company page.

    LinkedIn's web client uses the `growth/normInvitations` endpoint with
    `inviteeUrn` + `inviterCompanyOrganizationUrn`. The current account
    must be an admin of the page; otherwise LinkedIn returns 403.
    """
    client, did_relogin = _build_client_sync(account)
    profile_urn = profile.urn
    if not profile_urn and profile.public_id:
        data = client.get_profile(public_id=profile.public_id)
        profile_urn = data.get("profile_id") or data.get("urn_id")
    if not profile_urn:
        raise LinkedInProviderError("invite_to_page could not resolve profile URN")
    if not profile_urn.startswith("urn:"):
        profile_urn = f"urn:li:fsd_profile:{profile_urn}"

    org_urn = f"urn:li:fsd_company:{page_id}"
    payload = {
        "inviteeUnion": {"memberProfile": profile_urn},
        "inviterOrganization": org_urn,
    }
    res = client.client.session.post(
        "https://www.linkedin.com/voyager/api/growth/normInvitations",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    if res.status_code not in (200, 201, 204):
        raise LinkedInProviderError(
            f"invite_to_page returned {res.status_code}: {res.text[:200]}"
        )
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "profile_urn": profile_urn,
        "page_id": page_id,
    }


def _send_inmail_sync(
    account: Any, profile: ProfileRef, subject: str, body: str,
) -> dict[str, Any]:
    """Send an InMail.

    Requires LinkedIn Premium / Sales Navigator on the calling account.
    The endpoint is the same messaging API used for DMs but with the
    `INMAIL` subtype and a non-empty subject line. If the account lacks
    InMail credits, LinkedIn returns 403 with a body that includes
    "INMAIL" or "premium" — we map that to a ``premium_required`` flag
    that the sequencer turns into a clear skip row.
    """
    client, did_relogin = _build_client_sync(account)
    profile_urn = profile.urn
    if not profile_urn and profile.public_id:
        data = client.get_profile(public_id=profile.public_id)
        profile_urn = data.get("profile_id") or data.get("urn_id")
    if not profile_urn:
        raise LinkedInProviderError("send_inmail could not resolve profile URN")
    if not profile_urn.startswith("urn:"):
        profile_urn = f"urn:li:fsd_profile:{profile_urn}"

    payload = {
        "message": {
            "body": {"text": body, "attributes": []},
            "renderContentUnions": [],
        },
        "subtype": "INMAIL",
        "subject": subject,
        "recipientUrns": [profile_urn],
    }
    res = client.client.session.post(
        "https://www.linkedin.com/voyager/api/voyagerMessagingDashMessengerMessages?action=createMessage",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    if res.status_code in (200, 201, 204):
        return {
            "ok": True,
            "did_relogin": did_relogin,
            "cookies": _cookies_to_json(client.client.session.cookies),
            "profile_urn": profile_urn,
        }
    text = (res.text or "")[:300]
    lower = text.lower()
    if "inmail" in lower or "premium" in lower or res.status_code == 402:
        # Distinct error class so callers can route this to the user
        # ("you need Premium / no credits left") rather than treating it
        # as a generic failure.
        return {
            "ok": False,
            "did_relogin": did_relogin,
            "cookies": _cookies_to_json(client.client.session.cookies),
            "premium_required": True,
            "error": f"InMail unavailable ({res.status_code}): {text}",
        }
    raise LinkedInProviderError(f"send_inmail returned {res.status_code}: {text}")


def _comment_post_sync(account: Any, post_urn: str, comment: str) -> dict[str, Any]:
    """Comment on a post via Voyager.

    Endpoint: ``POST /voyager/api/feed/dash/socialActions/{post-urn}/comments``
    Body: ``{"actor": "<viewer-urn>", "value": {"text": "..."}}``
    """
    client, did_relogin = _build_client_sync(account)
    viewer = client.get_user_profile(use_cache=True) or {}
    viewer_urn = (
        viewer.get("plainId")
        or viewer.get("entityUrn")
        or viewer.get("publicIdentifier")
    )
    if viewer_urn and not str(viewer_urn).startswith("urn:"):
        viewer_urn = f"urn:li:fsd_profile:{viewer_urn}"

    payload = {
        "actor": viewer_urn,
        "value": {
            "com.linkedin.voyager.feed.shared.AnnotatedText": {
                "values": [], "text": comment,
            },
        },
    }
    # The post URN is part of the URL path; URL-encode it.
    from urllib.parse import quote
    res = client.client.session.post(
        f"https://www.linkedin.com/voyager/api/feed/dash/socialActions/{quote(post_urn, safe='')}/comments",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    if res.status_code not in (200, 201, 204):
        raise LinkedInProviderError(
            f"comment_post returned {res.status_code}: {res.text[:200]}"
        )
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "post_urn": post_urn,
    }


def _inbox_recent_sync(account: Any, since: datetime) -> dict[str, Any]:
    client, did_relogin = _build_client_sync(account)
    events: list[InboundEvent] = []
    since_ms = int(since.timestamp() * 1000)

    # Connection accepts arrive as fresh "first-degree" badges; the
    # cleanest signal we have is the invitations list flipping from
    # pending to accepted. linkedin-api exposes get_invitations() which
    # returns SENT invites + their state. We also list inbound DMs.
    try:
        convos = client.get_conversations()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LinkedIn get_conversations failed for %s: %s", account.id, exc)
        convos = {}

    for c in (convos.get("elements") or []):
        last_event = c.get("events", [{}])[-1]
        ts = last_event.get("createdAt") or c.get("lastActivityAt") or 0
        if ts <= since_ms:
            continue
        # Sender info nested differently per API version.
        participants = c.get("participants", []) or []
        from_participant = None
        for p in participants:
            mp = p.get("com.linkedin.voyager.messaging.MessagingMember") or p
            entity = mp.get("miniProfile") or {}
            from_participant = entity
            if entity.get("publicIdentifier"):
                break
        if from_participant is None:
            continue
        body = ""
        msg = last_event.get("eventContent", {}) or {}
        msg_event = msg.get("com.linkedin.voyager.messaging.event.MessageEvent") or {}
        attributed = msg_event.get("attributedBody", {}) or {}
        body = attributed.get("text", "") or msg_event.get("body", "")
        events.append(InboundEvent(
            kind="message_received",
            from_public_id=from_participant.get("publicIdentifier"),
            from_urn=from_participant.get("entityUrn") or from_participant.get("objectUrn"),
            occurred_at=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
            message_text=body or None,
        ))

    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
        "events": events,
    }


# --------------------------------------------------------------------------
# Provider impl — async wrappers + cookie persistence
# --------------------------------------------------------------------------


class LinkedinApiProvider(LinkedInProvider):

    async def _run(self, account: Any, fn, *args, **kwargs) -> dict[str, Any]:
        """Run a sync helper inside a thread, persist refreshed cookies."""
        result = await asyncio.to_thread(fn, account, *args, **kwargs)
        cookies = result.pop("cookies", None)
        if cookies:
            # Persist updated cookies onto the account. Caller's session
            # commits.
            account.session_cookies_encrypted = encryption.encrypt(cookies)
        return result

    async def test_connection(self, account: Any) -> ActionResult:
        try:
            result = await self._run(account, _build_client_sync_wrapper)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            return ActionResult(ok=False, error=f"challenge required: {exc}", meta={"challenged": True})
        except AccountRestricted as exc:
            return ActionResult(ok=False, error=f"account restricted: {exc}", meta={"restricted": True})
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, meta=result)

    async def view_profile(self, account: Any, profile: ProfileRef) -> ActionResult:
        try:
            result = await self._run(account, _profile_view_sync, profile)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("urn"), meta=result)

    async def follow_profile(self, account: Any, profile: ProfileRef) -> ActionResult:
        try:
            result = await self._run(account, _follow_profile_sync, profile)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("urn"), meta=result)

    async def react_to_post(
        self, account: Any, post_urn: str, reaction: str = "LIKE"
    ) -> ActionResult:
        try:
            result = await self._run(account, _react_to_post_sync, post_urn, reaction)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=post_urn, meta=result)

    async def latest_post_urn(
        self, account: Any, profile: ProfileRef
    ) -> str | None:
        try:
            result = await self._run(account, _latest_post_urn_sync, profile)
        except Exception as exc:  # noqa: BLE001
            logger.warning("latest_post_urn failed for %s: %s", account.id, exc)
            return None
        return result.get("urn")

    # ---- M3 write actions ----

    async def send_connect_request(
        self, account: Any, profile: ProfileRef, note: str | None = None
    ) -> ActionResult:
        try:
            result = await self._run(account, _send_connect_sync, profile, note)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(
            ok=True,
            external_id=result.get("profile_public_id"),
            meta=result,
        )

    async def send_dm(
        self, account: Any, profile: ProfileRef, text: str
    ) -> ActionResult:
        try:
            result = await self._run(account, _send_dm_sync, profile, text)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("profile_urn"), meta=result)

    async def invite_to_page(
        self, account: Any, profile: ProfileRef, page_id: str
    ) -> ActionResult:
        try:
            result = await self._run(account, _invite_to_page_sync, profile, page_id)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=result.get("profile_urn"), meta=result)

    # ---- M4 write actions ----

    async def send_inmail(
        self, account: Any, profile: ProfileRef, subject: str, body: str,
    ) -> ActionResult:
        """Send an InMail. Returns ok=False with meta.premium_required=True
        when the account lacks Premium / InMail credits — caller routes
        that to a skipped execution row with a clear message.
        """
        try:
            result = await self._run(account, _send_inmail_sync, profile, subject, body)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        if not result.get("ok"):
            return ActionResult(
                ok=False,
                meta=result,
                error=result.get("error", "InMail failed"),
            )
        return ActionResult(ok=True, external_id=result.get("profile_urn"), meta=result)

    async def comment_on_post(
        self, account: Any, post_urn: str, comment: str,
    ) -> ActionResult:
        try:
            result = await self._run(account, _comment_post_sync, post_urn, comment)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, error=str(exc))
        return ActionResult(ok=True, external_id=post_urn, meta=result)

    async def inbox_recent_events(
        self, account: Any, since: datetime
    ) -> list[InboundEvent]:
        try:
            result = await self._run(account, _inbox_recent_sync, since)
        except ChallengeRequired as exc:
            account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("inbox_recent_events failed for %s: %s", account.id, exc)
            return []
        return result.get("events", [])


def _build_client_sync_wrapper(account: Any) -> dict[str, Any]:
    """Plain test that just builds the client. Used by test_connection."""
    client, did_relogin = _build_client_sync(account)
    return {
        "ok": True,
        "did_relogin": did_relogin,
        "cookies": _cookies_to_json(client.client.session.cookies),
    }
