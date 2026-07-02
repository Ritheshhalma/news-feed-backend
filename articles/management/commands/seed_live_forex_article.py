from django.core.management.base import BaseCommand

from articles.models import Article, MSTArticleCategory, MSTArticlePortal


class Command(BaseCommand):
    help = "Create the is_live demo Article (USD/INR rate) if it doesn't already exist."

    def handle(self, *args, **options):
        portal, _ = MSTArticlePortal.objects.get_or_create(name="Markets Live")
        category, _ = MSTArticleCategory.objects.get_or_create(name="Markets")
        article, created = Article.objects.get_or_create(
            hashed_key="live-usd-inr-rate",
            defaults={
                "title": "USD/INR Live Exchange Rate",
                "source_url": "https://www.x-rates.com/calculator/?from=USD&to=INR&amount=1",
                "content": "Live USD to INR exchange rate, updated continuously.",
                "portal": portal,
                "category": category,
                "is_live": True,
                "live_poll_url": "https://www.x-rates.com/calculator/?from=USD&to=INR&amount=1",
            },
        )
        self.stdout.write(
            self.style.SUCCESS(f"{'Created' if created else 'Already exists'}: {article.title}")
        )
