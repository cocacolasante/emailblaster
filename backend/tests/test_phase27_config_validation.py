"""Tests for ``validate_required_settings``.

We don't import app.main here because that triggers the validator at
import time.  Instead we drive ``validate_required_settings`` directly
and monkeypatch ``settings`` attributes.

Provider API keys are per workspace now (stored encrypted in the DB), so
the only hard requirements are the platform secrets.
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


def test_validate_passes_with_platform_secrets_set(monkeypatch):
    _set(monkeypatch, ENCRYPTION_KEY="fernet-...", SECRET_KEY="real-prod-secret")
    assert validate_required_settings(raise_on_missing=False) == []


def test_provider_keys_are_not_required_at_boot():
    """A fresh multi-tenant deployment has no provider keys at all."""
    for key in ("ANTHROPIC_API_KEY", "BREVO_API_KEY", "HUNTER_API_KEY", "UNIPILE_API_KEY"):
        assert not hasattr(settings, key), f"{key} must not be a process setting"


def test_validate_flags_empty_required(monkeypatch):
    _set(monkeypatch, ENCRYPTION_KEY="", SECRET_KEY="real")
    errs = validate_required_settings(raise_on_missing=False)
    assert len(errs) == 1 and "ENCRYPTION_KEY" in errs[0]


def test_validate_flags_default_secret_key(monkeypatch):
    """Sending unsubscribe links signed with ``dev-secret-change-me`` is
    a real security risk — anyone who guesses the default can forge
    tokens for any lead.  Treat the default as a hard fail."""
    _set(monkeypatch, ENCRYPTION_KEY="x", SECRET_KEY="dev-secret-change-me")
    errs = validate_required_settings(raise_on_missing=False)
    assert any("SECRET_KEY" in e and "insecure default" in e for e in errs)


def test_validate_raises_by_default(monkeypatch):
    _set(monkeypatch, ENCRYPTION_KEY="")
    with pytest.raises(ConfigurationError) as exc_info:
        validate_required_settings()
    assert "ENCRYPTION_KEY" in str(exc_info.value)


def test_validate_lists_all_problems_at_once(monkeypatch):
    _set(monkeypatch, ENCRYPTION_KEY="", SECRET_KEY="")
    errs = validate_required_settings(raise_on_missing=False)
    assert len(errs) == 2
