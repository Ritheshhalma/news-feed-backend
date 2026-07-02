import io
import pytest
from PIL import Image
from articles.models import MSTArticlePortal, Article, ArticleMedia
from articles.tasks import process_article_image

pytestmark = pytest.mark.django_db


def _fake_image_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1200), color="red").save(buf, format="JPEG")
    return buf.getvalue()


def test_process_article_image_sets_thumbnail_and_creates_media(mocker):
    portal = MSTArticlePortal.objects.create(name="Media Portal")
    article = Article.objects.create(title="Story", source_url="https://x.com/m1",
                                      hashed_key="m1", content="body", portal=portal)
    mocker.patch("httpx.get", return_value=mocker.Mock(content=_fake_image_bytes(), raise_for_status=lambda: None))
    # Mock save_image so the test is agnostic to the active storage backend (local vs S3)
    mocker.patch(
        "articles.tasks.save_image",
        side_effect=lambda aid, data, suffix, max_width=None: f"/media/news-images/{aid}-{suffix}.jpg",
    )

    process_article_image(str(article.id), "https://source.com/hero.jpg")

    article.refresh_from_db()
    assert article.thumbnail_url.endswith(f"{article.id}-thumb.jpg")
    assert ArticleMedia.objects.get(article=article).url.endswith(f"{article.id}-full.jpg")


def test_process_article_image_failure_raises_for_retry(mocker):
    portal = MSTArticlePortal.objects.create(name="Media Portal Fail")
    article = Article.objects.create(title="Story 2", source_url="https://x.com/m2",
                                      hashed_key="m2", content="body", portal=portal)
    mocker.patch("httpx.get", side_effect=ConnectionError("image host down"))
    mocker.patch("articles.tasks._publish_to_dead_letter")  # don't hit RabbitMQ in tests

    # Task now re-raises so Celery can retry; after max retries it goes to DLQ
    with pytest.raises(ConnectionError):
        process_article_image(str(article.id), "https://source.com/broken.jpg")

    article.refresh_from_db()
    assert article.thumbnail_url is None  # article unaffected
