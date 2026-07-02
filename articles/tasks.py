import json
import logging
import time

import httpx
from celery import Task, shared_task
from django.utils import timezone

from articles.adapters.html import HTMLAdapter
from articles.adapters.rss import RSSAdapter
from articles.models import Article, ArticleMedia, ArticleSource, SourceFetchLog
from articles.services.ingest import ingest_articles
from articles.services.media import save_image

logger = logging.getLogger(__name__)

_ADAPTERS = {"rss": RSSAdapter, "html": HTMLAdapter}


def _publish_to_dead_letter(task_name, task_id, args, exc):
    """Publish a permanently failed task to the dead.letter queue via RabbitMQ."""
    try:
        from django.conf import settings
        from kombu import Connection, Exchange, Queue

        broker_url = getattr(settings, "CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")
        payload = json.dumps({
            "task": task_name,
            "id": task_id,
            "args": list(args or []),
            "error": str(exc),
        }).encode()

        dead_exchange = Exchange("dead.letter", type="direct", durable=True)
        dead_queue = Queue("dead.letter", dead_exchange, routing_key="dead.letter", durable=True)

        with Connection(broker_url) as conn:
            dead_queue.maybe_bind(conn)
            dead_queue.declare()
            producer = conn.Producer()
            producer.publish(
                payload,
                exchange=dead_exchange,
                routing_key="dead.letter",
                content_type="application/json",
                delivery_mode=2,  # persistent
            )
        logger.warning("DLQ: published %s[%s] — %s", task_name, task_id, exc)
    except Exception:
        logger.exception("DLQ: failed to publish %s[%s] to dead.letter", task_name, task_id)


class DLQTask(Task):
    """Base task that publishes to dead.letter on permanent failure (after all retries)."""
    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # on_failure is only called when the task gives up entirely — always DLQ it
        _publish_to_dead_letter(self.name, task_id, args, exc)
        super().on_failure(exc, task_id, args, kwargs, einfo)


@shared_task(
    base=DLQTask,
    bind=True,
    autoretry_for=(httpx.HTTPError, ConnectionError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
)
def scrape_source(self, source_id: str, trigger_type: str = "scheduled") -> dict:
    source = ArticleSource.objects.select_related("portal").get(id=source_id)
    log = SourceFetchLog.objects.create(
        source=source,
        task_id=self.request.id or "",
        trigger_type=trigger_type,
        attempt=self.request.retries,
        status="started",
    )
    started = time.monotonic()
    logger.info(
        "scrape_source started: source=%s trigger=%s attempt=%s",
        source_id, trigger_type, self.request.retries,
    )

    try:
        adapter_cls = _ADAPTERS[source.source_type]
        raw_articles = adapter_cls(source).fetch()
        counts = ingest_articles(source.portal, raw_articles)
    except Exception as exc:
        logger.exception("scrape_source failed: source=%s", source_id)
        log.status = "failed"
        log.error_message = str(exc)
        log.finished_at = timezone.now()
        log.duration_ms = int((time.monotonic() - started) * 1000)
        log.save(update_fields=["status", "error_message", "finished_at", "duration_ms"])
        source.status = "failed"
        source.error_message = str(exc)
        source.save(update_fields=["status", "error_message"])
        raise

    log.status = "success"
    log.articles_found = len(raw_articles)
    log.articles_created = counts["created"]
    log.articles_updated = counts["updated"]
    log.articles_unchanged = counts["unchanged"]
    log.finished_at = timezone.now()
    log.duration_ms = int((time.monotonic() - started) * 1000)
    log.save()

    source.status = "active"
    source.last_fetched_at = timezone.now()
    source.last_success_at = timezone.now()
    source.save(update_fields=["status", "last_fetched_at", "last_success_at"])

    logger.info("scrape_source finished: source=%s counts=%s", source_id, counts)
    return counts


@shared_task(
    base=DLQTask,
    bind=True,
    autoretry_for=(httpx.HTTPError, ConnectionError, OSError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
)
def process_article_image(self, article_id: str, source_image_url: str) -> None:
    try:
        response = httpx.get(source_image_url, timeout=10.0)
        response.raise_for_status()
        raw_bytes = response.content
    except Exception:
        logger.warning("Image fetch failed for article %s, will retry", article_id)
        raise  # autoretry_for handles retries; on_failure sends to DLQ after max_retries

    thumb_url = save_image(article_id, raw_bytes, suffix="thumb", max_width=480)
    full_url  = save_image(article_id, raw_bytes, suffix="full",  max_width=None)

    Article.objects.filter(id=article_id).update(thumbnail_url=thumb_url)
    ArticleMedia.objects.update_or_create(
        article_id=article_id, defaults={"url": full_url, "type": "image"}
    )


@shared_task
def poll_live_articles() -> None:
    from articles.services.live_poll import poll_one_live_article
    for article in Article.objects.filter(is_live=True).exclude(live_poll_url=""):
        poll_one_live_article(article)
