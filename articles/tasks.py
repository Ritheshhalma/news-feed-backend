import json
import logging
import time

import httpx
from celery import Task, shared_task
from django.db import IntegrityError
from django.utils import timezone

from articles.adapters.base import BaseAdapter
from articles.models import Article, ArticleMedia, ArticleSource, MSTArticleCategory, SourceFetchLog
from articles.services import llm_clean
from articles.services.ingest import ingest_articles
from articles.services.media import save_image

logger = logging.getLogger(__name__)


def _get_adapter(source: ArticleSource) -> BaseAdapter:
    """
    Factory that returns the correct BaseAdapter subclass for *source*.

    The choice is driven by source.parser_mode so the adapter accurately
    reflects how the source content is structured. Falls back to source_type
    for rows that pre-date the parser_mode field.
    """
    mode = source.parser_mode

    if mode in ("rss", "rss/multistage"):
        from articles.adapters.rss import RSSAdapter
        return RSSAdapter(source)

    if mode in ("html", "html/multistage"):
        from articles.adapters.html import HTMLAdapter
        return HTMLAdapter(source)

    if mode == "js/playwright":
        from articles.adapters.playwright_adapter import PlaywrightAdapter
        return PlaywrightAdapter(source)

    # Legacy fallback for rows without parser_mode set
    if source.source_type == "rss":
        from articles.adapters.rss import RSSAdapter
        return RSSAdapter(source)

    from articles.adapters.html import HTMLAdapter
    return HTMLAdapter(source)


def _publish_to_dead_letter(task_name, task_id, args, exc):
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
                delivery_mode=2,
            )
        logger.warning("DLQ: published %s[%s] — %s", task_name, task_id, exc)
    except Exception:
        logger.exception("DLQ: failed to publish %s[%s] to dead.letter", task_name, task_id)


class DLQTask(Task):
    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        _publish_to_dead_letter(self.name, task_id, args, exc)
        super().on_failure(exc, task_id, args, kwargs, einfo)


def _run_scrape(source_id: str, trigger_type: str, force: bool, task_self) -> dict:
    """Core scrape logic shared by scrape_source and scrape_playwright_source."""
    source = ArticleSource.objects.select_related("portal").get(id=source_id)
    log = SourceFetchLog.objects.create(
        source=source,
        task_id=task_self.request.id or "",
        trigger_type=trigger_type,
        attempt=task_self.request.retries,
        status="started",
    )
    started = time.monotonic()
    logger.info(
        "scrape started: source=%s mode=%s trigger=%s attempt=%s force=%s",
        source_id, source.parser_mode, trigger_type, task_self.request.retries, force,
    )

    try:
        adapter = _get_adapter(source)
        raw_articles = adapter.fetch(force=force)
        counts, failures = ingest_articles(source.portal, raw_articles)
    except Exception as exc:
        logger.exception("scrape failed: source=%s", source_id)
        log.status = "failed"
        log.error_message = str(exc)
        log.finished_at = timezone.now()
        log.duration_ms = int((time.monotonic() - started) * 1000)
        log.save(update_fields=["status", "error_message", "finished_at", "duration_ms"])
        source.status = "failed"
        source.error_message = str(exc)
        source.save(update_fields=["status", "error_message"])
        raise

    log.status = "partial" if failures else "success"
    log.articles_found = len(raw_articles)
    log.articles_created = counts["created"]
    log.articles_updated = counts["updated"]
    log.articles_unchanged = counts["unchanged"]
    log.articles_failed = counts["failed"]
    if failures:
        log.details = {"failures": failures[:50]}
    log.finished_at = timezone.now()
    log.duration_ms = int((time.monotonic() - started) * 1000)
    log.save()

    source.status = "active"
    source.last_fetched_at = timezone.now()
    source.last_success_at = timezone.now()
    source.save(update_fields=["status", "last_fetched_at", "last_success_at"])

    logger.info("scrape finished: source=%s counts=%s", source_id, counts)
    return counts


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
def scrape_source(self, source_id: str, trigger_type: str = "scheduled", force: bool = False) -> dict:
    """Scheduled / on-demand scraper for RSS and HTML sources."""
    return _run_scrape(source_id, trigger_type, force, self)


@shared_task(
    base=DLQTask,
    bind=True,
    autoretry_for=(httpx.HTTPError, ConnectionError, OSError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=2,   # playwright is slow; fewer retries to avoid long queue blockage
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def scrape_playwright_source(self, source_id: str, trigger_type: str = "scheduled", force: bool = False) -> dict:
    """Low-concurrency Playwright worker for JavaScript-rendered sources."""
    return _run_scrape(source_id, trigger_type, force, self)


def _run_resync(source_id: str, task_self) -> dict:
    """Deep resync: re-fetch every stored article for this source's portal directly
    by its own source_url, bypassing listing-page discovery entirely. Unlike
    scrape_source (which can only see articles still on the current listing page),
    this reaches articles that have scrolled off the listing or were stuck with
    empty/wrong content from a failed extraction.
    """
    import asyncio

    from articles.adapters.base import RawArticle
    from articles.adapters.html import _fetch_article_pages

    source = ArticleSource.objects.select_related("portal").get(id=source_id)
    log = SourceFetchLog.objects.create(
        source=source,
        task_id=task_self.request.id or "",
        trigger_type="on_demand",
        attempt=task_self.request.retries,
        status="started",
    )
    started = time.monotonic()
    logger.info("resync started: source=%s portal=%s", source_id, source.portal_id)

    try:
        articles = list(
            Article.objects.filter(portal=source.portal)
            .select_related("category", "author")
        )
        raw_list = [
            RawArticle(
                title=a.title,
                source_url=a.source_url,
                content=a.content,
                image_url=a.thumbnail_url,
                published_at=None,
                category_name=a.category.name if a.category_id else None,
                author_name=a.author.name if a.author_id else None,
                tags=[],
            )
            for a in articles
        ]
        enriched = asyncio.run(_fetch_article_pages(raw_list))
        counts, failures = ingest_articles(source.portal, enriched)
    except Exception as exc:
        logger.exception("resync failed: source=%s", source_id)
        log.status = "failed"
        log.error_message = str(exc)
        log.finished_at = timezone.now()
        log.duration_ms = int((time.monotonic() - started) * 1000)
        log.save(update_fields=["status", "error_message", "finished_at", "duration_ms"])
        raise

    log.status = "partial" if failures else "success"
    log.articles_found = len(raw_list)
    log.articles_created = counts["created"]
    log.articles_updated = counts["updated"]
    log.articles_unchanged = counts["unchanged"]
    log.articles_failed = counts["failed"]
    if failures:
        log.details = {"failures": failures[:50]}
    log.finished_at = timezone.now()
    log.duration_ms = int((time.monotonic() - started) * 1000)
    log.save()

    logger.info("resync finished: source=%s counts=%s", source_id, counts)
    return counts


@shared_task(
    base=DLQTask,
    bind=True,
    acks_late=True,
    soft_time_limit=1800,
    time_limit=1860,
)
def resync_source(self, source_id: str) -> dict:
    """On-demand deep resync of every stored article for a source — see _run_resync."""
    return _run_resync(source_id, self)


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
        raise

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


@shared_task(
    base=DLQTask,
    bind=True,
    autoretry_for=(llm_clean.LLMCleanError, Article.DoesNotExist),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
)
def clean_article_llm(self, article_id: str) -> None:
    """Async title/content repair + category classification via DeepSeek.
    See docs/superpowers/specs/2026-07-05-deepseek-article-cleaning-design.md.

    May only write title, content, category, llm_cleaned_at, llm_clean_status.
    Must never write content_hash or hashed_key — both are scraper-owned raw
    fingerprints that change detection depends on staying pinned to the raw
    scrape, independent of what LLM cleaning does to the title/content
    display fields.
    """
    article = Article.objects.get(id=article_id)

    try:
        result = llm_clean.clean_article(article.title, article.content)
    except llm_clean.LLMCleanError:
        logger.warning("LLM clean failed for article %s, will retry if attempts remain", article_id)
        Article.objects.filter(id=article_id).update(
            llm_clean_status="failed", llm_cleaned_at=timezone.now(),
        )
        raise

    category = MSTArticleCategory.objects.filter(name__iexact=result.category).first()
    if category is None:
        try:
            category = MSTArticleCategory.objects.create(
                name=result.category, is_llm_suggested=result.is_new_category,
            )
        except IntegrityError:
            # Lost a race with a concurrent clean_article_llm run that created
            # the same category first — fetch the row it just created.
            category = MSTArticleCategory.objects.filter(name__iexact=result.category).first()

    article.title = result.title
    article.content = result.content
    article.category = category
    article.llm_cleaned_at = timezone.now()
    article.llm_clean_status = "success"
    article.save(update_fields=[
        "title", "content", "category",
        "llm_cleaned_at", "llm_clean_status",
    ])
    logger.info("LLM clean succeeded for article %s (category=%s)", article_id, category.name)
