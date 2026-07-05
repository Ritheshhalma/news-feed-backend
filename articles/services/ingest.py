import logging
from datetime import datetime
from email.utils import parsedate_to_datetime

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from articles.models import Article, ArticleTagMap, MSTArticleCategory, MSTAuthor, MSTTag
from articles.services.dedup import content_hash, title_hash

logger = logging.getLogger(__name__)


def _push_feed_update(created: int, updated: int) -> None:
    layer = get_channel_layer()
    if layer is None:
        return  # channel layer not configured — no-op, not a crash
    async_to_sync(layer.group_send)("feed", {
        "type": "feed.update",
        "data": {"new_count": created, "updated_count": updated},
    })


def _attach_tags(article, tag_names: list[str]) -> None:
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag, _ = MSTTag.objects.get_or_create(name=name)
        ArticleTagMap.objects.get_or_create(article=article, tag=tag)


def ingest_articles(portal, raw_articles) -> dict:
    """Shared dedup/content-change pipeline — see design doc §3.
    Runs for both scheduled scrape and on-demand refresh; each item is its
    own transaction so one bad item can't roll back the rest of the batch.
    """
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    failures: list[dict] = []

    for raw in raw_articles:
        if not raw.title or not raw.source_url:
            logger.warning("Skipping article with missing title/source_url from portal %s", portal.name)
            continue

        try:
            with transaction.atomic():
                raw.title = raw.title[:255]
                raw.source_url = raw.source_url[:2000]
                key = title_hash(raw.title)
                # source_url is the stable identity for "have we already stored this
                # article" — title can legitimately change on re-fetch (e.g. a scraper
                # bug fix, or the source editing its headline). Falling back to the
                # title hash only for URLs we've never seen keeps cross-portal dedup
                # (same story, different source) without losing re-fetched updates.
                existing = Article.objects.filter(source_url=raw.source_url).first()
                if existing is None:
                    existing = Article.objects.filter(hashed_key=key).first()

                category = None
                if raw.category_name:
                    category, _ = MSTArticleCategory.objects.get_or_create(
                        name=raw.category_name.strip()
                    )

                author = None
                if raw.author_name:
                    author_name = raw.author_name.strip()
                    author, _ = MSTAuthor.objects.get_or_create(
                        name=author_name,
                        defaults={"short_name": author_name[:255]},
                    )

                published_at = None
                if raw.published_at:
                    try:
                        published_at = parsedate_to_datetime(raw.published_at)   # RFC 2822
                    except Exception:
                        try:
                            # ISO 8601 (from feedparser published_parsed or HTML meta)
                            published_at = datetime.fromisoformat(
                                raw.published_at.replace("Z", "+00:00")
                            )
                        except Exception:
                            published_at = None
                    if published_at and timezone.is_naive(published_at):
                        published_at = timezone.make_aware(published_at)

                if existing is None:
                    article = Article.objects.create(
                        title=raw.title,
                        source_url=raw.source_url,
                        hashed_key=key,
                        content=raw.content,
                        content_hash=content_hash(raw.content),
                        thumbnail_url=raw.image_url,
                        portal=portal,
                        category=category,
                        author=author,
                        published_at=published_at,
                    )
                    tag_names = list(raw.tags)
                    if category:
                        tag_names.append(category.name)
                    _attach_tags(article, tag_names)
                    if raw.image_url:
                        from articles.tasks import process_article_image  # avoids circular import
                        process_article_image.delay(str(article.id), raw.image_url)
                    counts["created"] += 1
                    continue

                new_hash = content_hash(raw.content)
                update_fields = ["updated_at"]
                title_changed = existing.title != raw.title
                content_changed = existing.content_hash != new_hash
                if title_changed:
                    existing.title = raw.title
                    existing.hashed_key = key
                    update_fields += ["title", "hashed_key"]
                if content_changed:
                    existing.content = raw.content
                    existing.content_hash = new_hash
                    update_fields += ["content", "content_hash"]
                if title_changed or content_changed:
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1
                if category and existing.category_id != category.id:
                    existing.category = category
                    update_fields.append("category")
                if author and existing.author_id != author.id:
                    existing.author = author
                    update_fields.append("author")
                if published_at and not existing.published_at:
                    existing.published_at = published_at
                    update_fields.append("published_at")
                if len(update_fields) > 1:
                    existing.save(update_fields=update_fields)
                tag_names = list(raw.tags)
                if category:
                    tag_names.append(category.name)
                _attach_tags(existing, tag_names)
                # Backfill local image for articles that still carry a remote URL
                if raw.image_url and existing.thumbnail_url and existing.thumbnail_url.startswith("http"):
                    from articles.tasks import process_article_image
                    process_article_image.delay(str(existing.id), raw.image_url)
        except Exception as exc:
            logger.exception("Failed to ingest article %r from portal %s", raw.title, portal.name)
            counts["failed"] += 1
            failures.append({
                "title": (raw.title or "")[:200],
                "source_url": raw.source_url,
                "error": str(exc),
            })
            continue

    if counts["created"] or counts["updated"]:
        _push_feed_update(created=counts["created"], updated=counts["updated"])

    return counts, failures
