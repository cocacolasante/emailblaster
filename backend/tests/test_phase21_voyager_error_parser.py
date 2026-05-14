"""Unit tests for ``_parse_li_error`` — the helper that pulls a useful
error code/message out of a LinkedIn Voyager error body.

When LinkedIn rejects a write (connect, DM, follow, etc.) the response is
typically a JSON envelope like ``{"status":422,"code":"X","message":"Y"}``
or has nested ``errorDetails.inputErrors``.  The parser turns those into a
short, human-readable string the user sees in the Activity tab.
"""
from __future__ import annotations

import pytest

from app.services.linkedin.playwright_impl import _parse_li_error


def test_parse_simple_code_and_message():
    body = '{"status":422,"code":"CANT_INVITE_MEMBER","message":"Daily limit reached"}'
    assert _parse_li_error(body) == "CANT_INVITE_MEMBER: Daily limit reached"


def test_parse_service_error_code_fallback():
    body = '{"status":403,"serviceErrorCode":100,"message":"Not authorised"}'
    assert _parse_li_error(body) == "err100: Not authorised"


def test_parse_message_only():
    body = '{"status":400,"message":"Bad request"}'
    assert _parse_li_error(body) == "?: Bad request"


def test_parse_input_errors_descriptions():
    body = (
        '{"errorDetails":{"inputErrors":['
        '{"description":{"value":"message too long"}},'
        '{"description":{"value":"profile not found"}}]}}'
    )
    assert _parse_li_error(body) == "input_errors: message too long; profile not found"


def test_parse_not_json_returns_none():
    assert _parse_li_error("<html><body>unhealthy upstream</body></html>") is None


def test_parse_empty_dict_returns_none():
    assert _parse_li_error("{}") is None


def test_parse_list_body_returns_none():
    # LinkedIn occasionally returns a JSON array on success; not an error envelope.
    assert _parse_li_error('[{"id":"x"}]') is None
