"""LinkedIn provider package.

All actions go through Unipile's hosted browser API (real Chrome on
residential IPs).  The DIY Playwright / hybrid / linkedin-api HTTP
providers were removed once Unipile proved it could keep sessions alive
without our own fingerprint/proxy bookkeeping; the ABC is preserved in
case a second hosted provider ever needs to slot in alongside Unipile.
"""
from app.services.linkedin.base import (
    AccountRestricted,
    ActionResult,
    ChallengeRequired,
    InboundEvent,
    LinkedInProvider,
    ProfileRef,
)
from app.services.linkedin.unipile_impl import UnipileLinkedInProvider

_provider: LinkedInProvider | None = None


def get_provider() -> LinkedInProvider:
    """Return the configured LinkedIn provider singleton."""
    global _provider
    if _provider is None:
        _provider = UnipileLinkedInProvider()
    return _provider


def _reset_provider_for_tests() -> None:
    """Reset the singleton.  Test-only; never call from app code."""
    global _provider
    _provider = None


__all__ = [
    "AccountRestricted",
    "ActionResult",
    "ChallengeRequired",
    "InboundEvent",
    "LinkedInProvider",
    "ProfileRef",
    "UnipileLinkedInProvider",
    "get_provider",
]
