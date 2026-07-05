import asyncio
import copy
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from articles.adapters.base import BaseAdapter, RawArticle

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_IMG_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-lazy", "src")

_NAV_SEGMENTS = frozenset({
    "search", "tag", "tags", "author", "authors", "topic", "topics",
    "rss", "feed", "feeds", "newsletter", "subscribe", "login", "signup",
    "about", "contact", "privacy", "terms", "sitemap", "advertise",
    "podcast", "podcasts", "apps", "download",
    "reel", "reels", "video", "videos", "watch", "shorts",  # video/reel hubs are not article listings
})

_NAV_URL_RE = re.compile(
    r"(#|javascript:|mailto:|tel:|whatsapp:|/share\?|/share/|"
    r"\?utm_|&utm_|[?&]ref=|/video/playlist|/photo-gallery/$)",
    re.IGNORECASE,
)

_DATE_PREFIX_RE = re.compile(r"^[A-Z][a-z]{1,8}\.?\s+\d{1,2},?\s+\d{4}\s*")
_TIME_PREFIX_RE = re.compile(r"^\d{2}:\d{2}\s*")
_TIME_AGO_RE = re.compile(
    r"\b\d+\s+(?:hr|hrs|hour|hours|min|mins|minute|minutes|sec|second|day|days)\s+ago\b",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"\b([A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+[A-Z][a-z]{2,8}\.?\s+\d{4})\b"
)

# Matches individual article URLs (not listing/category pages).
# Signals: path segment "article(s)", date prefix (20220221-), or long hash slug.
_ARTICLE_URL_RE = re.compile(
    r"/articles?/"               # /article/ or /articles/
    r"|/\d{8}-[a-z]"            # /20220221-slug
    r"|/\d{4}/\d{2}/\d{2}/"     # /2022/02/21/
    r"|/p/[a-z0-9]{6,}$"        # Medium/Ghost /p/<hash>
    r"|/(?=[a-z0-9]*\d)[a-z0-9]{10,}$",  # hash slug with ≥1 digit — excludes pure-alpha category names
    re.IGNORECASE,
)


def _is_single_article_url(url: str) -> bool:
    """Return True when the URL points to one article rather than a listing page."""
    path = urlparse(url).path
    parts = [p for p in path.strip("/").split("/") if p]
    # Listing pages have ≤2 path segments (e.g. /news/, /world/, /travel/)
    if len(parts) <= 1:
        return False
    return bool(_ARTICLE_URL_RE.search(path))


_URL_TAG_MAP = {
    "india": "India", "world": "World", "us": "United States",
    "usa": "United States", "uk": "United Kingdom", "europe": "Europe",
    "asia": "Asia", "africa": "Africa",
    "business": "Business", "sports": "Sports", "sport": "Sports",
    "tech": "Technology", "technology": "Technology",
    "entertainment": "Entertainment", "health": "Health",
    "science": "Science", "travel": "Travel", "politics": "Politics",
    "international": "International", "environment": "Environment",
    "education": "Education", "lifestyle": "Lifestyle",
    "opinion": "Opinion", "auto": "Auto", "cities": "Cities",
    "city": "Cities", "national": "India", "money": "Business",
    "economy": "Business",
}


def _tags_from_url(url: str) -> list[str]:
    """Derive topic tags from meaningful URL path segments."""
    parts = [p.lower() for p in urlparse(url).path.strip("/").split("/") if p]
    return [_URL_TAG_MAP[p] for p in parts[:4] if p in _URL_TAG_MAP]


def _parse_tags(raw) -> list[str]:
    """Split comma-separated tag strings (trafilatura returns them that way)."""
    if not raw:
        return []
    result = []
    for item in (raw if isinstance(raw, list) else [raw]):
        for part in str(item).split(","):
            part = part.strip()
            if part and len(part) < 60:
                result.append(part)
    return result


def _dedup_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        lk = t.lower()
        if lk not in seen:
            seen.add(lk)
            result.append(t)
    return result

_MIN_TITLE_LEN = 20
_MAX_ARTICLES = 60
_MAX_STAGE2 = 30   # cap concurrent article-page fetches per run
_STAGE2_CONCURRENCY = 5


def _get_known_urls(portal, urls: list[str]) -> set[str]:
    from articles.models import Article  # lazy — avoids circular import at module load
    return set(
        Article.objects.filter(portal=portal, source_url__in=urls)
        .values_list("source_url", flat=True)
    )


def _extract_with_trafilatura(html: str, fallback: RawArticle) -> RawArticle:
    """Run trafilatura on rendered HTML and merge result with listing-page fallback."""
    try:
        from trafilatura import bare_extraction, extract_metadata
    except ImportError:
        logger.warning("trafilatura not installed; returning card-level data only")
        return fallback

    result = bare_extraction(html, include_tables=False, favor_precision=True)
    # extract_metadata reads <meta name="keywords"> and JSON-LD — bare_extraction returns tags=None
    meta = extract_metadata(html)

    if not result and not meta:
        return fallback

    # trafilatura ≥2.0 returns a Document object; ≤1.x returned a dict
    r = result.as_dict() if result and hasattr(result, "as_dict") else (result or {})
    m = meta.as_dict() if meta and hasattr(meta, "as_dict") else (meta if isinstance(meta, dict) else {})

    # Merge: keywords meta tags + JSON-LD tags + URL path segment tags
    tags = _dedup_tags(
        _parse_tags(r.get("tags")) + _parse_tags(m.get("tags")) + _tags_from_url(fallback.source_url)
    )

    return RawArticle(
        title=r.get("title") or m.get("title") or fallback.title,
        source_url=fallback.source_url,
        # extract_metadata stores description in "description"; bare_extraction uses "text"
        content=r.get("text") or m.get("text") or m.get("description") or fallback.content,
        image_url=r.get("image") or m.get("image") or fallback.image_url,
        published_at=r.get("date") or m.get("date") or fallback.published_at,
        category_name=fallback.category_name,   # URL-derived category is more reliable
        author_name=r.get("author") or m.get("author") or fallback.author_name,
        tags=tags or fallback.tags,
    )


async def _fetch_article_pages(articles: list[RawArticle]) -> list[RawArticle]:
    """Stage 2: async HTTP GET each article page, run trafilatura, return enriched list."""
    sem = asyncio.Semaphore(_STAGE2_CONCURRENCY)

    async def fetch_one(article: RawArticle) -> RawArticle:
        async with sem:
            try:
                async with httpx.AsyncClient(
                    headers={"User-Agent": _USER_AGENT},
                    follow_redirects=True,
                    timeout=15.0,
                ) as client:
                    r = await client.get(article.source_url)
                    r.raise_for_status()
                    return _extract_with_trafilatura(r.text, article)
            except Exception as exc:
                logger.warning("Stage 2 failed for %s: %s", article.source_url, exc)
                return article  # keep card-level data as fallback

    results = await asyncio.gather(*[fetch_one(a) for a in articles])
    return list(results)


class HTMLAdapter(BaseAdapter):
    """
    Two-mode HTML scraper for static news listing pages.

    parser_mode="html"           → listing mode only (title, URL, thumbnail, snippet)
    parser_mode="html/multistage"→ listing + Stage 2 trafilatura (full body, author, tags)

    force=False (default): skips article URLs already stored in the database (Stage 2 only)
    force=True:            re-fetches and re-extracts ALL discovered article URLs
    """

    def fetch(self, force: bool = False) -> list[RawArticle]:
        response = httpx.get(
            self.source.url,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=15.0,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        base_url = str(response.url)

        # Source URL is a single article (e.g. /article/slug, /2022/02/21/title) — skip listing
        if _is_single_article_url(self.source.url):
            return self._extract_single_article(soup)

        card_articles = self._extract_from_listing(soup, base_url)

        if len(card_articles) < 3:
            # Listing returned almost nothing — treat as single-article page
            return self._extract_single_article(soup)

        logger.info("HTMLAdapter listing: %d articles from %s", len(card_articles), self.source.url)

        parser_mode = getattr(self.source, "parser_mode", "html")
        if parser_mode != "html/multistage":
            return card_articles[:_MAX_ARTICLES]

        # Stage 2: enrich only new articles (or all if force=True)
        candidates = card_articles[:_MAX_ARTICLES]
        if not force and self.source is not None and self.source.portal_id:
            known = _get_known_urls(self.source.portal, [a.source_url for a in candidates])
            to_enrich = [a for a in candidates if a.source_url not in known]
            logger.info(
                "HTMLAdapter Stage 2: %d new / %d total (force=%s)",
                len(to_enrich), len(candidates), force,
            )
        else:
            to_enrich = candidates

        if not to_enrich:
            return []

        return asyncio.run(_fetch_article_pages(to_enrich[:_MAX_STAGE2]))

    def validate(self) -> None:
        response = httpx.get(
            self.source.url,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=15.0,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        base_url = str(response.url)

        articles = self._extract_from_listing(soup, base_url)
        if len(articles) < 1 and not self._og(soup, "og:title"):
            raise ValueError(
                f"No articles or og:title found at {self.source.url}; "
                "page may require JavaScript rendering (use js/playwright)"
            )

    # ------------------------------------------------------------------ #
    # Listing mode                                                         #
    # ------------------------------------------------------------------ #

    def _extract_from_listing(self, soup: BeautifulSoup, base_url: str) -> list[RawArticle]:
        source_host = urlparse(base_url).netloc
        seen_urls: set[str] = set()
        results: list[RawArticle] = []

        for a_tag in soup.find_all("a", href=True):
            href = (a_tag["href"] or "").strip()
            if not href:
                continue

            href = urljoin(base_url, href)

            if not href.startswith(("http://", "https://")):
                continue
            if href in seen_urls:
                continue
            if self._is_nav_url(href, source_host):
                continue

            title = self._extract_title(a_tag)
            if not title or len(title) < _MIN_TITLE_LEN:
                continue

            seen_urls.add(href)

            results.append(RawArticle(
                title=title,
                source_url=href,
                content=self._extract_snippet(a_tag),
                image_url=self._extract_image(a_tag),
                published_at=self._extract_date(a_tag),
                category_name=self._category_from_url(href),
                author_name=None,
                tags=_tags_from_url(href),
            ))

        return results

    # ------------------------------------------------------------------ #
    # Single-article fallback                                              #
    # ------------------------------------------------------------------ #

    def _extract_single_article(self, soup: BeautifulSoup) -> list[RawArticle]:
        title = self._og(soup, "og:title")
        if not title:
            logger.warning("Page at %s lacks og:title; skipping", self.source.url)
            return []

        body_tag = soup.find(class_=re.compile(r"article.?body|story.?body|post.?body|entry.?content", re.I))
        if not body_tag:
            body_tag = soup.find("article")
        content = (
            body_tag.get_text(separator=" ", strip=True)
            if body_tag
            else self._og(soup, "og:description")
        )

        return [RawArticle(
            title=title,
            source_url=self.source.url,
            content=content,
            image_url=self._og(soup, "og:image") or None,
            published_at=(
                self._og(soup, "article:published_time")
                or self._og(soup, "og:article:published_time")
                or None
            ),
            category_name=self._og(soup, "article:section") or None,
            author_name=self._extract_author(soup),
            tags=_dedup_tags(self._extract_page_tags(soup) + _tags_from_url(self.source.url)),
        )]

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _is_nav_url(self, url: str, source_host: str) -> bool:
        if _NAV_URL_RE.search(url):
            return True
        parsed = urlparse(url)
        host = parsed.netloc
        root = lambda h: ".".join(h.split(".")[-2:])
        if root(host) != root(source_host):
            return True
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if not path_parts:
            return True
        if len(path_parts) < 2:
            return True
        if path_parts[0].lower() in _NAV_SEGMENTS:
            return True
        return False

    def _extract_title(self, a_tag) -> str:
        for tag_name in ("h1", "h2", "h3", "h4", "h5"):
            h = a_tag.find(tag_name)
            if h:
                t = h.get_text(strip=True)
                if len(t) >= _MIN_TITLE_LEN:
                    return self._clean_title(t)[:255]

        for attr in ("aria-label", "title"):
            t = a_tag.get(attr, "").strip()
            if len(t) >= _MIN_TITLE_LEN:
                return self._clean_title(t)[:255]

        # Headline-only text: drop <p> descendants first, since those are the
        # snippet (handled by _extract_snippet) — sites that skip h1-h5 tags
        # for headlines otherwise get the deck/snippet text glued onto the title.
        clone = copy.deepcopy(a_tag)
        for p in clone.find_all("p"):
            p.decompose()
        raw = clone.get_text(" ", strip=True)
        raw = re.split(r"\s+[A-Z]{2,}[A-Z\s]+:", raw, maxsplit=1)[0].strip()
        if len(raw) > 255:
            m = re.search(r"(?<=[.!?])\s", raw[30:])
            raw = (raw[: 30 + m.start()] if m else raw[:255]).strip()
        if len(raw) >= _MIN_TITLE_LEN:
            return self._clean_title(raw)[:255]

        node = a_tag.parent
        for _ in range(4):
            if not node:
                break
            for tag_name in ("h1", "h2", "h3", "h4", "h5"):
                h = node.find(tag_name)
                if h:
                    t = h.get_text(strip=True)
                    if len(t) >= _MIN_TITLE_LEN:
                        return self._clean_title(t)[:255]
            node = node.parent

        return self._clean_title(raw)[:255]

    @staticmethod
    def _clean_title(title: str) -> str:
        if "/" in title:
            parts = title.split("/", 1)
            if parts[0].strip().replace(" ", "").isalpha():
                title = parts[1].strip()
        title = _DATE_PREFIX_RE.sub("", title).strip()
        title = _TIME_PREFIX_RE.sub("", title).strip()
        return title

    def _extract_image(self, a_tag) -> str | None:
        node = a_tag
        for _ in range(6):
            if not node:
                break
            for img in node.find_all("img", recursive=True if node is not a_tag else False):
                for attr in _IMG_SRC_ATTRS:
                    url = img.get(attr, "").strip()
                    if url and self._is_real_image(url):
                        return url if url.startswith("http") else None
            node = node.parent
        return None

    def _is_real_image(self, url: str) -> bool:
        if not url.startswith("http"):
            return False
        low = url.lower()
        if any(x in low for x in ("placeholder", "blank.gif", "1x1", "spacer",
                                   "imgsize-44444444", "noimage", "default")):
            return False
        if url.startswith("data:") or low.endswith(".svg"):
            return False
        return True

    def _extract_date(self, a_tag) -> str | None:
        node = a_tag
        for _ in range(4):
            if not node:
                break
            time_el = node.find("time")
            if time_el:
                return time_el.get("datetime") or time_el.get_text(strip=True) or None
            text = " ".join(node.stripped_strings)
            m = _DATE_RE.search(text)
            if m:
                return m.group(1)
            node = node.parent
        return None

    def _extract_snippet(self, a_tag) -> str:
        title_text = self._extract_title(a_tag)

        # Priority 1: <p> tags inside the card <a> — skip those that mirror the title,
        # are purely a timestamp, or match known noise class names.
        for p in a_tag.find_all("p", limit=10):
            cls = " ".join(p.get("class", []))
            if re.search(r"author|byline|time|date|tag|cat|social|share", cls, re.I):
                continue
            text = p.get_text(separator=" ", strip=True)
            if len(text) < 30 or text == title_text:
                continue
            text = _TIME_AGO_RE.sub("", text).strip()
            if len(text) > 30:
                return text[:500]

        # Priority 2: walk parent looking for description-like class names.
        node = a_tag.parent
        for _ in range(3):
            if not node:
                break
            for el in node.find_all(
                class_=re.compile(r"desc|summar|snippet|teaser|excerpt|lead|intro|blurb", re.I),
                limit=3,
            ):
                text = el.get_text(separator=" ", strip=True)
                if len(text) > 40:
                    return _TIME_AGO_RE.sub("", text).strip()[:500]
            node = node.parent

        return ""

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        for prop in ("author", "article:author"):
            m = soup.find("meta", {"name": prop}) or soup.find("meta", {"property": prop})
            if m:
                v = m.get("content", "").strip()
                if v:
                    return v
        a = soup.find("a", rel=lambda r: isinstance(r, list) and "author" in r)
        if a:
            v = a.get_text(strip=True)
            if 2 < len(v) < 80:
                return v
        for cls_pat in (r"\bauthor\b", r"\bbyline\b", r"\bwriter\b", r"\bcontributor\b"):
            el = soup.find(class_=re.compile(cls_pat, re.I))
            if el:
                v = el.get_text(strip=True)
                if 2 < len(v) < 80:
                    return v
        return None

    def _extract_page_tags(self, soup: BeautifulSoup) -> list[str]:
        tags: list[str] = []
        kw = soup.find("meta", {"name": "keywords"})
        if kw and kw.get("content"):
            tags += [t.strip() for t in kw["content"].split(",") if t.strip()]
        for m in soup.find_all("meta", property="article:tag"):
            v = m.get("content", "").strip()
            if v:
                tags.append(v)
        return _dedup_tags(tags)[:10]

    def _category_from_url(self, url: str) -> str | None:
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        if not parts:
            return None
        slug = parts[0].lower()
        if slug in _NAV_SEGMENTS:
            return None
        _MAP = {
            "india": "India", "world": "World", "business": "Business",
            "sports": "Sports", "tech": "Technology", "technology": "Technology",
            "entertainment": "Entertainment", "health": "Health",
            "science": "Science", "education": "Education",
            "opinion": "Opinion", "city": "Cities", "cities": "Cities",
            "lifestyle": "Lifestyle", "auto": "Auto", "travel": "Travel",
            "environment": "Environment", "politics": "Politics",
            "international": "World", "national": "India",
        }
        return _MAP.get(slug, slug.replace("-", " ").title())

    @staticmethod
    def _og(soup: BeautifulSoup, prop: str) -> str:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return (tag.get("content", "") if tag else "").strip()
