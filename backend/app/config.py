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

    # LinkedIn provider selection
    # "unipile"    — hosted browser API (real Chrome + residential IPs).
    #                Recommended for any production-ish use; no challenge
    #                bot-detection issues because LinkedIn sees a real
    #                desktop browser from a residential IP.  Requires
    #                UNIPILE_API_KEY + UNIPILE_DSN.
    # "playwright" — all actions via local headless Chromium.  Bot-detection
    #                risk; profile pages get challenge-flagged within a few
    #                runs even on established accounts.  Kept for offline
    #                fallback / dev without a Unipile key.
    # "hybrid"     — Playwright writes + linkedin-api HTTP reads.  Legacy.
    # "http"       — all actions via linkedin-api HTTP.  Legacy.
    LINKEDIN_PROVIDER: str = "unipile"

    # Unipile config — required when LINKEDIN_PROVIDER=unipile.
    # DSN is the tenant host returned from the dashboard, e.g.
    # "api12.unipile.com:13443".  API key from dashboard → access-tokens.
    # Webhook secret is shared between Unipile's webhook config + our handler;
    # used to verify incoming events.
    UNIPILE_DSN: str = ""
    UNIPILE_API_KEY: str = ""
    UNIPILE_WEBHOOK_SECRET: str = ""

    # LinkedIn (DIY: linkedin-api library + optional residential proxy)
    LINKEDIN_PROXY_URL: str = ""  # e.g. http://user:pass@host:port; empty = direct
    LINKEDIN_DAILY_ACTION_CAP: int = 20  # per-account TOTAL actions per day
    LINKEDIN_MIN_ACTION_DELAY_SECONDS: int = 90  # min gap between actions per account
    # Inbox poll frequency. Every poll fires a fresh playwright Chromium
    # launch against LinkedIn, and frequent launches drift the browser
    # fingerprint enough that LinkedIn flags the `li_at` as bot-suspicious.
    # 30 min strikes a balance between reply latency + cookie safety.
    LINKEDIN_POLL_INTERVAL_MINUTES: int = 30
    # Per-LinkedIn-account on-disk Chrome profile. Persistent context keeps
    # cookies + localStorage + Chrome's fingerprint stable across launches,
    # which keeps LinkedIn from invalidating the session every time we open
    # a fresh browser. Each account gets a subdir named by its UUID.
    LINKEDIN_PROFILES_DIR: str = "/app/_linkedin_profiles"
    # Per-kind subcaps for write actions (M3). These are stricter than the
    # overall daily cap because connects/DMs are what get accounts flagged.
    # Per-kind subcaps. LinkedIn's real enforcement: ~100 connects/week for
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
