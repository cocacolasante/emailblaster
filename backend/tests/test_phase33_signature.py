"""Phase 33: signature apply service (pure function)."""
from app.services.signature import apply_signature

SIG = "Anthony Colasante\n555-123-4567\nacme.com\ncal.com/anthony"


def test_replaces_multiline_signoff():
    body = "Hi Jane,\n\nLove what Acme does.\n\nBest,\nAnthony"
    out = apply_signature(body, SIG)
    assert out == "Hi Jane,\n\nLove what Acme does.\n\n" + SIG
    assert "Best,\nAnthony" not in out  # old sign-off gone


def test_replaces_various_closings():
    for closing in ["Best regards,", "Regards,", "Thanks,", "Cheers,", "Sincerely,"]:
        body = f"Hi,\n\nBody here.\n\n{closing}\nAnthony"
        out = apply_signature(body, SIG)
        assert out.endswith(SIG)
        assert closing not in out


def test_appends_when_no_signoff_detected():
    body = "Hi Jane,\n\nQuick question about Acme. Let me know."
    out = apply_signature(body, SIG)
    assert out == body + "\n\n" + SIG


def test_idempotent_when_already_applied():
    body = "Hi,\n\nBody.\n\nBest,\nAnthony"
    once = apply_signature(body, SIG)
    twice = apply_signature(once, SIG)
    assert once == twice  # second apply is a no-op


def test_empty_signature_returns_body_unchanged():
    body = "Hi,\n\nBody.\n\nBest,\nAnthony"
    assert apply_signature(body, "") == body.rstrip()
    assert apply_signature(body, None) == body.rstrip()
    assert apply_signature(body, "   ") == body.rstrip()


def test_empty_body_returns_signature():
    assert apply_signature("", SIG) == SIG
    assert apply_signature(None, SIG) == SIG


def test_does_not_match_closing_word_mid_body():
    # "Thanks" deep in the body (not near the end) must NOT be treated as the
    # sign-off — only a closing line near the bottom is replaced.
    body = (
        "Hi Jane,\n\nThanks for the great talk last week.\n\n"
        "I wanted to follow up on a few things about Acme.\n\n"
        "Hope to connect soon and learn more about your roadmap.\n\n"
        "Best,\nAnthony"
    )
    out = apply_signature(body, SIG)
    assert "Thanks for the great talk" in out   # mid-body line preserved
    assert out.endswith(SIG)
