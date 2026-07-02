import pytest
from rest_framework.test import APIClient
from articles.models import MSTArticleCategory, MSTTag, MSTArticlePortal, ArticleSource

pytestmark = pytest.mark.django_db


def test_categories_endpoint_lists_all_categories():
    MSTArticleCategory.objects.create(name="Sports")
    MSTArticleCategory.objects.create(name="Markets")
    response = APIClient().get("/api/v1/categories/")
    assert response.status_code == 200
    assert response.data["count"] == 2


def test_tags_endpoint_lists_all_tags():
    MSTTag.objects.create(name="cricket")
    response = APIClient().get("/api/v1/tags/")
    assert response.data["count"] == 1


def test_sources_endpoint_lists_sources_with_status():
    portal = MSTArticlePortal.objects.create(name="Lookup Portal")
    ArticleSource.objects.create(url="https://x.com/feed", source_type="rss", portal=portal)
    response = APIClient().get("/api/v1/sources/")
    assert response.data["results"][0]["status"] == "pending_validation"
