import calendar
import logging
import re
from datetime import datetime, timezone as dt_tz

import feedparser
from bs4 import BeautifulSoup

from articles.adapters.base import BaseAdapter, RawArticle

logger = logging.getLogger(__name__)

_URL_CATEGORY_RE = re.compile(
    r"ndtv\.com/"
    r"(india-news|world-news|business-news|sports|tech|technology|"
    r"entertainment|health|science|education|opinion|telangana-news|"
    r"artificial-intelligence|offbeat|cities|auto|food|travel|lifestyle)"
    r"/",
    re.IGNORECASE,
)

_SLUG_TO_LABEL = {
    "india-news": "India", "world-news": "World", "business-news": "Business",
    "sports": "Sports", "tech": "Technology", "technology": "Technology",
    "entertainment": "Entertainment", "health": "Health", "science": "Science",
    "education": "Education", "opinion": "Opinion", "telangana-news": "India",
    "artificial-intelligence": "Technology", "offbeat": "Offbeat",
    "cities": "India", "auto": "Auto", "food": "Lifestyle",
    "travel": "Lifestyle", "lifestyle": "Lifestyle",
}


def _category_from_url(url: str) -> str | None:
    m = _URL_CATEGORY_RE.search(url or "")
    if m:
        return _SLUG_TO_LABEL.get(m.group(1).lower())
    return None


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


def _struct_time_to_iso(t) -> str | None:
    """Convert feedparser's published_parsed (UTC struct_time) to ISO 8601 string."""
    if not t:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(t), tz=dt_tz.utc).isoformat()
    except Exception:
        return None


def _extract_content(entry) -> str:
    """Prefer full article body (entry.content); fall back to summary excerpt."""
    for c in entry.get("content", []):
        val = _strip_html(c.get("value", ""))
        if len(val) > 100:          # ignore tiny/empty content objects
            return val
    return _strip_html(entry.get("summary", ""))


class RSSAdapter(BaseAdapter):
    def fetch(self) -> list[RawArticle]:
        feed = feedparser.parse(self.source.url)
        results = []
        for entry in feed.entries:
            try:
                raw_tags = entry.get("tags", [])
                tag_terms = [t.get("term", "").strip() for t in raw_tags if t.get("term", "").strip()]

                # Prefer structured URL-based category; it's more reliable than feed tags
                url_category = _category_from_url(entry.get("link", ""))
                if url_category:
                    category = url_category
                    tags = tag_terms          # ALL tag terms become tags
                else:
                    # No URL category — use first tag term as category, rest as tags
                    category = (
                        tag_terms[0] if tag_terms
                        else entry.get("category")
                    )
                    tags = tag_terms[1:] if len(tag_terms) > 1 else []

                # Author: try multiple feed fields
                author = (
                    entry.get("author")
                    or entry.get("dc_creator")
                    or (entry.get("authors") or [{}])[0].get("name")
                )

                results.append(RawArticle(
                    title=entry.title,
                    source_url=entry.link,
                    content=_extract_content(entry),
                    image_url=self._extract_image(entry),
                    published_at=_struct_time_to_iso(entry.get("published_parsed"))
                               or entry.get("published"),
                    category_name=category,
                    author_name=author or None,
                    tags=tags,
                ))
            except (AttributeError, KeyError) as exc:
                logger.warning("Skipping malformed RSS entry from %s: %s", self.source.url, exc)
                continue

        return results

    @staticmethod
    def _extract_image(entry) -> str | None:
        for enc in entry.get("enclosures", []):
            if enc.get("type", "").startswith("image/"):
                return enc.get("href")
        media = entry.get("media_thumbnail") or entry.get("media_content", [])
        if media:
            return media[0].get("url")
        return None
