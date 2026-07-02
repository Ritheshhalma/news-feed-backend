import pytest
from rest_framework.test import APIClient
from articles.models import ArticleSource, MSTArticlePortal
from articles.adapters.base import RawArticle

pytestmark = pytest.mark.django_db


def test_adding_a_valid_source_activates_it_after_trial_fetch(mocker):
    mocker.patch("articles.adapters.rss.RSSAdapter.fetch", return_value=[
        RawArticle(title="T", source_url="https://x.com/1", content="c", image_url=None, published_at=None, category_name=None)
    ])
    response = APIClient().post("/api/v1/sources/", {
        "portal_name": "New Portal", "url": "https://x.com/feed.xml", "source_type": "rss",
    }, format="json")

    assert response.status_code == 201
    assert response.data["status"] == "active"
    assert MSTArticlePortal.objects.filter(name="New Portal").exists()


def test_adding_an_unreachable_source_marks_it_failed_with_error(mocker):
    mocker.patch("articles.adapters.rss.RSSAdapter.fetch", side_effect=ConnectionError("timeout"))
    response = APIClient().post("/api/v1/sources/", {
        "portal_name": "Broken Portal", "url": "https://broken.com/feed.xml", "source_type": "rss",
    }, format="json")

    assert response.status_code == 201
    assert response.data["status"] == "failed"
    assert "timeout" in response.data["error_message"]


def test_adding_source_with_existing_portal_name_reuses_the_portal(mocker):
    MSTArticlePortal.objects.create(name="Existing Portal")
    mocker.patch("articles.adapters.rss.RSSAdapter.fetch", return_value=[])
    APIClient().post("/api/v1/sources/", {
        "portal_name": "Existing Portal", "url": "https://x.com/feed2.xml", "source_type": "rss",
    }, format="json")
    assert MSTArticlePortal.objects.filter(name="Existing Portal").count() == 1
