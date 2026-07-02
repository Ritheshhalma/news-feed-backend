import pathlib
import feedparser
import pytest
from articles.models import MSTArticlePortal, ArticleSource
from articles.adapters.rss import RSSAdapter

pytestmark = pytest.mark.django_db

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "sample_feed.xml"


def test_rss_adapter_parses_fixture_feed_into_raw_articles(mocker):
    portal = MSTArticlePortal.objects.create(name="Sample Portal")
    source = ArticleSource.objects.create(url="https://example.com/feed.xml", source_type="rss", portal=portal)
    mocker.patch("feedparser.parse", return_value=feedparser.parse(str(FIXTURE_PATH)))

    raw_articles = RSSAdapter(source).fetch()

    assert len(raw_articles) == 2
    assert raw_articles[0].title == "Fire breaks out at restaurant"
    assert raw_articles[0].source_url == "https://example.com/articleshow/1.cms"
    assert raw_articles[0].category_name == "delhi"


def test_rss_adapter_skips_malformed_entry_without_aborting_feed(mocker):
    portal = MSTArticlePortal.objects.create(name="Sample Portal Malformed")
    source = ArticleSource.objects.create(url="https://example.com/feed.xml", source_type="rss", portal=portal)

    good_entry = feedparser.parse(str(FIXTURE_PATH)).entries[0]
    malformed_entry = mocker.Mock(spec=[])  # no attributes at all -> AttributeError on entry.title
    fake_feed = mocker.Mock(entries=[malformed_entry, good_entry])
    mocker.patch("feedparser.parse", return_value=fake_feed)

    raw_articles = RSSAdapter(source).fetch()

    assert len(raw_articles) == 1
    assert raw_articles[0].title == good_entry.title
