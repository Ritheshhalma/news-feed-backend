import pytest
from articles.models import MSTArticlePortal, ArticleSource

pytestmark = pytest.mark.django_db

def test_article_source_defaults_to_pending_validation():
    portal = MSTArticlePortal.objects.create(name="Test Portal")
    source = ArticleSource.objects.create(
        url="https://example.com/feed.xml", source_type="rss", portal=portal,
    )
    assert source.status == "pending_validation"
    assert source.scrape_interval_minutes == 30
    assert source.error_message == ""

def test_article_source_type_choices_enforced_at_app_level():
    portal = MSTArticlePortal.objects.create(name="Test Portal 2")
    source = ArticleSource.objects.create(
        url="https://example.com/page", source_type="html", portal=portal,
    )
    assert source.get_source_type_display() == "HTML"
