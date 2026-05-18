from celery import Celery

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
}
