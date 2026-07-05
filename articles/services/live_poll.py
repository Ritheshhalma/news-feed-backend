"""
Live data polling for forex and stock articles.

Two concrete fetchers implement BaseLiveFetcher:

  ForexFetcher  — scrapes x-rates.com for exchange rates
  StockFetcher  — calls Yahoo Finance chart API for equity prices

fetch_live_value() is the public entry point; it selects the correct fetcher
by poll_type and returns a plain dict of live data.
"""
import logging
import re
from abc import ABC, abstractmethod

import httpx
from asgiref.sync import async_to_sync
from bs4 import BeautifulSoup
from channels.layers import get_channel_layer

from articles.models import ArticleRealTimeState

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; NewsFeedBot/1.0)"
_HTTP_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json, text/html"}


# ── Abstract base ─────────────────────────────────────────────────────────────

class BaseLiveFetcher(ABC):
    """Contract for live data sources (forex, stock, etc.)."""

    @abstractmethod
    def fetch(self, url: str) -> dict:
        """Fetch live data from *url* and return a plain dict of values."""
        ...


# ── Forex fetcher (x-rates.com) ───────────────────────────────────────────────

class ForexFetcher(BaseLiveFetcher):

    def fetch(self, url: str) -> dict:
        response = httpx.get(url, headers=_HTTP_HEADERS, timeout=10.0)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        rate_tag = soup.find(class_="ccOutputRslt")
        if not rate_tag:
            raise ValueError("rate element not found — source markup may have changed")

        raw = rate_tag.get_text(strip=True)
        m = re.search(r"[\d,]+\.?\d*", raw)
        if not m:
            raise ValueError("no numeric value found in rate element")

        rate = float(m.group(0).replace(",", ""))

        change_pct = None
        trend_tag = soup.find(class_="ccOutputTrend")
        if trend_tag:
            cm = re.search(r"[-+]?[\d.]+", trend_tag.get_text(strip=True))
            if cm:
                change_pct = float(cm.group(0))

        result = {"rate": rate}
        if change_pct is not None:
            result["change_pct"] = change_pct
        return result


# ── Stock fetcher (Yahoo Finance chart API) ───────────────────────────────────

_YF_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}


class StockFetcher(BaseLiveFetcher):
    """
    url should be the Yahoo Finance chart endpoint, e.g.:
      https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS
    """

    def fetch(self, url: str) -> dict:
        response = httpx.get(url, headers=_YF_HEADERS, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()

        try:
            result_block = payload["chart"]["result"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Unexpected Yahoo Finance response shape") from exc

        meta = result_block["meta"]
        price = float(meta["regularMarketPrice"])
        prev_close = float(meta.get("previousClose") or meta.get("chartPreviousClose") or price)
        change = round(price - prev_close, 4)
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        return {
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "symbol": meta.get("symbol", ""),
            "currency": meta.get("currency", ""),
            "exchange": meta.get("exchangeName", ""),
            "market_state": meta.get("marketState", ""),
        }


# ── Registry ──────────────────────────────────────────────────────────────────

_FETCHERS: dict[str, BaseLiveFetcher] = {
    "forex": ForexFetcher(),
    "stock": StockFetcher(),
}


def fetch_live_value(poll_type: str, url: str) -> dict:
    fetcher = _FETCHERS.get(poll_type)
    if not fetcher:
        raise ValueError(f"Unknown poll_type: {poll_type!r}. Valid types: {list(_FETCHERS)}")
    return fetcher.fetch(url)


# ── Push + poll ───────────────────────────────────────────────────────────────

def _push_live_update(article_id: str, data: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(f"live_article_{article_id}", {
        "type": "live.update",
        "data": data,
    })


def poll_one_live_article(article) -> None:
    poll_type = article.poll_type or "forex"
    try:
        new_data = fetch_live_value(poll_type, article.live_poll_url)
    except Exception:
        logger.exception("Live poll failed for article %s (type=%s)", article.id, poll_type)
        return

    state, _ = ArticleRealTimeState.objects.get_or_create(
        article=article, defaults={"current_data": {}}
    )
    if state.current_data == new_data:
        return

    state.current_data = new_data
    state.save(update_fields=["current_data", "last_updated_at"])
    _push_live_update(str(article.id), new_data)
