"""Apply a campaign signature to a composed email body.

The AI composer ends each email with a sign-off (e.g. ``Best,\\nAnthony``)
that often lacks contact info / website / calendar link.  ``apply_signature``
swaps that sign-off for the campaign's signature block, falling back to a
plain append when no recognizable sign-off is found.  It is idempotent — a
body that already ends with the signature is returned unchanged — so the
bulk-apply endpoint and a re-compose can run safely more than once.
"""
from __future__ import annotations

# Common email closings the AI emits, lowercased, punctuation stripped.  A
# line that IS one of these (its own line, as Claude formats them) marks the
# start of the sign-off block we replace.
_CLOSINGS: frozenset[str] = frozenset({
    "best", "best regards", "warm regards", "warmest regards", "kind regards",
    "kindest regards", "regards", "thanks", "thank you", "many thanks",
    "thanks so much", "cheers", "sincerely", "warmly", "talk soon",
    "all the best", "with appreciation", "with gratitude", "respectfully",
    "looking forward", "speak soon", "yours", "yours truly", "best wishes",
})

# Don't scan further than this many lines up from the end — the sign-off is
# always near the bottom, and scanning the whole body risks matching a word
# like "Thanks" mid-message.
_MAX_SIGNOFF_LOOKBACK = 6


def _normalize_closing(line: str) -> str:
    return line.strip().rstrip(",.!:;").strip().lower()


def apply_signature(body: str | None, signature: str | None) -> str:
    """Return ``body`` with its sign-off replaced by ``signature``.

    - Empty signature → body unchanged.
    - Body already ending with the signature → unchanged (idempotent).
    - A recognizable closing line near the end → everything from it to the
      end is replaced with the signature.
    - Otherwise the signature is appended after a blank line.
    """
    body = (body or "").rstrip()
    sig = (signature or "").strip()
    if not sig:
        return body
    if not body:
        return sig
    if body.rstrip().endswith(sig):
        return body  # idempotent — already applied

    lines = body.split("\n")
    cut: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        if len(lines) - 1 - i > _MAX_SIGNOFF_LOOKBACK:
            break
        if _normalize_closing(lines[i]) in _CLOSINGS:
            cut = i
            break

    if cut is not None:
        kept = "\n".join(lines[:cut]).rstrip()
        return f"{kept}\n\n{sig}" if kept else sig
    # No sign-off detected — append.
    return f"{body}\n\n{sig}"
