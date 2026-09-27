"""SQLAlchemy models. Import side-effect registers all tables on Base.metadata."""
import app.tenancy.scoping  # noqa: F401 — registers the tenant-scoping Session listeners
from app.models.agent import (
    AGENT_SETTINGS_SINGLETON_ID,
    AgentAction,
    AgentActionStatus,
    AgentActionType,
    AgentSettings,
    Notification,
    NotificationKind,
)
from app.models.campaign import Campaign, CampaignStatus, ResearchMode
from app.models.identity import (
    AuthToken,
    AuthTokenPurpose,
    Invitation,
    Membership,
    MembershipRole,
    Tenant,
    TenantStatus,
    User,
    UserSession,
)
from app.models.copy_feedback import CampaignCopyInsights, ReplyOutcome
from app.models.connected_account import ConnectedAccount, ConnectedAccountTestStatus
from app.models.crm import (
    CLOSED_STAGES,
    STAGE_DEFAULT_PROBABILITY,
    Account,
    Contact,
    CrmActivity,
    CrmActivityDirection,
    CrmActivityType,
    CrmDocument,
    CrmLeadStatus,
    Opportunity,
    OpportunityProduct,
    OpportunityStage,
    OpportunityStageChange,
    Pipeline,
    PipelineStage,
)
from app.models.api_key import ApiKey
from app.models.report import ReportDefinition
from app.models.tenant_keys import TenantProviderKey
from app.models.email_event import EmailEvent, EmailEventType
from app.models.icp import (
    IcpProfile,
    IcpProfileSource,
    IcpProfileStatus,
    LookalikeCandidate,
    LookalikeCandidateStatus,
)
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
from app.models.signals import (
    ProspectSignal,
    ProspectSignalStatus,
    SignalWatch,
    SignalWatchStatus,
    SignalWatchType,
)
from app.models.funding import (
    FundingEnrichmentQueue,
    FundingEnrichmentStatus,
    FundingSourceState,
)
from app.models.intent import (
    IcpIntentProfile,
    IntentSignalSource,
    IntentSignalStatus,
    IntentSignalType,
    Org,
    OrgIntentScore,
    OrgSizeBand,
    Signal,
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
from app.models.suppression import Suppression, SuppressionReason, canonical_email
from app.models.webhook_event import WebhookEvent

__all__ = [
    "ApiKey",
    "TenantProviderKey",
    "AuthToken",
    "AuthTokenPurpose",
    "Invitation",
    "Membership",
    "MembershipRole",
    "Tenant",
    "TenantStatus",
    "User",
    "UserSession",
    "AGENT_SETTINGS_SINGLETON_ID",
    "AgentAction",
    "AgentActionStatus",
    "AgentActionType",
    "AgentSettings",
    "Notification",
    "NotificationKind",
    "Campaign",
    "CampaignCopyInsights",
    "ReplyOutcome",
    "CampaignStatus",
    "ResearchMode",
    "ConnectedAccount",
    "ConnectedAccountTestStatus",
    "CLOSED_STAGES",
    "STAGE_DEFAULT_PROBABILITY",
    "Account",
    "Contact",
    "CrmActivity",
    "CrmActivityDirection",
    "CrmActivityType",
    "CrmDocument",
    "CrmLeadStatus",
    "Opportunity",
    "OpportunityProduct",
    "OpportunityStage",
    "OpportunityStageChange",
    "Pipeline",
    "PipelineStage",
    "ReportDefinition",
    "IcpProfile",
    "IcpProfileSource",
    "IcpProfileStatus",
    "LookalikeCandidate",
    "LookalikeCandidateStatus",
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
    "FundingEnrichmentQueue",
    "FundingEnrichmentStatus",
    "FundingSourceState",
    "IcpIntentProfile",
    "IntentSignalSource",
    "IntentSignalStatus",
    "IntentSignalType",
    "Org",
    "OrgIntentScore",
    "OrgSizeBand",
    "Signal",
    "ProspectSignal",
    "ProspectSignalStatus",
    "SignalWatch",
    "SignalWatchStatus",
    "SignalWatchType",
    "StyleCorrection",
    "Suppression",
    "SuppressionReason",
    "canonical_email",
    "WebhookEvent",
]

# Composite owner FKs (tenant_id, owner_id) → memberships, for create_all.
from app.database import Base as _Base  # noqa: E402
from app.tenancy.mixin import install_owner_fk_ddl as _install_owner_fk_ddl  # noqa: E402

_install_owner_fk_ddl(_Base.metadata)

# Tenant-blind record → tenant resolver (SECURITY DEFINER), for create_all.
from app.tenancy.worker import install_tenant_of_ddl as _install_tenant_of_ddl  # noqa: E402

_install_tenant_of_ddl(_Base.metadata)

# API-key verifier (SECURITY DEFINER), for create_all.
from app.tenancy.api_key_sql import install_api_key_ddl as _install_api_key_ddl  # noqa: E402

_install_api_key_ddl(_Base.metadata)
