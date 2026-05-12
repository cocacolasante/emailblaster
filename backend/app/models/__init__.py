"""SQLAlchemy models. Import side-effect registers all tables on Base.metadata."""
from app.models.campaign import Campaign, CampaignStatus, ResearchMode
from app.models.connected_account import ConnectedAccount, ConnectedAccountTestStatus
from app.models.email_event import EmailEvent, EmailEventType
from app.models.lead import ComposeStatus, Lead, ResearchStatus, SendStatus
from app.models.style_correction import StyleCorrection
from app.models.suppression import Suppression, SuppressionReason

__all__ = [
    "Campaign",
    "CampaignStatus",
    "ResearchMode",
    "ConnectedAccount",
    "ConnectedAccountTestStatus",
    "EmailEvent",
    "EmailEventType",
    "Lead",
    "ResearchStatus",
    "ComposeStatus",
    "SendStatus",
    "StyleCorrection",
    "Suppression",
    "SuppressionReason",
]
