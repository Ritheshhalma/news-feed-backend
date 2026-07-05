import pytest
from rest_framework.test import APIClient
from articles.models import MSTArticlePortal, ArticleSource, SourceFetchLog

pytestmark = pytest.mark.django_db


def test_refresh_endpoint_enqueues_scrape_source_and_returns_task_id(mocker):
    mock_delay = mocker.patch("articles.views.scrape_source.delay")
    mock_delay.return_value.id = "fake-task-id-123"
    portal = MSTArticlePortal.objects.create(name="Refresh Portal")
    source = ArticleSource.objects.create(
        url="https://x.com/feed", source_type="rss", parser_mode="rss", portal=portal,
    )

    response = APIClient().post(f"/api/v1/sources/{source.id}/refresh/")

    assert response.status_code == 202
    assert response.data["task_id"] == "fake-task-id-123"
    mock_delay.assert_called_once_with(str(source.id), trigger_type="on_demand", force=False)


def test_refresh_with_force_true_passes_force_flag(mocker):
    mock_delay = mocker.patch("articles.views.scrape_source.delay")
    mock_delay.return_value.id = "task-force-456"
    portal = MSTArticlePortal.objects.create(name="Force Portal")
    source = ArticleSource.objects.create(
        url="https://x.com/feed", source_type="rss", parser_mode="rss", portal=portal,
    )

    response = APIClient().post(
        f"/api/v1/sources/{source.id}/refresh/",
        {"force": True},
        format="json",
    )

    assert response.status_code == 202
    mock_delay.assert_called_once_with(str(source.id), trigger_type="on_demand", force=True)


def test_refresh_playwright_source_uses_playwright_task(mocker):
    mock_delay = mocker.patch("articles.views.scrape_playwright_source.delay")
    mock_delay.return_value.id = "pw-task-789"
    portal = MSTArticlePortal.objects.create(name="PW Portal")
    source = ArticleSource.objects.create(
        url="https://spa.com/news", source_type="html", parser_mode="js/playwright", portal=portal,
    )

    response = APIClient().post(f"/api/v1/sources/{source.id}/refresh/")

    assert response.status_code == 202
    assert response.data["task_id"] == "pw-task-789"
    mock_delay.assert_called_once_with(str(source.id), trigger_type="on_demand", force=False)


def test_jobs_endpoint_returns_attempt_history_for_task_id():
    portal = MSTArticlePortal.objects.create(name="Jobs Portal")
    source = ArticleSource.objects.create(url="https://x.com/feed2", source_type="rss", portal=portal)
    SourceFetchLog.objects.create(source=source, task_id="abc-123", trigger_type="on_demand", status="failed", attempt=0)
    SourceFetchLog.objects.create(source=source, task_id="abc-123", trigger_type="on_demand", status="success", attempt=1)

    response = APIClient().get("/api/v1/jobs/abc-123/")

    assert response.status_code == 200
    assert len(response.data) == 2
    assert response.data[0]["attempt"] == 1  # most recent first
