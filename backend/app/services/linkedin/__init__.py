"""LinkedIn provider package.

Concrete impls live alongside the ABC. ``get_provider()`` returns the
configured implementation; M2 hard-codes the DIY ``LinkedinApiProvider``.
"""
from app.services.linkedin.base import (
    ActionResult,
    ChallengeRequired,
    InboundEvent,
    LinkedInProvider,
    ProfileRef,
)
from app.services.linkedin.linkedin_api_impl import LinkedinApiProvider

_provider: LinkedInProvider | None = None


def get_provider() -> LinkedInProvider:
    """Return the configured LinkedIn provider singleton.

    M2 always returns ``LinkedinApiProvider``. When/if a hosted provider
    (Unipile, etc.) is wired in, switch here based on ``settings``.
    """
    global _provider
    if _provider is None:
        _provider = LinkedinApiProvider()
    return _provider


__all__ = [
    "ActionResult",
    "ChallengeRequired",
    "InboundEvent",
    "LinkedInProvider",
    "LinkedinApiProvider",
    "ProfileRef",
    "get_provider",
]
