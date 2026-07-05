"""
python manage.py resync_articles [--source-url URL] [--portal NAME] [--empty-content]
                                  [--limit N] [--dry-run]

Re-fetches articles directly from their own source_url and re-runs Stage 2
(trafilatura) extraction, bypassing listing-page discovery entirely. Fixes
two cases the normal "Refresh" action can't reach:

  - Articles whose story has scrolled off the source's listing page (Refresh
    can only discover URLs still present on that page).
  - Articles stuck with empty/short content from a failed extraction.

Writes go through ingest_articles(), so a corrected title/content updates the
existing row in place via the source_url lookup rather than creating a
duplicate.
"""
import asyncio
import logging

from django.core.management.base import BaseCommand

from articles.adapters.base import RawArticle
from articles.adapters.html import _fetch_article_pages
from articles.models import Article
from articles.services.ingest import ingest_articles

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Re-fetch articles directly from their source_url, bypassing listing-page discovery"

    def add_arguments(self, parser):
        parser.add_argument("--source-url", help="Resync a single article by source_url")
        parser.add_argument("--portal", help="Limit to a portal name")
        parser.add_argument(
            "--empty-content", action="store_true",
            help="Limit to articles with empty/blank content",
        )
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true", help="Preview without writing")

    def handle(self, *args, **options):
        qs = Article.objects.select_related("portal", "category", "author").order_by("-created_at")
        if options["source_url"]:
            qs = qs.filter(source_url=options["source_url"])
        if options["portal"]:
            qs = qs.filter(portal__name=options["portal"])
        if options["empty_content"]:
            qs = qs.filter(content="")
        if options["limit"]:
            qs = qs[: options["limit"]]

        articles = list(qs)
        if not articles:
            self.stdout.write("No matching articles.")
            return

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

        self.stdout.write(f"Re-fetching {len(articles)} article(s) directly from source_url...")
        enriched = asyncio.run(_fetch_article_pages(raw_list))

        if options["dry_run"]:
            for article, fresh in zip(articles, enriched):
                new_title = (fresh.title or article.title)[:255]
                new_content = fresh.content or article.content
                if new_title != article.title or new_content != article.content:
                    self.stdout.write(f"[{article.id}] {article.source_url}")
                    if new_title != article.title:
                        self.stdout.write(f"  title:   {article.title[:80]!r}\n        -> {new_title[:80]!r}")
                    if new_content != article.content:
                        self.stdout.write(f"  content: {article.content[:60]!r}\n        -> {new_content[:60]!r}")
            self.stdout.write(self.style.WARNING("Dry run — no changes written"))
            return

        by_portal: dict = {}
        for article, fresh in zip(articles, enriched):
            by_portal.setdefault(article.portal, []).append(fresh)

        total_counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
        for portal, raws in by_portal.items():
            counts, failures = ingest_articles(portal, raws)
            for k in total_counts:
                total_counts[k] += counts[k]
            for f in failures:
                self.stdout.write(self.style.ERROR(f"  failed: {f['source_url']} — {f['error']}"))

        self.stdout.write(self.style.SUCCESS(
            f"Done. updated={total_counts['updated']} unchanged={total_counts['unchanged']} "
            f"failed={total_counts['failed']} (of {len(articles)})"
        ))
