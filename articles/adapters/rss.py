import asyncio
import calendar
import logging
import re
from datetime import datetime, timezone as dt_tz

import feedparser
from bs4 import BeautifulSoup

from articles.adapters.base import BaseAdapter, RawArticle
from articles.adapters.html import (
    _tags_from_url, _dedup_tags,
    _extract_with_trafilatura, _fetch_article_pages, _get_known_urls,
    _MAX_STAGE2,
)

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
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


def _struct_time_to_iso(t) -> str | None:
    if not t:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(t), tz=dt_tz.utc).isoformat()
    except Exception:
        return None


def _extract_content(entry) -> str:
    for c in entry.get("content", []):
        val = _strip_html(c.get("value", ""))
        if len(val) > 100:
            return val
    return _strip_html(entry.get("summary", ""))


class RSSAdapter(BaseAdapter):

    def fetch(self, force: bool = False) -> list[RawArticle]:
        feed = feedparser.parse(self.source.url)
        results = []
        for entry in feed.entries:
            try:
                raw_tags = entry.get("tags", [])
                tag_terms = [t.get("term", "").strip() for t in raw_tags if t.get("term", "").strip()]

                url_category = _category_from_url(entry.get("link", ""))
                url_tags = _tags_from_url(entry.get("link", ""))
                if url_category:
                    category = url_category
                    tags = _dedup_tags(tag_terms + url_tags)
                else:
                    category = (
                        tag_terms[0] if tag_terms
                        else entry.get("category")
                    )
                    tags = _dedup_tags((tag_terms[1:] if len(tag_terms) > 1 else []) + url_tags)

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

        parser_mode = getattr(self.source, "parser_mode", "rss")
        if parser_mode == "rss/multistage" and results:
            candidates = results[:_MAX_STAGE2]
            if not force and self.source is not None and self.source.portal_id:
                known = _get_known_urls(self.source.portal, [a.source_url for a in candidates])
                to_enrich = [a for a in candidates if a.source_url not in known]
                logger.info(
                    "RSSAdapter Stage 2: %d new / %d total",
                    len(to_enrich), len(candidates),
                )
            else:
                to_enrich = candidates
                logger.info(
                    "RSSAdapter Stage 2: enriching all %d candidates (force=%s)",
                    len(candidates), force,
                )
            if to_enrich:
                enriched = asyncio.run(_fetch_article_pages(to_enrich))
                # Re-attach RSS-level fallbacks not in Stage 2 batch
                enriched_urls = {a.source_url for a in to_enrich}
                results = enriched + [a for a in results if a.source_url not in enriched_urls]

        return results

    def validate(self) -> None:
        feed = feedparser.parse(self.source.url)
        if feed.bozo and not feed.entries:
            raise ValueError(
                f"Invalid RSS feed at {self.source.url}: {feed.bozo_exception}"
            )
        if not feed.entries:
            raise ValueError(f"RSS feed at {self.source.url} returned no entries")

    @staticmethod
    def _extract_image(entry) -> str | None:
        for enc in entry.get("enclosures", []):
            if enc.get("type", "").startswith("image/"):
                return enc.get("href")
        media = entry.get("media_thumbnail") or entry.get("media_content", [])
        if media:
            return media[0].get("url")
        return None
