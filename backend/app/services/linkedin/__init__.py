"""LinkedIn provider package.

Concrete impls live alongside the ABC. ``get_provider()`` returns the
implementation selected by ``settings.LINKEDIN_PROVIDER``:

- ``"unipile"``    (default) — hosted browser API.  Real Chrome on
  residential IPs.  No bot-detection issues.  Requires UNIPILE_API_KEY +
  UNIPILE_DSN.
- ``"playwright"``          — all actions via local headless Chromium.
  Permanent bot-detection trouble; kept for dev fallback.
- ``"hybrid"``              — Playwright writes + HTTP reads.  Legacy.
- ``"http"``                — all actions via linkedin-api HTTP.  Legacy.
"""
from app.services.linkedin.base import (
    ActionResult,
    ChallengeRequired,
    InboundEvent,
    LinkedInProvider,
    ProfileRef,
)
from app.services.linkedin.hybrid_impl import HybridLinkedInProvider
from app.services.linkedin.linkedin_api_impl import LinkedinApiProvider
from app.services.linkedin.playwright_impl import PlaywrightLinkedInProvider
from app.services.linkedin.unipile_impl import UnipileLinkedInProvider

_provider: LinkedInProvider | None = None


def get_provider() -> LinkedInProvider:
    """Return the configured LinkedIn provider singleton."""
    global _provider
    if _provider is None:
        from app.config import settings
        name = (settings.LINKEDIN_PROVIDER or "unipile").lower()
        if name == "unipile":
            _provider = UnipileLinkedInProvider()
        elif name == "http":
            _provider = LinkedinApiProvider()
        elif name == "playwright":
            _provider = PlaywrightLinkedInProvider()
        else:
            _provider = HybridLinkedInProvider()
    return _provider


def _reset_provider_for_tests() -> None:
    """Reset the singleton.  Test-only; never call from app code."""
    global _provider
    _provider = None


__all__ = [
    "ActionResult",
    "ChallengeRequired",
    "HybridLinkedInProvider",
    "InboundEvent",
    "LinkedInProvider",
    "LinkedinApiProvider",
    "PlaywrightLinkedInProvider",
    "ProfileRef",
    "UnipileLinkedInProvider",
    "get_provider",
]
