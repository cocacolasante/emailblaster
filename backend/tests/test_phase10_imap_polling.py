"""Phase 10: IMAP reply polling — fetch + match logic."""
from datetime import datetime, time, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.models import Campaign, Lead
from app.services import imap_client


# --------------------------------------------------------------------------
# fetch_unseen_messages (imaplib mocked)
# --------------------------------------------------------------------------


def _imap_message_bytes(headers: dict[str, str]) -> bytes:
    return ("\r\n".join(f"{k}: {v}" for k, v in headers.items()) + "\r\n").encode()


def test_fetch_unseen_messages_parses_headers():
    msg_bytes = _imap_message_bytes({
        "From": "Jane Doe <jane@external.com>",
        "Subject": "Re: Your reach out",
        "In-Reply-To": "<msg-abc-123>",
        "References": "<msg-abc-123> <other-msg>",
    })

    m = MagicMock()
    m.login.return_value = ("OK", [])
    m.select.return_value = ("OK", [b"1"])
    m.search.return_value = ("OK", [b"42"])
    m.fetch.return_value = ("OK", [(b"42 (RFC822.HEADER {200}", msg_bytes), b")"])
    m.store.return_value = ("OK", [])
    m.logout.return_value = ("BYE", [])

    with patch("app.services.imap_client.IMAP4_SSL", return_value=m):
        msgs = imap_client.fetch_unseen_messages(
            "imap.gmail.com", 993, True, "u@x.com", "pw",
            since=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )

    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["uid"] == "42"
    assert msg["in_reply_to"] == "msg-abc-123"
    assert msg["references"] == ["msg-abc-123", "other-msg"]
    assert msg["subject"] == "Re: Your reach out"
    assert msg["from_email"] == "jane@external.com"


def test_fetch_unseen_messages_marks_seen():
    msg_bytes = _imap_message_bytes({
        "From": "x@y.com", "Subject": "Hi", "In-Reply-To": "<id>",
    })
    m = MagicMock()
    m.search.return_value = ("OK", [b"7"])
    m.select.return_value = ("OK", [b"1"])
    m.fetch.return_value = ("OK", [(b"7 (RFC822.HEADER", msg_bytes)])

    with patch("app.services.imap_client.IMAP4_SSL", return_value=m):
        imap_client.fetch_unseen_messages(
            "h", 993, True, "u", "p", datetime(2026, 5, 1), mark_seen=True,
        )

    m.store.assert_called_with(b"7", "+FLAGS", "\\Seen")


def test_fetch_unseen_no_messages_returns_empty():
    m = MagicMock()
    m.search.return_value = ("OK", [b""])
    m.select.return_value = ("OK", [b"1"])
    with patch("app.services.imap_client.IMAP4_SSL", return_value=m):
        msgs = imap_client.fetch_unseen_messages(
            "h", 993, True, "u", "p", datetime(2026, 5, 1),
        )
    assert msgs == []


# --------------------------------------------------------------------------
# match_message_to_lead (DB-backed)
# --------------------------------------------------------------------------


async def _make_campaign_with_lead(
    db_session, *, brevo_message_id: str, composed_subject: str = "S",
    email: str = "lead@external.com",
) -> Lead:
    c = Campaign(
        name="P10", goal="g", tone="t",
        sender_name="s", sender_email="s@x.com",
        sample_count=1,
        schedule_days=[0, 1, 2, 3, 4],
        schedule_time_start=time(9, 0), schedule_time_end=time(17, 0),
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    l = Lead(
        campaign_id=c.id, email=email,
        composed_subject=composed_subject,
        brevo_message_id=brevo_message_id,
    )
    db_session.add(l)
    await db_session.commit()
    await db_session.refresh(l)
    return l


async def test_match_by_in_reply_to(db_session):
    lead = await _make_campaign_with_lead(db_session, brevo_message_id="msg-AAA")
    msg = {
        "uid": "1",
        "in_reply_to": "msg-AAA",
        "references": [],
        "subject": "Re: anything",
        "from_email": "anyone@x.com",
    }
    matched = await imap_client.match_message_to_lead(
        db_session, msg, [lead.campaign_id]
    )
    assert matched is not None
    assert matched.id == lead.id


async def test_match_by_references_when_in_reply_to_missing(db_session):
    lead = await _make_campaign_with_lead(db_session, brevo_message_id="msg-BBB")
    msg = {
        "uid": "1",
        "in_reply_to": "",
        "references": ["unknown", "msg-BBB"],
        "subject": "Re: x",
        "from_email": "anyone@x.com",
    }
    matched = await imap_client.match_message_to_lead(
        db_session, msg, [lead.campaign_id]
    )
    assert matched is not None
    assert matched.id == lead.id


async def test_match_falls_back_to_subject_and_email(db_session):
    lead = await _make_campaign_with_lead(
        db_session,
        brevo_message_id="msg-CCC",
        composed_subject="Quick question",
        email="lead@external.com",
    )
    msg = {
        "uid": "1",
        "in_reply_to": "",
        "references": [],
        "subject": "Re: Quick question",
        "from_email": "lead@external.com",
    }
    matched = await imap_client.match_message_to_lead(
        db_session, msg, [lead.campaign_id]
    )
    assert matched is not None
    assert matched.id == lead.id


async def test_subject_fallback_requires_email_match(db_session):
    lead = await _make_campaign_with_lead(
        db_session,
        brevo_message_id="msg-DDD",
        composed_subject="Quick question",
        email="lead@external.com",
    )
    msg = {
        "uid": "1",
        "in_reply_to": "",
        "references": [],
        "subject": "Re: Quick question",
        "from_email": "imposter@elsewhere.com",
    }
    matched = await imap_client.match_message_to_lead(
        db_session, msg, [lead.campaign_id]
    )
    assert matched is None


async def test_match_scoped_to_campaign_ids(db_session):
    # Lead is in campaign A; we search only campaign B → no match.
    lead = await _make_campaign_with_lead(db_session, brevo_message_id="msg-EEE")
    other_campaign = Campaign(
        name="Other", goal="g", tone="t",
        sender_name="s", sender_email="s@x.com",
        sample_count=1,
        schedule_days=[0], schedule_time_start=time(9, 0), schedule_time_end=time(17, 0),
    )
    db_session.add(other_campaign)
    await db_session.commit()

    msg = {
        "uid": "1", "in_reply_to": "msg-EEE", "references": [],
        "subject": "Re: x", "from_email": "any@x.com",
    }
    matched = await imap_client.match_message_to_lead(
        db_session, msg, [other_campaign.id]
    )
    assert matched is None
