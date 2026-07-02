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

# Attributes tried in order when looking for an image URL on an <img> tag
_IMG_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-lazy", "src")

# URL path segments that signal a navigation/utility page rather than an article
_NAV_SEGMENTS = frozenset({
    "search", "tag", "tags", "author", "authors", "topic", "topics",
    "rss", "feed", "feeds", "newsletter", "subscribe", "login", "signup",
    "about", "contact", "privacy", "terms", "sitemap", "advertise",
    "podcast", "podcasts", "apps", "download",
})

# Regex patterns that indicate a URL is a nav/section page, not an article
_NAV_URL_RE = re.compile(
    r"(#|javascript:|mailto:|tel:|whatsapp:|/share\?|/share/|"
    r"\?utm_|&utm_|[?&]ref=|/video/playlist|/photo-gallery/$)",
    re.IGNORECASE,
)

_DATE_PREFIX_RE = re.compile(r"^[A-Z][a-z]{1,8}\.?\s+\d{1,2},?\s+\d{4}\s*")
_TIME_PREFIX_RE = re.compile(r"^\d{2}:\d{2}\s*")

# Date patterns in nearby text
_DATE_RE = re.compile(
    r"\b([A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+[A-Z][a-z]{2,8}\.?\s+\d{4})\b"
)

_MIN_TITLE_LEN = 20
_MAX_ARTICLES = 60


class HTMLAdapter(BaseAdapter):
    """
    Generic news-listing-page scraper.

    Works with any site that server-renders article links in HTML —
    TOI, NDTV, BBC, etc.  Two modes:

    Listing page  → finds every article <a> on the page and returns one
                    RawArticle per link (title, URL, thumbnail, category).
    Article page  → falls back to og:title / og:description (original
                    behaviour) when fewer than 3 candidate links are found.
    """

    def fetch(self) -> list[RawArticle]:
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

        if len(articles) >= 3:
            logger.info(
                "HTMLAdapter listing mode: %d articles from %s",
                len(articles), self.source.url,
            )
            return articles[:_MAX_ARTICLES]

        # Fallback: treat the page itself as a single article
        return self._extract_single_article(soup)

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

            href = urljoin(base_url, href)  # handles relative URLs

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
                tags=[],
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

        # Content — prefer visible article body over og:description
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
            tags=self._extract_page_tags(soup),
        )]

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _is_nav_url(self, url: str, source_host: str) -> bool:
        if _NAV_URL_RE.search(url):
            return True
        parsed = urlparse(url)
        # Allow same host or related subdomains (e.g. sports.ndtv.com)
        host = parsed.netloc
        root = lambda h: ".".join(h.split(".")[-2:])
        if root(host) != root(source_host):
            return True  # skip external domains
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if not path_parts:
            return True  # homepage
        # Path with only 1 segment is usually a section page, not an article
        if len(path_parts) < 2:
            return True
        if path_parts[0].lower() in _NAV_SEGMENTS:
            return True
        return False

    def _extract_title(self, a_tag) -> str:
        # 1. Prefer a heading element INSIDE the anchor (avoids body-text bleed-in)
        for tag_name in ("h1", "h2", "h3", "h4", "h5"):
            h = a_tag.find(tag_name)
            if h:
                t = h.get_text(strip=True)
                if len(t) >= _MIN_TITLE_LEN:
                    return self._clean_title(t)[:255]

        # 2. aria-label / title attribute
        for attr in ("aria-label", "title"):
            t = a_tag.get(attr, "").strip()
            if len(t) >= _MIN_TITLE_LEN:
                return self._clean_title(t)[:255]

        # 3. Direct anchor text — take only the first line to avoid bleed-in
        #    (some sites wrap the whole card in a single <a>)
        raw = a_tag.get_text(" ", strip=True)
        # Stop at a dateline pattern like "NEW DELHI:" or "MUMBAI:"
        raw = re.split(r"\s+[A-Z]{2,}[A-Z\s]+:", raw, maxsplit=1)[0].strip()
        # Alternatively stop at the first sentence boundary beyond 30 chars
        if len(raw) > 255:
            m = re.search(r"(?<=[.!?])\s", raw[30:])
            raw = (raw[: 30 + m.start()] if m else raw[:255]).strip()
        if len(raw) >= _MIN_TITLE_LEN:
            return self._clean_title(raw)[:255]

        # 4. Walk up parent containers for a heading
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
        # Strip "Section / " or "Section/" separators (e.g. "India / ", "World/")
        if "/" in title:
            parts = title.split("/", 1)
            # Only strip if the left side looks like a section label (no digits)
            if parts[0].strip().replace(" ", "").isalpha():
                title = parts[1].strip()
        # Strip date prefix: "Jul 1, 2026 " or "1 Jul 2026 "
        title = _DATE_PREFIX_RE.sub("", title).strip()
        # Strip time prefix: "04:01 "
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
            # Also check the anchor itself for a background-image style (common in some sites)
            node = node.parent
        return None

    def _is_real_image(self, url: str) -> bool:
        if not url.startswith("http"):
            return False
        low = url.lower()
        # Skip obvious placeholders
        if any(x in low for x in ("placeholder", "blank.gif", "1x1", "spacer",
                                   "imgsize-44444444", "noimage", "default")):
            return False
        # Skip data URIs or SVG icons
        if url.startswith("data:") or low.endswith(".svg"):
            return False
        return True

    def _extract_date(self, a_tag) -> str | None:
        node = a_tag
        for _ in range(4):
            if not node:
                break
            # Check <time> elements first
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
        """Best-effort content snippet from article card context on a listing page."""
        node = a_tag.parent
        for _ in range(4):
            if not node:
                break
            for el in node.find_all(["p", "div"], recursive=False):
                # Skip elements that are just navigation/meta UI
                cls = " ".join(el.get("class", []))
                if re.search(r"author|byline|date|time|tag|cat|social|share", cls, re.I):
                    continue
                text = el.get_text(strip=True)
                if len(text) > 40:
                    return text[:500]
            node = node.parent
        return ""

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        """Extract author name from a single article page."""
        # 1. Meta tags (most reliable)
        for prop in ("author", "article:author"):
            m = soup.find("meta", {"name": prop}) or soup.find("meta", {"property": prop})
            if m:
                v = m.get("content", "").strip()
                if v:
                    return v
        # 2. Linked author (rel="author")
        a = soup.find("a", rel=lambda r: isinstance(r, list) and "author" in r)
        if a:
            v = a.get_text(strip=True)
            if 2 < len(v) < 80:
                return v
        # 3. Byline / author class elements
        for cls_pat in (r"\bauthor\b", r"\bbyline\b", r"\bwriter\b", r"\bcontributor\b"):
            el = soup.find(class_=re.compile(cls_pat, re.I))
            if el:
                v = el.get_text(strip=True)
                if 2 < len(v) < 80:
                    return v
        return None

    def _extract_page_tags(self, soup: BeautifulSoup) -> list[str]:
        """Extract tags/keywords from a single article page's meta tags."""
        tags: list[str] = []
        # keywords meta
        kw = soup.find("meta", {"name": "keywords"})
        if kw and kw.get("content"):
            tags += [t.strip() for t in kw["content"].split(",") if t.strip()]
        # article:tag (multiple allowed by spec)
        for m in soup.find_all("meta", property="article:tag"):
            v = m.get("content", "").strip()
            if v:
                tags.append(v)
        # Deduplicate preserving order, cap at 10
        seen: set[str] = set()
        result: list[str] = []
        for t in tags:
            if t.lower() not in seen:
                seen.add(t.lower())
                result.append(t)
        return result[:10]

    def _category_from_url(self, url: str) -> str | None:
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        if not parts:
            return None
        slug = parts[0].lower()
        if slug in _NAV_SEGMENTS:
            return None
        # Map common news slugs to display names
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
