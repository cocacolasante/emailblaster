"""Per-workspace third-party credentials.

Every provider key (Anthropic, Brevo, Hunter, Apollo, Unipile, Adzuna)
belongs to a workspace, not to the process.  Callers never read
``settings.*_API_KEY``; they ask this module:

    creds = credentials.require("brevo")   # raises MissingCredential
    creds = credentials.get("hunter")      # None when not configured

Resolution is bound to the tenant context: the request dependency
(``app.database.get_db``) and the worker scope
(``app.tenancy.worker.run_in_tenant``) load the workspace's
``CredentialBundle`` once and bind it with ``use_bundle`` for the
duration of the unit of work, so lookups are synchronous and cheap and
always match the tenant the queries are scoped to.

Interim (pre-P5) behaviour: with no bundle bound, credentials are read
from the process settings so the single-tenant deployment keeps working.
P5 removes that branch — there is no .env fallback for workspaces.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, Literal, Union

from app.config import settings

Provider = Literal["anthropic", "brevo", "hunter", "apollo", "unipile", "adzuna"]
PROVIDERS: tuple[Provider, ...] = (
    "anthropic", "brevo", "hunter", "apollo", "unipile", "adzuna",
)

PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic",
    "brevo": "Brevo",
    "hunter": "Hunter",
    "apollo": "Apollo",
    "unipile": "Unipile (LinkedIn)",
    "adzuna": "Adzuna",
}


@dataclass(frozen=True)
class AnthropicCreds:
    api_key: str


@dataclass(frozen=True)
class BrevoCreds:
    api_key: str
    sender_email: str = ""
    sender_name: str = ""
    webhook_secret: str = ""


@dataclass(frozen=True)
class HunterCreds:
    api_key: str


@dataclass(frozen=True)
class ApolloCreds:
    api_key: str


@dataclass(frozen=True)
class UnipileCreds:
    dsn: str
    api_key: str
    webhook_secret: str = ""


@dataclass(frozen=True)
class AdzunaCreds:
    app_id: str
    app_key: str


Creds = Union[AnthropicCreds, BrevoCreds, HunterCreds, ApolloCreds, UnipileCreds, AdzunaCreds]

CREDS_CLASSES: dict[str, type] = {
    "anthropic": AnthropicCreds,
    "brevo": BrevoCreds,
    "hunter": HunterCreds,
    "apollo": ApolloCreds,
    "unipile": UnipileCreds,
    "adzuna": AdzunaCreds,
}

# Fields that must be non-empty for the credential to count as configured.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "anthropic": ("api_key",),
    "brevo": ("api_key", "sender_email"),
    "hunter": ("api_key",),
    "apollo": ("api_key",),
    "unipile": ("dsn", "api_key"),
    "adzuna": ("app_id", "app_key"),
}
# Fields that are secrets (never echoed back to the UI).
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "anthropic": ("api_key",),
    "brevo": ("api_key", "webhook_secret"),
    "hunter": ("api_key",),
    "apollo": ("api_key",),
    "unipile": ("api_key", "webhook_secret"),
    "adzuna": ("app_key",),
}


class MissingCredential(RuntimeError):
    """The current workspace hasn't configured ``provider``."""

    def __init__(self, provider: str):
        self.provider = provider
        label = PROVIDER_LABELS.get(provider, provider)
        super().__init__(
            f"{label} isn't connected for this workspace. "
            "Add it in Settings → Integrations."
        )


def build_creds(provider: str, data: dict | None) -> Creds | None:
    """Construct a typed creds object from a raw dict, or None when any
    required field is empty."""
    if not data:
        return None
    cls = CREDS_CLASSES[provider]
    fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
    kwargs = {k: str(data.get(k) or "").strip() for k in fields}
    if any(not kwargs.get(f) for f in REQUIRED_FIELDS[provider]):
        return None
    return cls(**kwargs)


@dataclass
class CredentialBundle:
    tenant_id: uuid.UUID | None
    creds: dict[str, Creds] = field(default_factory=dict)

    def get(self, provider: str) -> Creds | None:
        return self.creds.get(provider)


_bundle: ContextVar[CredentialBundle | None] = ContextVar("credential_bundle", default=None)


@contextmanager
def use_bundle(bundle: CredentialBundle | None) -> Iterator[None]:
    token = _bundle.set(bundle)
    try:
        yield
    finally:
        _bundle.reset(token)


def current_bundle() -> CredentialBundle | None:
    return _bundle.get()


def _from_settings(provider: str) -> Creds | None:
    """Interim single-tenant source (removed in P5)."""
    raw: dict[str, str] = {
        "anthropic": {"api_key": settings.ANTHROPIC_API_KEY},
        "brevo": {
            "api_key": settings.BREVO_API_KEY,
            "sender_email": settings.BREVO_SENDER_EMAIL,
            "sender_name": settings.BREVO_SENDER_NAME,
            "webhook_secret": settings.BREVO_WEBHOOK_SECRET,
        },
        "hunter": {"api_key": settings.HUNTER_API_KEY},
        "apollo": {"api_key": settings.APOLLO_API_KEY},
        "unipile": {
            "dsn": settings.UNIPILE_DSN,
            "api_key": settings.UNIPILE_API_KEY,
            "webhook_secret": settings.UNIPILE_WEBHOOK_SECRET,
        },
        "adzuna": {"app_id": settings.ADZUNA_APP_ID, "app_key": settings.ADZUNA_APP_KEY},
    }[provider]
    return build_creds(provider, raw)


def get(provider: Provider) -> Creds | None:
    """The current workspace's credentials for ``provider``, or None."""
    bundle = _bundle.get()
    if bundle is None:
        return _from_settings(provider)
    return bundle.get(provider)


def require(provider: Provider) -> Creds:
    creds = get(provider)
    if creds is None:
        raise MissingCredential(provider)
    return creds


def is_configured(provider: Provider) -> bool:
    return get(provider) is not None


def default_sender() -> tuple[str, str]:
    """``(sender_name, sender_email)`` from the workspace's Brevo creds,
    or ``("", "")`` when Brevo isn't configured."""
    c = get("brevo")
    if c is None:
        return "", ""
    return c.sender_name, c.sender_email  # type: ignore[union-attr]


async def load_bundle(factory, tenant_id: uuid.UUID) -> CredentialBundle | None:
    """Load the workspace's credential bundle.

    Interim (pre-P5): returns None, so ``get`` falls back to process
    settings for the single bootstrap workspace.
    """
    return None
