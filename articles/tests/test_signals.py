import pytest
from django_celery_beat.models import PeriodicTask
from articles.models import MSTArticlePortal, ArticleSource

pytestmark = pytest.mark.django_db


def test_saving_an_active_rss_source_registers_scrape_source_task():
    portal = MSTArticlePortal.objects.create(name="Beat Portal RSS")
    source = ArticleSource.objects.create(
        url="https://x.com/feed", source_type="rss", parser_mode="rss", portal=portal,
        status="active", scrape_interval_minutes=15,
    )
    task = PeriodicTask.objects.get(name=f"scrape_source_{source.id}")
    assert task.task == "articles.tasks.scrape_source"
    assert task.enabled is True
    assert task.interval.every == 15


def test_saving_an_active_playwright_source_registers_playwright_task():
    portal = MSTArticlePortal.objects.create(name="Beat Portal JS")
    source = ArticleSource.objects.create(
        url="https://spa.com/news", source_type="html", parser_mode="js/playwright", portal=portal,
        status="active", scrape_interval_minutes=30,
    )
    task = PeriodicTask.objects.get(name=f"scrape_source_{source.id}")
    assert task.task == "articles.tasks.scrape_playwright_source"
    assert task.enabled is True


def test_saving_an_html_multistage_source_registers_scrape_source_task():
    portal = MSTArticlePortal.objects.create(name="Beat Portal HTML")
    source = ArticleSource.objects.create(
        url="https://news.com/", source_type="html", parser_mode="html/multistage", portal=portal,
        status="active",
    )
    task = PeriodicTask.objects.get(name=f"scrape_source_{source.id}")
    assert task.task == "articles.tasks.scrape_source"


def test_saving_a_failed_source_disables_its_periodic_task():
    portal = MSTArticlePortal.objects.create(name="Beat Portal Fail")
    source = ArticleSource.objects.create(
        url="https://x.com/feed2", source_type="rss", parser_mode="rss", portal=portal, status="active",
    )
    source.status = "failed"
    source.save()
    task = PeriodicTask.objects.get(name=f"scrape_source_{source.id}")
    assert task.enabled is False
