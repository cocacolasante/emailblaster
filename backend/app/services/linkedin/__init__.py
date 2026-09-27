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

def get_provider() -> LinkedInProvider:
    """A LinkedIn provider bound to the CURRENT workspace's Unipile creds.

    Deliberately not a singleton: each workspace has its own Unipile
    DSN + key, so a cached instance would send one tenant's actions
    through another's workspace.  Construction is cheap (no I/O).
    """
    return UnipileLinkedInProvider()


def _reset_provider_for_tests() -> None:
    """No-op (kept for tests written against the old singleton)."""


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
