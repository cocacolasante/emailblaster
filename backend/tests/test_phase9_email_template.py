"""Phase 9: HTML + plain-text email template rendering."""
from app.services.email_template import render_html, render_text


def test_text_render_is_passthrough():
    body = "Hi Jane,\n\nQuick question about your team."
    assert render_text(body) == body


def test_html_render_wraps_paragraphs():
    body = "Para one.\n\nPara two."
    html = render_html(body)
    assert "<p>Para one.</p>" in html
    assert "<p>Para two.</p>" in html


def test_html_render_links_urls():
    body = "Click here: https://example.com/foo\n\nThanks."
    html = render_html(body)
    assert '<a href="https://example.com/foo">https://example.com/foo</a>' in html


def test_html_render_escapes_html_in_content():
    body = "<script>alert('xss')</script>"
    html = render_html(body)
    assert "<script>alert" not in html  # raw <script> escaped
    assert "&lt;script&gt;" in html


def test_html_render_handles_unsubscribe_footer():
    body = (
        "Hi Jane,\n\nQuick value pitch.\n\n---\n"
        "To unsubscribe: https://test.example.com/unsubscribe/abc-123"
    )
    html = render_html(body)
    assert '<a href="https://test.example.com/unsubscribe/abc-123">' in html
    # Body paragraphs render correctly even with the footer present.
    assert "<p>Hi Jane,</p>" in html
    assert "<p>Quick value pitch.</p>" in html


def test_html_render_converts_single_newlines_to_br():
    body = "Line one\nLine two\n\nNew paragraph"
    html = render_html(body)
    assert "Line one<br>Line two" in html


def test_html_render_skips_empty_paragraphs():
    body = "First\n\n\n\nSecond"
    html = render_html(body)
    assert html.count("<p>") == 2


def test_html_render_includes_responsive_viewport_and_doctype():
    html = render_html("Hi.")
    assert html.startswith("<!DOCTYPE html>")
    assert 'name="viewport"' in html
    assert "max-width: 600px" in html
