"""SQLAlchemy models. Import side-effect registers all tables on Base.metadata."""
from app.models.campaign import Campaign, CampaignStatus, ResearchMode
from app.models.connected_account import ConnectedAccount, ConnectedAccountTestStatus
from app.models.email_event import EmailEvent, EmailEventType
from app.models.lead import ComposeStatus, Lead, LinkedInConnectionStatus, ResearchStatus, SendStatus
from app.models.linkedin_account import LinkedInAccount, LinkedInAccountStatus
from app.models.linkedin_profile_cache import LinkedInProfileCache
from app.models.research_cache import ResearchCache
from app.models.social_listening import (
    SocialListeningOpportunity,
    SocialListeningPost,
    SocialListeningSearch,
    SocialOpportunityAction,
    SocialOpportunityCategory,
    SocialOpportunityStatus,
    SocialPostProvider,
    SocialSearchFrequency,
    SocialSearchSource,
    SocialSearchStatus,
)
from app.models.sequence import (
    LeadSequenceState,
    LeadSequenceStatus,
    LeadStepExecution,
    LeadStepResult,
    Sequence,
    SequenceEdge,
    SequenceNode,
    SequenceNodeKind,
)
from app.models.style_correction import StyleCorrection
from app.models.suppression import Suppression, SuppressionReason
from app.models.webhook_event import WebhookEvent

__all__ = [
    "Campaign",
    "CampaignStatus",
    "ResearchMode",
    "ConnectedAccount",
    "ConnectedAccountTestStatus",
    "EmailEvent",
    "EmailEventType",
    "Lead",
    "LinkedInAccount",
    "LinkedInAccountStatus",
    "LinkedInProfileCache",
    "LinkedInConnectionStatus",
    "ResearchStatus",
    "ComposeStatus",
    "SendStatus",
    "Sequence",
    "SequenceNode",
    "SequenceNodeKind",
    "SequenceEdge",
    "LeadSequenceState",
    "LeadSequenceStatus",
    "LeadStepExecution",
    "LeadStepResult",
    "ResearchCache",
    "SocialListeningSearch",
    "SocialListeningPost",
    "SocialListeningOpportunity",
    "SocialSearchSource",
    "SocialSearchFrequency",
    "SocialSearchStatus",
    "SocialPostProvider",
    "SocialOpportunityCategory",
    "SocialOpportunityAction",
    "SocialOpportunityStatus",
    "StyleCorrection",
    "Suppression",
    "SuppressionReason",
    "WebhookEvent",
]
