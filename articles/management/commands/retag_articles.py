"""
python manage.py retag_articles

Backfills tags for existing articles using:
  1. Category name  → tag  (always available if article has a category)
  2. URL path slug  → tag  (world, india, sports, technology, …)

Run with --all to re-tag articles that already have tags.
Run with --dry-run to preview without writing.
"""
import logging

from django.core.management.base import BaseCommand

from articles.adapters.html import _tags_from_url
from articles.models import Article, ArticleTagMap, MSTTag

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill tags for existing articles using category and URL path approaches"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Re-tag all articles, not just those with no tags",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be done without making DB changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        retag_all = options["all"]

        qs = Article.objects.select_related("category")
        if not retag_all:
            qs = qs.filter(articletagmap__isnull=True).distinct()

        total = qs.count()
        self.stdout.write(f"Processing {total} articles (dry_run={dry_run}, all={retag_all})")

        tagged = 0
        for article in qs.iterator():
            tag_names: set[str] = set()

            if article.category:
                tag_names.add(article.category.name)

            tag_names.update(_tags_from_url(article.source_url))

            if not tag_names:
                continue

            if dry_run:
                self.stdout.write(
                    f"  [{article.id}] {article.title[:60]!r} → {sorted(tag_names)}"
                )
                continue

            for name in tag_names:
                tag, _ = MSTTag.objects.get_or_create(name=name)
                ArticleTagMap.objects.get_or_create(article=article, tag=tag)

            tagged += 1

        if dry_run:
            self.stdout.write(f"Dry run complete — {total} articles would be processed.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Done. Tagged {tagged} / {total} articles."))
