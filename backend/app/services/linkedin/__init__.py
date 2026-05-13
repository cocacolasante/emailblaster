"""LinkedIn provider package.

Concrete impls live alongside the ABC. ``get_provider()`` returns the
implementation selected by ``settings.LINKEDIN_PROVIDER``:

- ``"hybrid"``    (default) — reads via linkedin-api HTTP, writes via
  Playwright browser.  Best speed + fingerprint combination.
- ``"playwright"``          — all actions via Playwright.
- ``"http"``                — all actions via linkedin-api HTTP (legacy).
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

_provider: LinkedInProvider | None = None


def get_provider() -> LinkedInProvider:
    """Return the configured LinkedIn provider singleton."""
    global _provider
    if _provider is None:
        from app.config import settings
        name = (settings.LINKEDIN_PROVIDER or "hybrid").lower()
        if name == "http":
            _provider = LinkedinApiProvider()
        elif name == "playwright":
            _provider = PlaywrightLinkedInProvider()
        else:
            _provider = HybridLinkedInProvider()
    return _provider


__all__ = [
    "ActionResult",
    "ChallengeRequired",
    "HybridLinkedInProvider",
    "InboundEvent",
    "LinkedInProvider",
    "LinkedinApiProvider",
    "PlaywrightLinkedInProvider",
    "ProfileRef",
    "get_provider",
]
