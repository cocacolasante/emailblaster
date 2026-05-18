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
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

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
    LINKEDIN_POLL_INTERVAL_MINUTES: int = 30    # inbox poller cadence
    # Per-kind subcaps.  LinkedIn's real enforcement: ~100 connects/week for
    # established accounts (~14/day).  20/day is conservative and safe.
    # DMs require 1st-degree — 30/day is the practical ceiling before risk.
    LINKEDIN_DAILY_CONNECT_CAP: int = 20        # per-account connect requests / day
    LINKEDIN_DAILY_DM_CAP: int = 30             # per-account DMs / day
    LINKEDIN_MONTHLY_PAGE_INVITE_CAP: int = 250 # per-PAGE invites / month

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
