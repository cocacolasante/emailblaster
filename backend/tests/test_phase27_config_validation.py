"""Tests for ``validate_required_settings``.

We don't import app.main here because that triggers the validator at
import time (which would fail when this test file is imported with the
test .env).  Instead we drive ``validate_required_settings`` directly
and monkeypatch ``settings`` attributes.
"""
from __future__ import annotations

import pytest

from app.config import (
    ConfigurationError,
    settings,
    validate_required_settings,
)


def _set(monkeypatch, **kwargs):
    for k, v in kwargs.items():
        monkeypatch.setattr(settings, k, v)


def test_validate_passes_with_all_required_set(monkeypatch):
    _set(
        monkeypatch,
        ANTHROPIC_API_KEY="sk-ant-...",
        BREVO_API_KEY="brevo-...",
        BREVO_SENDER_EMAIL="me@example.com",
        ENCRYPTION_KEY="fernet-...",
        SECRET_KEY="real-prod-secret",
    )
    assert validate_required_settings(raise_on_missing=False) == []


def test_validate_flags_empty_required(monkeypatch):
    _set(
        monkeypatch,
        ANTHROPIC_API_KEY="",
        BREVO_API_KEY="brevo-x",
        BREVO_SENDER_EMAIL="me@example.com",
        ENCRYPTION_KEY="fernet-x",
        SECRET_KEY="real",
    )
    errs = validate_required_settings(raise_on_missing=False)
    assert any("ANTHROPIC_API_KEY" in e for e in errs)
    assert len(errs) == 1


def test_validate_flags_default_secret_key(monkeypatch):
    """Sending unsubscribe links signed with ``dev-secret-change-me`` is
    a real security risk — anyone who guesses the default can forge
    tokens for any lead.  Treat the default as a hard fail."""
    _set(
        monkeypatch,
        ANTHROPIC_API_KEY="x",
        BREVO_API_KEY="x",
        BREVO_SENDER_EMAIL="me@example.com",
        ENCRYPTION_KEY="x",
        SECRET_KEY="dev-secret-change-me",
    )
    errs = validate_required_settings(raise_on_missing=False)
    assert any("SECRET_KEY" in e and "insecure default" in e for e in errs)


def test_validate_flags_default_sender_email(monkeypatch):
    _set(
        monkeypatch,
        ANTHROPIC_API_KEY="x",
        BREVO_API_KEY="x",
        BREVO_SENDER_EMAIL="noreply@example.com",
        ENCRYPTION_KEY="x",
        SECRET_KEY="real",
    )
    errs = validate_required_settings(raise_on_missing=False)
    assert any("BREVO_SENDER_EMAIL" in e and "insecure default" in e for e in errs)


def test_validate_raises_by_default(monkeypatch):
    _set(monkeypatch, ANTHROPIC_API_KEY="")
    with pytest.raises(ConfigurationError) as exc_info:
        validate_required_settings()
    assert "ANTHROPIC_API_KEY" in str(exc_info.value)


def test_validate_lists_all_problems_at_once(monkeypatch):
    """Better to surface every issue in one boot attempt than play
    whac-a-mole one missing var at a time."""
    _set(
        monkeypatch,
        ANTHROPIC_API_KEY="",
        BREVO_API_KEY="",
        BREVO_SENDER_EMAIL="me@example.com",
        ENCRYPTION_KEY="",
        SECRET_KEY="dev-secret-change-me",
    )
    errs = validate_required_settings(raise_on_missing=False)
    assert len(errs) == 4  # 3 empty + 1 default
