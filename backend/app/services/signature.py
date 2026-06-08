"""Apply a campaign signature to a composed email body.

The AI composer ends each email with a sign-off (e.g. ``Best,\\nAnthony``)
that often lacks contact info / website / calendar link.  ``apply_signature``
swaps that sign-off for the campaign's signature block, falling back to a
plain append when no recognizable sign-off is found.  It is idempotent — a
body that already ends with the signature is returned unchanged — so the
bulk-apply endpoint and a re-compose can run safely more than once.

The HTML-signature path (used by the research-client one-off send so the
user can embed ``<img>`` and ``<a>`` tags in their per-inbox signature)
operates differently: rather than concatenating signature text into the
body, the send endpoint:

  1. Strips the AI sign-off from the plain-text body via
     ``strip_signoff``.
  2. Renders the stripped body through the standard
     ``render_html`` / ``render_text``.
  3. Appends the signature via ``signature_to_html`` (preserves any
     HTML tags the user typed, converts naked newlines to ``<br>``) for
     the HTML body, and via ``signature_to_text`` (collapses tags into
     plain-text equivalents) for the text body.

This keeps the existing campaign-pipeline ``apply_signature`` behaviour
unchanged while letting the research-client signature carry real HTML.
"""
from __future__ import annotations

import html as _html
import re

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


def strip_signoff(body: str | None) -> str:
    """Return ``body`` with the AI's sign-off block stripped if one is
    detected near the end.  Mirrors ``apply_signature``'s closing-line
    detection but does not append anything in its place — the caller
    (the research-client send endpoint) emits the signature separately
    so that an HTML-tagged signature can render as real HTML instead of
    being escaped through ``render_html``.

    Empty/None body → empty string."""
    body = (body or "").rstrip()
    if not body:
        return ""
    lines = body.split("\n")
    cut: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        if len(lines) - 1 - i > _MAX_SIGNOFF_LOOKBACK:
            break
        if _normalize_closing(lines[i]) in _CLOSINGS:
            cut = i
            break
    if cut is None:
        return body
    return "\n".join(lines[:cut]).rstrip()


# ---- HTML-signature renderers -----------------------------------------

# Matches an opening tag of the form `<tagname ...>` so we can tell which
# parts of the signature are markup the user typed (preserved as-is) and
# which are naked text (escaped + newlines → <br>).  Used by
# ``signature_to_html``.
_HTML_TAG_RE = re.compile(r"<[a-zA-Z!/][^>]*>")

# Tags we know how to translate to plain text.  Anything not in this map
# is stripped (its inner text content is kept).  Used by
# ``signature_to_text``.
_VOID_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_PARA_OPEN_RE = re.compile(r"<p\b[^>]*>", re.IGNORECASE)
_PARA_CLOSE_RE = re.compile(r"</p\s*>", re.IGNORECASE)
_ANCHOR_RE = re.compile(
    r'<a\s+[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_IMG_RE = re.compile(
    r'<img\s+[^>]*src\s*=\s*["\']([^"\']+)["\'][^>]*/?>',
    re.IGNORECASE,
)
_GENERIC_TAG_RE = re.compile(r"<[^>]+>")
_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'",
    "&nbsp;": " ",
}


def signature_to_html(signature: str | None) -> str:
    """Render the signature for the HTML email body.

    Preserves any HTML tags the user typed (``<a>`` for links, ``<img>``
    for inline images, basic formatting) so they reach the recipient's
    inbox as real HTML.  Naked newlines OUTSIDE of tags become ``<br>``
    so a multi-line plain-text signature still renders with line breaks.
    Text outside of tags is NOT escaped — the user controls this field
    in Settings and is trusted to type valid HTML (this is the same
    trust model as the per-Campaign signature).

    Empty/None signature → empty string."""
    sig = (signature or "").strip()
    if not sig:
        return ""
    # Walk the string: tags pass through verbatim; text between tags has
    # naked newlines converted to <br>.  No HTML-escape — see docstring.
    out: list[str] = []
    last_end = 0
    for match in _HTML_TAG_RE.finditer(sig):
        chunk = sig[last_end:match.start()]
        if chunk:
            out.append(chunk.replace("\n", "<br>\n"))
        out.append(match.group(0))
        last_end = match.end()
    tail = sig[last_end:]
    if tail:
        out.append(tail.replace("\n", "<br>\n"))
    return "".join(out)


def signature_to_text(signature: str | None) -> str:
    """Render the signature for the plain-text email body.

    - ``<a href="X">text</a>`` → ``text (X)``
    - ``<img src="X" alt="Y">`` → ``[image: Y — X]`` (or ``[image: X]``
      when no alt attribute).
    - ``<br>`` → newline.
    - ``<p>`` and ``</p>`` → newline (paragraph break).
    - Any other tag is stripped, its inner text content preserved.
    - Common HTML entities (``&amp;`` etc.) are decoded.

    The result is what a plain-text email client (or someone who's
    disabled HTML rendering) will actually see for the signature block.
    """
    sig = (signature or "").strip()
    if not sig:
        return ""
    out = sig
    # Anchors → "text (URL)" before stripping other tags so we don't
    # lose the href.
    out = _ANCHOR_RE.sub(
        lambda m: f"{_strip_inner_tags(m.group(2)).strip()} ({m.group(1)})",
        out,
    )
    # Images → "[image: alt — URL]" or "[image: URL]".
    def _img_repl(m: re.Match[str]) -> str:
        whole = m.group(0)
        src = m.group(1)
        alt_m = re.search(r'alt\s*=\s*["\']([^"\']*)["\']', whole, re.IGNORECASE)
        alt = (alt_m.group(1).strip() if alt_m else "") or ""
        return f"[image: {alt} — {src}]" if alt else f"[image: {src}]"
    out = _IMG_RE.sub(_img_repl, out)
    # Block / line-break tags → newlines.
    out = _VOID_TAG_RE.sub("\n", out)
    out = _PARA_OPEN_RE.sub("", out)
    out = _PARA_CLOSE_RE.sub("\n", out)
    # Strip remaining tags, keep their inner text.
    out = _GENERIC_TAG_RE.sub("", out)
    # Decode common entities.
    for ent, repl in _ENTITIES.items():
        out = out.replace(ent, repl)
    # Collapse runs of 3+ newlines down to 2 — a paragraph break is fine.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _strip_inner_tags(text: str) -> str:
    """Helper for the anchor substitution above — link text might
    contain nested ``<strong>``/``<em>`` etc.; reduce to plain words."""
    return _GENERIC_TAG_RE.sub("", text)
