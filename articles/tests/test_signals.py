import pytest
from django_celery_beat.models import PeriodicTask
from articles.models import MSTArticlePortal, ArticleSource

pytestmark = pytest.mark.django_db


def test_saving_an_active_source_registers_a_periodic_task():
    portal = MSTArticlePortal.objects.create(name="Beat Portal")
    source = ArticleSource.objects.create(
        url="https://x.com/feed", source_type="rss", portal=portal,
        status="active", scrape_interval_minutes=15,
    )
    task = PeriodicTask.objects.get(name=f"scrape_source_{source.id}")
    assert task.task == "articles.tasks.scrape_source"
    assert task.enabled is True
    assert task.interval.every == 15


def test_saving_a_failed_source_disables_its_periodic_task():
    portal = MSTArticlePortal.objects.create(name="Beat Portal Fail")
    source = ArticleSource.objects.create(
        url="https://x.com/feed2", source_type="rss", portal=portal, status="active",
    )
    source.status = "failed"
    source.save()
    task = PeriodicTask.objects.get(name=f"scrape_source_{source.id}")
    assert task.enabled is False
