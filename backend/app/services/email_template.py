"""Render composed text into a minimal responsive HTML email + plain text."""
from __future__ import annotations

import html
import re

# Matches http(s) URLs without surrounding whitespace/brackets.
_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")


def render_html(body_text: str) -> str:
    """Convert a plain-text email body into responsive HTML.

    Paragraphs are split on blank lines. URLs become clickable links.
    Newlines inside a paragraph render as <br>. Everything else is escaped.
    """
    paragraphs = [p for p in body_text.split("\n\n") if p.strip()]
    rendered: list[str] = []
    for p in paragraphs:
        urls = _URL_RE.findall(p)
        # Replace URLs with placeholders BEFORE escaping so we can re-insert
        # them as <a> tags without having to undo HTML escaping of '://'.
        masked = p
        for i, url in enumerate(urls):
            masked = masked.replace(url, f"\x00URL{i}\x00", 1)
        escaped = html.escape(masked)
        for i, url in enumerate(urls):
            safe = html.escape(url, quote=True)
            escaped = escaped.replace(
                f"\x00URL{i}\x00", f'<a href="{safe}">{safe}</a>', 1
            )
        escaped = escaped.replace("\n", "<br>")
        rendered.append(f"<p>{escaped}</p>")
    body = "\n".join(rendered)
    return (
        "<!DOCTYPE html>\n"
        '<html>\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "</head>\n"
        '<body style="font-family: -apple-system, BlinkMacSystemFont, '
        "'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; max-width: 600px; "
        'margin: 0 auto; padding: 20px; color: #333; line-height: 1.5;">\n'
        f"{body}\n"
        "</body>\n</html>"
    )


def render_text(body_text: str) -> str:
    """Plain-text version is the body unchanged."""
    return body_text
