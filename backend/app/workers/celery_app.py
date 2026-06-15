from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "emailblaster",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.workers.ingest",
        "app.workers.research",
        "app.workers.compose",
        "app.workers.send",
        "app.workers.reply_poller",
        "app.workers.sequencer",
        "app.workers.linkedin_poller",
        "app.workers.brevo_events_poller",
        "app.workers.lead_sweeper",
        "app.workers.social_listening",
        "app.workers.agent_sweeper",
        "app.workers.digest",
        "app.workers.deliverability",
        "app.workers.copy_insights_refresher",
        "app.workers.signals",
        "app.workers.icp",
        "app.workers.funding_signals",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Celery on the Redis broker holds future-ETA tasks in worker
    # process MEMORY until the ETA arrives.  If the worker dies before
    # then, the task sits in ``unacked`` until visibility_timeout
    # expires and a live worker reclaims it.  The default is 1 hour;
    # 5 min is much friendlier when we ``apply_async(countdown=60)`` for
    # rate-limit retries.  Side note: the timeout MUST be >= the
    # longest realistic task runtime, or in-progress tasks can get
    # double-delivered.  300 s comfortably exceeds any task we run
    # (longest is Brevo POST + DB write, well under 30 s).
    broker_transport_options={"visibility_timeout": 300},
)

celery_app.conf.beat_schedule = {
    "poll-all-replies": {
        "task": "reply_poller.poll_all_replies",
        "schedule": float(settings.IMAP_POLL_INTERVAL_MINUTES * 60),
    },
    "advance-sequences": {
        "task": "sequencer.advance_sequences",
        "schedule": 60.0,
    },
    "linkedin-poll": {
        "task": "linkedin_poller.poll_all",
        "schedule": float(settings.LINKEDIN_POLL_INTERVAL_MINUTES * 60),
    },
    "brevo-events-poll": {
        "task": "brevo_events_poller.poll",
        "schedule": float(settings.BREVO_EVENTS_POLL_INTERVAL_MINUTES * 60),
    },
    "lead-sweeper": {
        "task": "lead_sweeper.sweep_stale",
        "schedule": 300.0,  # every 5 min
    },
    "social-radar-runner": {
        # Dispatcher that selects social listening searches whose
        # ``next_run_at <= now`` and enqueues ``run_social_search`` for
        # each.  Per-search frequency lives on the search row.
        "task": "social_listening.scheduled_runner",
        "schedule": 60.0,
    },
    "agent-sweep-reminders": {
        # Owner reminders for open tasks due soon / overdue.  One
        # reminder per task ever (CrmActivity.reminder_sent_at anchor).
        "task": "agent_sweeper.sweep_reminders",
        "schedule": float(settings.AGENT_REMINDER_SWEEP_INTERVAL_MINUTES * 60),
    },
    "agent-sweep-stale-opps": {
        # Nudge open opportunities idle > AGENT_STALE_OPP_DAYS.
        "task": "agent_sweeper.sweep_stale_opps",
        "schedule": 3600.0,  # hourly
    },
    "signals-runner": {
        # Dispatcher for due prospect-signal watches (job change /
        # funding / hiring) — sibling of social-radar-runner.
        "task": "signals.scheduled_runner",
        "schedule": 60.0,
    },
    "copy-insights-refresh": {
        # Per-campaign winning-angle summaries; LLM runs only for
        # campaigns with enough NEW reply outcomes since last refresh.
        "task": "copy_insights.refresh_all",
        "schedule": 3600.0,  # hourly
    },
    "deliverability-health-sweep": {
        # Bounce/spam circuit-breaker backstop over running campaigns.
        "task": "deliverability.sweep_health",
        "schedule": 900.0,  # every 15 min
    },
    "icp-refresh-profile": {
        # Regenerate the auto ICP from closed-won deals.
        "task": "icp.refresh_profile",
        "schedule": crontab(minute=0, hour=2),  # daily, 02:00 UTC
    },
    "icp-discover": {
        # Stage new lookalike candidates an hour after the refresh.
        "task": "icp.discover",
        "schedule": crontab(minute=0, hour=3),  # daily, 03:00 UTC
    },
    "agent-daily-digest": {
        # One summary email a day; idempotent via the digest:<date>
        # dedup key, so a beat double-fire can't send two.
        "task": "digest.send_daily",
        "schedule": crontab(minute=0, hour=settings.AGENT_DIGEST_HOUR_UTC),
    },
    "funding-poll-usaspending": {
        # Recent nonprofit grant awards → prospect_signals.  No-ops
        # unless USASPENDING_ENABLED.
        "task": "funding.poll_usaspending",
        "schedule": crontab(minute=0, hour=4),  # daily, 04:00 UTC
    },
    "funding-poll-irs-bmf": {
        # New 501(c)(3) rulings → prospect_signals.  No-ops unless
        # IRS_BMF_ENABLED + states configured.  Runs the 15th, after the
        # 2nd-Tuesday EO BMF refresh.
        "task": "funding.poll_irs_bmf",
        "schedule": crontab(minute=0, hour=5, day_of_month=15),
    },
}
