import pathlib
import pytest
from articles.models import MSTArticlePortal, ArticleSource
from articles.adapters.html import HTMLAdapter

pytestmark = pytest.mark.django_db

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "sample_article.html"


def test_html_adapter_parses_one_fixture_page_via_og_meta(mocker):
    portal = MSTArticlePortal.objects.create(name="HTML Portal")
    source = ArticleSource.objects.create(url="https://example.com/article-1", source_type="html", portal=portal)
    html = FIXTURE_PATH.read_text()
    mock_response = mocker.Mock(text=html, status_code=200)
    mocker.patch("httpx.get", return_value=mock_response)

    raw_articles = HTMLAdapter(source).fetch()

    assert len(raw_articles) == 1
    assert raw_articles[0].title == "Sample HTML Article"
    assert raw_articles[0].source_url == "https://example.com/article-1"
    assert raw_articles[0].image_url == "https://example.com/hero.jpg"
    assert "Full article content" in raw_articles[0].content


def test_html_adapter_returns_empty_list_when_page_lacks_og_title(mocker):
    """Verify graceful degradation: non-parseable page returns empty list, not crash."""
    portal = MSTArticlePortal.objects.create(name="HTML Portal")
    source = ArticleSource.objects.create(url="https://example.com/not-an-article", source_type="html", portal=portal)
    html = "<html><head></head><body>Just some random page with no og:title.</body></html>"
    mock_response = mocker.Mock(text=html, status_code=200)
    mocker.patch("httpx.get", return_value=mock_response)

    raw_articles = HTMLAdapter(source).fetch()

    assert len(raw_articles) == 0


def test_html_adapter_logs_warning_when_og_title_missing(mocker, caplog):
    portal = MSTArticlePortal.objects.create(name="No Title Portal")
    source = ArticleSource.objects.create(url="https://example.com/no-title", source_type="html", portal=portal)
    mocker.patch("httpx.get", return_value=mocker.Mock(text="<html><body>no meta tags</body></html>", raise_for_status=lambda: None))

    with caplog.at_level("WARNING"):
        result = HTMLAdapter(source).fetch()

    assert result == []
    assert "lacks og:title" in caplog.text
