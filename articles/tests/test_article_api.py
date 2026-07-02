import pytest
from rest_framework.test import APIClient
from articles.models import MSTArticlePortal, MSTArticleCategory, Article

pytestmark = pytest.mark.django_db


def _make_article(portal, category=None, **kwargs):
    defaults = dict(title="Story", source_url=f"https://x.com/{kwargs.get('title','s')}",
                     hashed_key=kwargs.get("title", "s"), content="body", portal=portal, category=category)
    defaults.update(kwargs)
    return Article.objects.create(**defaults)


def test_list_articles_returns_paginated_results():
    portal = MSTArticlePortal.objects.create(name="API Portal")
    _make_article(portal, title="Story One", hashed_key="h1", source_url="https://x.com/1")
    _make_article(portal, title="Story Two", hashed_key="h2", source_url="https://x.com/2")

    response = APIClient().get("/api/v1/articles/")

    assert response.status_code == 200
    assert response.data["count"] == 2


def test_filter_articles_by_category_id():
    portal = MSTArticlePortal.objects.create(name="API Portal 2")
    sports = MSTArticleCategory.objects.create(name="Sports")
    markets = MSTArticleCategory.objects.create(name="Markets")
    _make_article(portal, category=sports, title="Sports story", hashed_key="h3", source_url="https://x.com/3")
    _make_article(portal, category=markets, title="Markets story", hashed_key="h4", source_url="https://x.com/4")

    response = APIClient().get(f"/api/v1/articles/?category_id={sports.id}")

    assert response.data["count"] == 1
    assert response.data["results"][0]["title"] == "Sports story"


def test_search_articles_by_title():
    portal = MSTArticlePortal.objects.create(name="API Portal 3")
    _make_article(portal, title="Fire breaks out in Delhi", hashed_key="h5", source_url="https://x.com/5")
    _make_article(portal, title="Markets close higher", hashed_key="h6", source_url="https://x.com/6")

    response = APIClient().get("/api/v1/articles/?search=fire")

    assert response.data["count"] == 1
    assert "Fire" in response.data["results"][0]["title"]


def test_article_detail_returns_404_for_unknown_id():
    response = APIClient().get("/api/v1/articles/00000000-0000-0000-0000-000000000000/")
    assert response.status_code == 404
