import pytest
from articles.models import MSTArticlePortal, ArticleSource, SourceFetchLog, Article
from articles.adapters.base import RawArticle
from articles.tasks import scrape_source

pytestmark = pytest.mark.django_db


def test_scrape_source_writes_success_log_and_creates_articles(mocker):
    portal = MSTArticlePortal.objects.create(name="Task Portal")
    source = ArticleSource.objects.create(
        url="https://x.com/feed", source_type="rss", parser_mode="rss", portal=portal,
    )
    fake_raw = [RawArticle(title="Story A", source_url="https://x.com/a", content="body",
                            image_url=None, published_at=None, category_name=None)]
    mocker.patch("articles.adapters.rss.RSSAdapter.fetch", return_value=fake_raw)

    result = scrape_source(str(source.id), trigger_type="scheduled")

    log = SourceFetchLog.objects.get(source=source)
    assert log.status == "success"
    assert log.articles_created == 1
    assert log.finished_at is not None
    assert Article.objects.filter(source_url="https://x.com/a").exists()
    assert result["created"] == 1


def test_scrape_source_records_failure_without_raising(mocker):
    portal = MSTArticlePortal.objects.create(name="Task Portal Fail")
    source = ArticleSource.objects.create(
        url="https://broken.com/feed", source_type="rss", parser_mode="rss", portal=portal,
    )
    mocker.patch("articles.adapters.rss.RSSAdapter.fetch", side_effect=ConnectionError("DNS failure"))

    with pytest.raises(ConnectionError):
        scrape_source(str(source.id), trigger_type="scheduled")

    log = SourceFetchLog.objects.get(source=source)
    assert log.status == "failed"
    assert "DNS failure" in log.error_message


def test_scrape_source_with_force_passes_flag_to_adapter(mocker):
    portal = MSTArticlePortal.objects.create(name="Force Portal")
    source = ArticleSource.objects.create(
        url="https://news.com/", source_type="html", parser_mode="html/multistage", portal=portal,
    )
    mock_fetch = mocker.patch("articles.adapters.html.HTMLAdapter.fetch", return_value=[])

    scrape_source(str(source.id), trigger_type="on_demand", force=True)

    mock_fetch.assert_called_once_with(force=True)
