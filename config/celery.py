import os
from celery import Celery
from kombu import Exchange, Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")

app = Celery("newsfeed")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ── Dead Letter Queue setup ────────────────────────────────────────────────
# Messages that exhaust all retries are routed to dead.letter instead of
# being silently discarded. Use `manage.py requeue_dead_letters` to replay
# or inspect them.

_dead_exchange = Exchange("dead.letter", type="direct", durable=True)

app.conf.task_queues = [
    Queue(
        "scrape.scheduled",
        Exchange("scrape.scheduled", type="direct"),
        routing_key="scrape.scheduled",
        queue_arguments={"x-dead-letter-exchange": "dead.letter",
                         "x-dead-letter-routing-key": "dead.letter"},
    ),
    Queue(
        "scrape.ondemand",
        Exchange("scrape.ondemand", type="direct"),
        routing_key="scrape.ondemand",
        queue_arguments={"x-dead-letter-exchange": "dead.letter",
                         "x-dead-letter-routing-key": "dead.letter"},
    ),
    Queue(
        "media.process",
        Exchange("media.process", type="direct"),
        routing_key="media.process",
        queue_arguments={"x-dead-letter-exchange": "dead.letter",
                         "x-dead-letter-routing-key": "dead.letter"},
    ),
    Queue(
        "live.poll",
        Exchange("live.poll", type="direct"),
        routing_key="live.poll",
        queue_arguments={"x-dead-letter-exchange": "dead.letter",
                         "x-dead-letter-routing-key": "dead.letter"},
    ),
    Queue(
        "scrape.playwright",
        Exchange("scrape.playwright", type="direct"),
        routing_key="scrape.playwright",
        queue_arguments={"x-dead-letter-exchange": "dead.letter",
                         "x-dead-letter-routing-key": "dead.letter"},
    ),
    Queue(
        "dead.letter",
        _dead_exchange,
        routing_key="dead.letter",
    ),
]

app.conf.task_routes = {
    "articles.tasks.scrape_source": {"queue": "scrape.scheduled"},
    "articles.tasks.scrape_playwright_source": {"queue": "scrape.playwright"},
    "articles.tasks.refresh_source": {"queue": "scrape.ondemand"},
    "articles.tasks.validate_source": {"queue": "scrape.ondemand"},
    "articles.tasks.resync_source": {"queue": "scrape.ondemand"},
    "articles.tasks.process_article_image": {"queue": "media.process"},
    "articles.tasks.poll_live_articles": {"queue": "live.poll"},
}
