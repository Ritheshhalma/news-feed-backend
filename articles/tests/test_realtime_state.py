import pytest
from articles.models import MSTArticlePortal, Article, ArticleRealTimeState

pytestmark = pytest.mark.django_db


def test_realtime_state_one_to_one_with_article():
    portal = MSTArticlePortal.objects.create(name="Live Portal")
    article = Article.objects.create(
        title="USD/INR Live Rate",
        source_url="https://example.com/live1",
        hashed_key="hash-live-1",
        content="live tracker",
        portal=portal,
        is_live=True,
    )
    state = ArticleRealTimeState.objects.create(
        article=article,
        current_data={"rate": 83.42, "change_pct": 0.12},
    )
    assert state.current_data["rate"] == 83.42
    assert article.realtimestate.id == state.id
