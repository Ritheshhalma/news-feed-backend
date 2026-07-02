import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from articles.models import MSTArticlePortal, Article
from articles.views import ArticleViewSet

pytestmark = pytest.mark.django_db


def test_article_list_is_served_from_cache_on_second_request(mocker):
    cache.clear()
    portal = MSTArticlePortal.objects.create(name="Cache Portal")
    Article.objects.create(title="Cached story", source_url="https://x.com/cache1",
                            hashed_key="cache1", content="body", portal=portal)
    client = APIClient()

    client.get("/api/v1/articles/")  # first request — populates cache
    mock_qs = mocker.patch.object(ArticleViewSet, "get_queryset")
    second = client.get("/api/v1/articles/")

    mock_qs.assert_not_called()  # second request never entered the view — served from cache
    assert second.data["count"] == 1
