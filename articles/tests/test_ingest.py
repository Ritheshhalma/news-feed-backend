import pytest
from articles.models import MSTArticlePortal, Article
from articles.adapters.base import RawArticle
from articles.services.ingest import ingest_articles

pytestmark = pytest.mark.django_db


def _raw(title="Story", url="https://example.com/1", content="body"):
    return RawArticle(title=title, source_url=url, content=content, image_url=None, published_at=None, category_name=None)


def test_ingest_inserts_new_article():
    portal = MSTArticlePortal.objects.create(name="Ingest Portal 1")
    result, failures = ingest_articles(portal, [_raw()])
    assert result == {"created": 1, "updated": 0, "unchanged": 0, "failed": 0}
    assert failures == []
    assert Article.objects.filter(portal=portal).count() == 1


def test_ingest_is_idempotent_for_unchanged_content():
    portal = MSTArticlePortal.objects.create(name="Ingest Portal 2")
    ingest_articles(portal, [_raw(content="same body")])
    result, failures = ingest_articles(portal, [_raw(content="same body")])
    assert result == {"created": 0, "updated": 0, "unchanged": 1, "failed": 0}
    assert failures == []
    assert Article.objects.filter(portal=portal).count() == 1


def test_ingest_updates_content_when_changed_and_bumps_updated_at():
    portal = MSTArticlePortal.objects.create(name="Ingest Portal 3")
    ingest_articles(portal, [_raw(content="original body")])
    article_before = Article.objects.get(portal=portal)

    result, failures = ingest_articles(portal, [_raw(content="revised body")])

    article_after = Article.objects.get(portal=portal)
    assert result == {"created": 0, "updated": 1, "unchanged": 0, "failed": 0}
    assert failures == []
    assert article_after.content == "revised body"
    assert article_after.updated_at >= article_before.updated_at


def test_ingest_skips_one_bad_item_without_aborting_the_batch():
    portal = MSTArticlePortal.objects.create(name="Ingest Portal 4")
    good = _raw(title="Good story", url="https://example.com/good")
    bad = RawArticle(title="", source_url="https://example.com/bad", content="x", image_url=None, published_at=None, category_name=None)

    result, failures = ingest_articles(portal, [bad, good])

    assert result["created"] == 1
    assert failures == []
    assert Article.objects.filter(portal=portal, title="Good story").exists()


def test_ingest_recovers_from_db_exception_on_one_item_without_aborting_batch(mocker):
    portal = MSTArticlePortal.objects.create(name="Ingest Portal Exception")
    good_before = _raw(title="Story before", url="https://example.com/exc-before")
    bad = _raw(title="Story that explodes", url="https://example.com/exc-bad")
    good_after = _raw(title="Story after", url="https://example.com/exc-after")

    original_create = Article.objects.create
    def create_or_raise(*args, **kwargs):
        if kwargs.get("title") == "Story that explodes":
            raise Exception("simulated DB failure")
        return original_create(*args, **kwargs)
    mocker.patch.object(Article.objects, "create", side_effect=create_or_raise)

    result, failures = ingest_articles(portal, [good_before, bad, good_after])

    assert result["created"] == 2
    assert result["failed"] == 1
    assert len(failures) == 1
    assert failures[0]["source_url"] == "https://example.com/exc-bad"
    assert Article.objects.filter(portal=portal, title="Story before").exists()
    assert Article.objects.filter(portal=portal, title="Story after").exists()
    assert not Article.objects.filter(portal=portal, title="Story that explodes").exists()


def test_ingest_pushes_feed_notification_when_articles_change(mocker):
    mock_send = mocker.patch("articles.services.ingest._push_feed_update")
    portal = MSTArticlePortal.objects.create(name="Push Portal")
    ingest_articles(portal, [_raw(title="Pushed story", url="https://example.com/push1")])
    mock_send.assert_called_once_with(created=1, updated=0)


def test_ingest_does_not_push_when_nothing_changed(mocker):
    portal = MSTArticlePortal.objects.create(name="Push Portal 2")
    ingest_articles(portal, [_raw(content="same", url="https://example.com/push2")])
    mock_send = mocker.patch("articles.services.ingest._push_feed_update")
    ingest_articles(portal, [_raw(content="same", url="https://example.com/push2")])
    mock_send.assert_not_called()
