from datetime import timedelta

import pytest
from django.utils import timezone

from articles.models import MSTArticlePortal, ArticleSource, SourceFetchLog

pytestmark = pytest.mark.django_db

def test_source_fetch_log_defaults():
    portal = MSTArticlePortal.objects.create(name="Log Test Portal")
    source = ArticleSource.objects.create(url="https://x.com/feed", source_type="rss", portal=portal)
    log = SourceFetchLog.objects.create(source=source, trigger_type="scheduled", status="started")
    assert log.attempt == 0
    assert log.articles_found == 0
    assert log.finished_at is None

def test_source_fetch_log_orders_by_started_at_desc_for_source():
    portal = MSTArticlePortal.objects.create(name="Log Order Portal")
    source = ArticleSource.objects.create(url="https://y.com/feed", source_type="rss", portal=portal)
    now = timezone.now()

    first = SourceFetchLog.objects.create(source=source, trigger_type="scheduled", status="success")
    SourceFetchLog.objects.filter(pk=first.pk).update(started_at=now - timedelta(minutes=5))

    second = SourceFetchLog.objects.create(source=source, trigger_type="on_demand", status="success", attempt=1)
    SourceFetchLog.objects.filter(pk=second.pk).update(started_at=now)

    logs = list(SourceFetchLog.objects.filter(source=source).order_by("-started_at"))
    assert logs[0].id == second.id
    assert logs[1].id == first.id
