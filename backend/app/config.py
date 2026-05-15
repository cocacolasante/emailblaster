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
