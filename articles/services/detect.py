"""
Auto-detect parser_mode from a user-supplied URL.

Returns (parser_mode, effective_url) where effective_url may differ from the
input if RSS autodiscovery finds a feed link embedded in the HTML page.
"""
import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_RSS_URL_HINTS = ("/rss", "/feed", "/atom", ".xml", "rss.xml", "feed.xml", "atom.xml")
_RSS_CONTENT_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "text/xml",
    "application/xml",
)


def auto_detect_parser_mode(url: str) -> tuple[str, str]:
    """
    Probe *url* and return ``(parser_mode, effective_url)``.

    parser_mode is one of:
      "rss"             — RSS/Atom feed
      "html/multistage" — static HTML listing page
      "js/playwright"   — JavaScript-rendered page (Next.js / SPA)

    effective_url may differ from url when RSS autodiscovery upgrades a
    homepage URL to its embedded feed URL.
    """
    lower = url.lower()

    # Step 1 — URL pattern hints (cheap, no HTTP call)
    if any(hint in lower for hint in _RSS_URL_HINTS):
        try:
            r = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=15)
            ct = r.headers.get("content-type", "")
            if any(t in ct for t in _RSS_CONTENT_TYPES) or r.text.lstrip().startswith("<?xml"):
                logger.info("detect: RSS via URL pattern for %s", url)
                return "rss", str(r.url)
        except Exception:
            pass  # not a valid feed — fall through to HTML probe

    # Step 2 — HTTP GET the URL
    try:
        r = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        raise ValueError(f"Could not reach {url}: {exc}") from exc

    effective_url = str(r.url)
    ct = r.headers.get("content-type", "")

    # Step 3 — Content-Type declares XML/feed
    if any(t in ct for t in _RSS_CONTENT_TYPES):
        logger.info("detect: RSS via Content-Type for %s", effective_url)
        return "rss", effective_url

    # Step 4 — RSS autodiscovery in HTML <link> tags
    soup = BeautifulSoup(r.text, "html.parser")
    rss_link = soup.find(
        "link",
        rel=lambda r: isinstance(r, list) and "alternate" in r,
        type=lambda t: t and ("rss" in t.lower() or "atom" in t.lower() or "xml" in t.lower()),
    )
    if rss_link and rss_link.get("href"):
        feed_url = urljoin(effective_url, rss_link["href"])
        logger.info("detect: RSS autodiscovered %s → %s", effective_url, feed_url)
        return "rss", feed_url

    # Step 5 — JS rendering detection
    if soup.find("script", id="__NEXT_DATA__"):
        logger.info("detect: js/playwright (Next.js) for %s", effective_url)
        return "js/playwright", effective_url

    root_div = soup.find("div", id="root") or soup.find("div", id="app")
    if root_div:
        body_text = soup.body.get_text(strip=True) if soup.body else ""
        if len(body_text) < 2000:
            logger.info("detect: js/playwright (thin SPA shell) for %s", effective_url)
            return "js/playwright", effective_url

    # Default — static HTML listing page
    logger.info("detect: html/multistage for %s", effective_url)
    return "html/multistage", effective_url
