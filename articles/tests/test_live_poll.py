import pytest
from articles.models import MSTArticlePortal, Article, ArticleRealTimeState
from articles.services.live_poll import poll_one_live_article, fetch_live_value

pytestmark = pytest.mark.django_db


def test_poll_one_live_article_updates_state_and_pushes_on_change(mocker):
    mock_push = mocker.patch("articles.services.live_poll._push_live_update")
    portal = MSTArticlePortal.objects.create(name="Live Poll Portal")
    article = Article.objects.create(title="USD/INR", source_url="https://x.com/live1",
                                      hashed_key="live1", content="c", portal=portal,
                                      is_live=True, live_poll_url="https://fx.example.com/usdinr")
    mocker.patch("articles.services.live_poll.fetch_live_value", return_value={"rate": 83.42})

    poll_one_live_article(article)

    state = ArticleRealTimeState.objects.get(article=article)
    assert state.current_data == {"rate": 83.42}
    mock_push.assert_called_once_with(str(article.id), {"rate": 83.42})


def test_poll_one_live_article_skips_push_when_value_unchanged(mocker):
    portal = MSTArticlePortal.objects.create(name="Live Poll Portal 2")
    article = Article.objects.create(title="EUR/INR", source_url="https://x.com/live2",
                                      hashed_key="live2", content="c", portal=portal,
                                      is_live=True, live_poll_url="https://fx.example.com/eurinr")
    ArticleRealTimeState.objects.create(article=article, current_data={"rate": 90.1})
    mocker.patch("articles.services.live_poll.fetch_live_value", return_value={"rate": 90.1})
    mock_push = mocker.patch("articles.services.live_poll._push_live_update")

    poll_one_live_article(article)

    mock_push.assert_not_called()


def test_fetch_live_value_parses_forex_rate_from_fixture_page(mocker):
    html = '<html><body><span class="ccOutputRslt">83.42INR</span></body></html>'
    mocker.patch("httpx.get", return_value=mocker.Mock(text=html, raise_for_status=lambda: None))
    result = fetch_live_value("forex", "https://www.x-rates.com/calculator/?from=USD&to=INR&amount=1")
    assert result == {"rate": 83.42}


def test_fetch_live_value_parses_stock_data_from_yahoo_response(mocker):
    payload = {
        "chart": {
            "result": [{
                "meta": {
                    "symbol": "RELIANCE.NS",
                    "regularMarketPrice": 2400.0,
                    "previousClose": 2350.0,
                    "currency": "INR",
                    "exchangeName": "NSI",
                    "marketState": "REGULAR",
                }
            }]
        }
    }
    mocker.patch(
        "httpx.get",
        return_value=mocker.Mock(json=lambda: payload, raise_for_status=lambda: None),
    )
    result = fetch_live_value("stock", "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS")
    assert result["symbol"] == "RELIANCE.NS"
    assert result["price"] == 2400.0
    assert result["change"] == pytest.approx(50.0)
    assert result["change_pct"] == pytest.approx(2.13)


def test_fetch_live_value_raises_on_unknown_poll_type():
    with pytest.raises(ValueError, match="Unknown poll_type"):
        fetch_live_value("crypto", "https://example.com")
