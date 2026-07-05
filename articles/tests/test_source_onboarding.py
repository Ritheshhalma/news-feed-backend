import pytest
from rest_framework.test import APIClient
from articles.models import ArticleSource, MSTArticlePortal

pytestmark = pytest.mark.django_db


def _post_source(mocker, parser_mode="rss", effective_url="https://x.com/feed.xml", validate_raises=None):
    """Helper: mock auto-detection + validate, POST to /api/v1/sources/."""
    mocker.patch(
        "articles.views.auto_detect_parser_mode",
        return_value=(parser_mode, effective_url),
    )
    if validate_raises:
        mocker.patch("articles.adapters.rss.RSSAdapter.validate", side_effect=validate_raises)
        mocker.patch("articles.adapters.html.HTMLAdapter.validate", side_effect=validate_raises)
    else:
        mocker.patch("articles.adapters.rss.RSSAdapter.validate", return_value=None)
        mocker.patch("articles.adapters.html.HTMLAdapter.validate", return_value=None)

    return APIClient().post("/api/v1/sources/", {
        "portal_name": "Test Portal", "url": effective_url,
    }, format="json")


def test_adding_a_valid_source_activates_it_after_validate(mocker):
    response = _post_source(mocker, parser_mode="rss")
    assert response.status_code == 201
    assert response.data["status"] == "active"
    assert response.data["parser_mode"] == "rss"
    assert MSTArticlePortal.objects.filter(name="Test Portal").exists()


def test_adding_an_unreachable_source_marks_it_failed_with_error(mocker):
    response = _post_source(mocker, validate_raises=ConnectionError("timeout"))
    assert response.status_code == 201
    assert response.data["status"] == "failed"
    assert "timeout" in response.data["error_message"]


def test_auto_detected_html_multistage_source_is_created_correctly(mocker):
    response = _post_source(
        mocker, parser_mode="html/multistage", effective_url="https://news.example.com/"
    )
    assert response.status_code == 201
    assert response.data["source_type"] == "html"
    assert response.data["parser_mode"] == "html/multistage"


def test_auto_detected_playwright_source_is_created_correctly(mocker):
    mocker.patch(
        "articles.views.auto_detect_parser_mode",
        return_value=("js/playwright", "https://spa.example.com/news"),
    )
    mocker.patch("articles.adapters.playwright_adapter.PlaywrightAdapter.validate", return_value=None)

    response = APIClient().post("/api/v1/sources/", {
        "portal_name": "SPA Portal", "url": "https://spa.example.com/news",
    }, format="json")
    assert response.status_code == 201
    assert response.data["parser_mode"] == "js/playwright"
    assert response.data["source_type"] == "html"


def test_adding_source_with_existing_portal_name_reuses_the_portal(mocker):
    MSTArticlePortal.objects.create(name="Existing Portal")
    mocker.patch("articles.views.auto_detect_parser_mode", return_value=("rss", "https://x.com/f.xml"))
    mocker.patch("articles.adapters.rss.RSSAdapter.validate", return_value=None)

    APIClient().post("/api/v1/sources/", {
        "portal_name": "Existing Portal", "url": "https://x.com/f.xml",
    }, format="json")
    assert MSTArticlePortal.objects.filter(name="Existing Portal").count() == 1


def test_missing_url_returns_400(mocker):
    response = APIClient().post("/api/v1/sources/", {"portal_name": "P"}, format="json")
    assert response.status_code == 400


def test_missing_portal_name_returns_400(mocker):
    response = APIClient().post("/api/v1/sources/", {"url": "https://x.com/f"}, format="json")
    assert response.status_code == 400
