from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://emailblaster:emailblaster@localhost:5432/emailblaster"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    # Compose model — writes the actual email copy, so quality matters.
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    # Research model — just extracts structured facts from web-search results,
    # which a cheaper model handles fine.  Research ingests large web-search
    # result pages as input tokens (the dominant AI cost), so running it on
    # Haiku instead of Sonnet is ~3.75x cheaper on that token spend.
    ANTHROPIC_RESEARCH_MODEL: str = "claude-haiku-4-5-20251001"
    # Web searches allowed per lead's research call.  Each search costs a tool
    # fee AND ingests result pages as input tokens, so this is a direct cost
    # lever.  Default 2: one search for the person, one for the company.
    # Bumping to 3 surfaces marginal extra signal at +50% cost; we found
    # that 2 covers the typical lead profile well enough.
    RESEARCH_WEB_SEARCH_MAX_USES: int = 2
    # Freshness window for the cross-campaign research cache (keyed by email
    # or by ``linkedin:<slug>`` for the one-off research-a-client tool).
    # A second campaign adding the same email — or a second click on the
    # same LinkedIn URL — reuses the cached research_data if it was
    # refreshed within this many days, skipping the API call.
    RESEARCH_CACHE_TTL_DAYS: int = 90

    # Research-a-client (one-off LinkedIn URL → outreach) — same extraction
    # task as the bulk pipeline, so we run it on Haiku too.  Was previously
    # ``settings.ANTHROPIC_MODEL`` (Sonnet) which made every "Research a
    # client" click ~4x more expensive than necessary.  Deep mode still
    # gets a larger search budget than fast mode but uses the same Haiku
    # model — the depth is about WHERE we search, not which model writes.
    ANTHROPIC_RESEARCH_CLIENT_MODEL: str = "claude-haiku-4-5-20251001"
    # Per-call web_search budget for the one-off tool.  Fast mode 2 covers
    # identity + a recent news item; deep mode 5 gives Claude room to find
    # podcast / GitHub / Substack signal that fast skips.  Down from 3/8
    # (cost-driven; 8 was rarely surfacing anything the 5th call didn't).
    RESEARCH_CLIENT_FAST_WEB_SEARCH_MAX_USES: int = 2
    RESEARCH_CLIENT_DEEP_WEB_SEARCH_MAX_USES: int = 5

    # Social Listening Radar — discovery is "find LinkedIn posts matching
    # this query, extract structured fields from web-search results."  Same
    # extraction-shape task as research_person_web, so default to Haiku.
    # A single bad-luck run on Sonnet was costing $5+ because each of
    # max_queries_per_run (20) Anthropic calls was ingesting ~90K tokens
    # of web-search-result pages on Sonnet input pricing.  Haiku is ~3.75x
    # cheaper on the same input + output, bringing a full run to ~$1.50.
    ANTHROPIC_SOCIAL_DISCOVERY_MODEL: str = "claude-haiku-4-5-20251001"
    # Web searches allowed per discovery call.  Each search ingests result
    # pages as input tokens AND has a per-search tool fee, so this is a
    # direct cost lever.  2 is enough to find recent posts for a single
    # query; 3 was the original default and rarely surfaced anything new
    # the second pass didn't.
    # Per non-LinkedIn discovery call.  Set to 3 (up from 2) so the
    # discovery prompt can actually try 2-3 keyword variations of each
    # query before bailing — the failure mode was Anthropic running
    # exactly one literal search per query and returning 0 results.
    SOCIAL_DISCOVERY_WEB_SEARCH_MAX_USES: int = 3
    # LinkedIn-specific overrides.  LinkedIn aggressively blocks crawlers
    # (Google indexes very little of it), so the default budget gets
    # almost nothing back.  More uses + Sonnet (better at finding the
    # buried indexed-but-rare content) help recall a lot.  Per-run cost
    # impact when LinkedIn is enabled: ~+$0.50.
    LINKEDIN_DISCOVERY_MODEL: str = "claude-sonnet-4-6"
    LINKEDIN_DISCOVERY_WEB_SEARCH_MAX_USES: int = 5

    # Email sending
    BREVO_API_KEY: str = ""
    BREVO_SENDER_EMAIL: str = "noreply@example.com"
    BREVO_SENDER_NAME: str = "Email Blaster"
    # How often (minutes) to poll Brevo's transactional events API for
    # delivered/opened/clicked/bounced/spam/unsubscribed.  We poll
    # instead of taking the inbound webhook because the webhook needs a
    # public tunnel + paid plan on some Brevo tiers; polling is free
    # with the regular API key.  Trade-off: up to this many minutes of
    # lag from event-at-Brevo to event-row-in-our-DB.
    BREVO_EVENTS_POLL_INTERVAL_MINUTES: int = 10
    # Legacy: the (now-deleted) /webhooks/brevo route used this as a
    # shared-secret gate.  No longer referenced anywhere; keep the
    # setting for one release so anybody whose .env still has it doesn't
    # see a load-time error from pydantic-settings extra=forbid.  Safe
    # to delete in a follow-up after .env templates are scrubbed.
    BREVO_WEBHOOK_SECRET: str = ""

    # Enrichment (optional)
    APOLLO_API_KEY: str = ""
    HUNTER_API_KEY: str = ""

    # Encryption
    ENCRYPTION_KEY: str = ""

    # IMAP polling
    IMAP_POLL_INTERVAL_MINUTES: int = 20

    # Unipile (hosted LinkedIn browser API on residential IPs).
    # DSN is the tenant host returned from the dashboard, e.g.
    # "api12.unipile.com:13443".  API key from dashboard → access-tokens.
    # Webhook auth: Unipile doesn't HMAC-sign bodies; instead, when creating
    # the webhook you configure a custom HTTP header that Unipile echoes
    # back on every delivery.  We check the inbound request for the same
    # header + value.
    UNIPILE_DSN: str = ""
    UNIPILE_API_KEY: str = ""
    UNIPILE_WEBHOOK_SECRET: str = ""
    UNIPILE_WEBHOOK_AUTH_HEADER: str = "X-Unipile-Auth"

    # LinkedIn rate limits.  Unipile manages humanization on its side, but
    # we still enforce daily caps + a per-account min-delay as a burst floor
    # so a runaway sequence can't flood a single account with actions.
    LINKEDIN_DAILY_ACTION_CAP: int = 20         # per-account TOTAL actions / day
    LINKEDIN_MIN_ACTION_DELAY_SECONDS: int = 30 # min gap between actions
    # Sequencer dispatch staggering: the scheduler releases at most one
    # LinkedIn step per account per this many seconds, parking the rest until
    # their slot opens.  This spaces a campaign's leads out (one lead, wait,
    # next lead) instead of dispatching the whole batch at once.  Set to 0 to
    # disable staggering (fall back to the per-action min-delay floor only).
    LINKEDIN_STAGGER_SECONDS: int = 120
    LINKEDIN_POLL_INTERVAL_MINUTES: int = 30    # inbox poller cadence
    # Per-kind subcaps.  LinkedIn's real enforcement: ~100 connects/week for
    # established accounts (~14/day).  20/day is conservative and safe.
    # DMs require 1st-degree — 30/day is the practical ceiling before risk.
    LINKEDIN_DAILY_CONNECT_CAP: int = 20        # per-account connect requests / day
    LINKEDIN_DAILY_DM_CAP: int = 30             # per-account DMs / day
    LINKEDIN_MONTHLY_PAGE_INVITE_CAP: int = 250 # per-PAGE invites / month

    # --- Agent / notifications ---
    # Where agent alerts (positive-reply pings, task reminders, the daily
    # digest) are emailed.  Empty = notifications persist in the DB but
    # no email goes out.
    OWNER_NOTIFY_EMAIL: str = ""
    OWNER_NOTIFY_NAME: str = "Operator"
    # Master kill-switch for every autonomous agent behaviour (reply
    # classification, reminders, nudges, digest).  The finer-grained
    # per-behaviour toggles live in the runtime-editable AgentSettings
    # DB row; this env var is the hard off-switch that wins over all of
    # them — useful for incident response without touching the DB.
    AGENT_ENABLED: bool = True
    # Classification (reply sentiment/intent) is an extraction task —
    # Haiku handles it fine and runs per inbound reply, so cost matters.
    ANTHROPIC_AGENT_MODEL: str = "claude-haiku-4-5-20251001"
    # Reply DRAFTS are prose the user may actually send — Sonnet quality.
    # Only invoked when AgentSettings.auto_draft_replies is on.
    ANTHROPIC_AGENT_DRAFT_MODEL: str = "claude-sonnet-4-6"
    AGENT_REMINDER_SWEEP_INTERVAL_MINUTES: int = 30
    # Hour (UTC) the daily digest email goes out.
    AGENT_DIGEST_HOUR_UTC: int = 12
    # An open opportunity with no activity for this many days gets a
    # nudge task + notification.
    AGENT_STALE_OPP_DAYS: int = 7
    # Tasks due within this window trigger a "due soon" reminder.
    AGENT_TASK_DUE_SOON_HOURS: int = 24

    # App
    SECRET_KEY: str = "dev-secret-change-me"
    FRONTEND_URL: str = "http://localhost:5173"
    WEBHOOK_BASE_URL: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


class ConfigurationError(RuntimeError):
    """Raised by ``validate_required_settings`` when a load-bearing env
    var is empty or set to a known-bad default.  Surfaced at app boot so
    a half-configured ``.env`` fails immediately rather than mid-campaign.
    """


# Settings whose absence we deliberately tolerate but loudly warn about.
# These either have safe defaults (`FRONTEND_URL`, `WEBHOOK_BASE_URL`) or
# only matter for optional features (`UNIPILE_*`, `APOLLO_*`, `HUNTER_*`).
_HARD_REQUIRED = (
    # Without these the first compose / send / IMAP test crashes
    # unrecoverably with `RuntimeError: ... is not configured`.
    "ANTHROPIC_API_KEY",
    "BREVO_API_KEY",
    "BREVO_SENDER_EMAIL",
    "ENCRYPTION_KEY",
    "SECRET_KEY",
)
_DANGEROUS_DEFAULTS = {
    "SECRET_KEY": "dev-secret-change-me",
    "BREVO_SENDER_EMAIL": "noreply@example.com",
}


def validate_required_settings(*, raise_on_missing: bool = True) -> list[str]:
    """Inspect the loaded settings and return a list of human-readable
    error strings (``[]`` when everything's healthy).  Called from
    ``app.main`` at startup; raises on missing hard requirements so the
    container fails fast at ``docker compose up`` instead of running for
    minutes and only erroring at first send/compose attempt.

    Returns the error list either way (test-friendly), and raises
    ``ConfigurationError`` when ``raise_on_missing=True`` (the default).
    """
    errors: list[str] = []
    for key in _HARD_REQUIRED:
        val = getattr(settings, key, "")
        if not val or not str(val).strip():
            errors.append(
                f"{key} is empty.  Set it in .env before starting the backend."
            )
            continue
        bad = _DANGEROUS_DEFAULTS.get(key)
        if bad is not None and str(val) == bad:
            errors.append(
                f"{key} is still set to the insecure default ({bad!r}). "
                "Replace it in .env before sending any real campaign."
            )
    if errors and raise_on_missing:
        raise ConfigurationError(
            "Refusing to start with missing / insecure-default config:\n  - "
            + "\n  - ".join(errors)
        )
    return errors
