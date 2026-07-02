import pytest
from django.db import IntegrityError
from articles.models import MSTArticlePortal, MSTArticleCategory, MSTTag, MSTAuthor, Article, ArticleMedia, ArticleTagMap

pytestmark = pytest.mark.django_db

def test_article_portal_name_is_unique():
    MSTArticlePortal.objects.create(name="Times of India")
    with pytest.raises(IntegrityError):
        MSTArticlePortal.objects.create(name="Times of India")

def test_article_category_str_returns_name():
    cat = MSTArticleCategory.objects.create(name="Sports")
    assert str(cat) == "Sports"

def test_tag_and_author_creation():
    tag = MSTTag.objects.create(name="cricket")
    author = MSTAuthor.objects.create(name="Jane Doe", short_name="jane-doe")
    assert tag.id is not None
    assert author.short_name == "jane-doe"

def test_article_requires_unique_hashed_key():
    portal = MSTArticlePortal.objects.create(name="NDTV")
    Article.objects.create(
        title="Test story", source_url="https://example.com/a",
        hashed_key="hash1", content="body", portal=portal,
    )
    with pytest.raises(IntegrityError):
        Article.objects.create(
            title="Different title", source_url="https://example.com/b",
            hashed_key="hash1", content="body2", portal=portal,
        )

def test_article_defaults():
    portal = MSTArticlePortal.objects.create(name="The Hindu")
    article = Article.objects.create(
        title="Story", source_url="https://example.com/c",
        hashed_key="hash2", content="body", portal=portal,
    )
    assert article.is_live is False
    assert article.live_poll_url is None
    assert article.created_at is not None
    assert article.updated_at is not None

def test_article_media_is_one_to_one_with_article():
    portal = MSTArticlePortal.objects.create(name="Portal Media Test")
    article = Article.objects.create(
        title="Story", source_url="https://example.com/media1",
        hashed_key="hash-media-1", content="body", portal=portal,
    )
    ArticleMedia.objects.create(article=article, url="https://s3/img.jpg", type="image")
    with pytest.raises(IntegrityError):
        ArticleMedia.objects.create(article=article, url="https://s3/other.jpg", type="image")

def test_article_tags_map_links_article_and_tag():
    portal = MSTArticlePortal.objects.create(name="Portal MSTTag Test")
    article = Article.objects.create(
        title="Story", source_url="https://example.com/tag1",
        hashed_key="hash-tag-1", content="body", portal=portal,
    )
    tag = MSTTag.objects.create(name="finance")
    mapping = ArticleTagMap.objects.create(article=article, tag=tag)
    assert mapping.article_id == article.id
    assert mapping.tag_id == tag.id
