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
}
