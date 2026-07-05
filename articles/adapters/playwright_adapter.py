"""
PlaywrightAdapter — Camoufox-backed scraper for JavaScript-rendered sites.

Extends HTMLAdapter so all the BeautifulSoup listing/single-article parsing
is inherited. Only the HTTP fetch layer is replaced with Camoufox rendering.

Two stages (same logic as HTMLAdapter multi-stage):
  1. Render the listing page with Camoufox → extract card articles via BS4.
  2. Render each new article page with Camoufox → trafilatura extraction.

validate() uses plain httpx (no browser) — enough to confirm the URL is
reachable; full JS rendering is reserved for the actual scrape.
"""
import asyncio
import logging

import httpx
from asgiref.sync import sync_to_async
from bs4 import BeautifulSoup

from articles.adapters.base import RawArticle
from articles.adapters.html import (
    HTMLAdapter,
    _USER_AGENT,
    _MAX_STAGE2,
    _get_known_urls,
    _extract_with_trafilatura,
    _is_single_article_url,
)

logger = logging.getLogger(__name__)

_BROWSER_CONCURRENCY = 3   # lower than httpx concurrency — browsers are heavier
_PLAYWRIGHT_MAX_STAGE2 = 20  # cap per run; worst case 7 rounds × 15s = ~105s Stage 2


class PlaywrightAdapter(HTMLAdapter):
    """
    JavaScript-aware adapter using Camoufox (a hardened Firefox fork).

    Inherits all BS4 parsing helpers from HTMLAdapter; only overrides
    fetch() and validate() to use browser rendering.
    """

    def fetch(self, force: bool = False) -> list[RawArticle]:
        return asyncio.run(self._async_fetch(force))

    def validate(self) -> None:
        r = httpx.get(
            self.source.url,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=15.0,
        )
        r.raise_for_status()

    # ------------------------------------------------------------------ #
    # Async implementation                                                 #
    # ------------------------------------------------------------------ #

    async def _async_fetch(self, force: bool) -> list[RawArticle]:
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as exc:
            raise RuntimeError(
                "camoufox is not installed. "
                "Add camoufox to requirements.txt and rebuild the image."
            ) from exc

        collected: list[RawArticle] = []
        try:
            async with AsyncCamoufox(headless=True) as browser:
                listing_html = await self._render(browser, self.source.url)
                soup = BeautifulSoup(listing_html, "html.parser")

                # Single article URL → extract directly, no listing walk
                if _is_single_article_url(self.source.url):
                    base = self._extract_single_article(soup)
                    if base:
                        collected = [_extract_with_trafilatura(listing_html, base[0])]
                    else:
                        collected = []
                    return collected

                card_articles = self._extract_from_listing(soup, self.source.url)

                if len(card_articles) < 3:
                    # Listing returned almost nothing — treat as single-article page
                    base = self._extract_single_article(soup)
                    if base:
                        collected = [_extract_with_trafilatura(listing_html, base[0])]
                    else:
                        collected = []
                    return collected

                logger.info(
                    "PlaywrightAdapter listing: %d articles from %s",
                    len(card_articles), self.source.url,
                )

                candidates = card_articles[:_PLAYWRIGHT_MAX_STAGE2]
                if not force and self.source is not None and self.source.portal_id:
                    # _get_known_urls is a sync ORM call; sync_to_async runs it in a
                    # thread pool so it's safe to await inside an async context.
                    known = await sync_to_async(_get_known_urls)(
                        self.source.portal, [a.source_url for a in candidates]
                    )
                    to_enrich = [a for a in candidates if a.source_url not in known]
                    logger.info(
                        "PlaywrightAdapter Stage 2: %d new / %d total",
                        len(to_enrich), len(candidates),
                    )
                else:
                    to_enrich = candidates

                if not to_enrich:
                    return []

                sem = asyncio.Semaphore(_BROWSER_CONCURRENCY)

                async def enrich_one(article: RawArticle) -> RawArticle:
                    async with sem:
                        try:
                            html = await self._render(browser, article.source_url)
                            return _extract_with_trafilatura(html, article)
                        except Exception as exc:
                            logger.warning(
                                "PlaywrightAdapter Stage 2 failed for %s: %s",
                                article.source_url, exc,
                            )
                            return article

                collected = list(await asyncio.gather(*[enrich_one(a) for a in to_enrich]))
        except Exception as exc:
            # "Connection closed while reading from the driver" means Firefox crashed,
            # usually due to insufficient /dev/shm in Docker (add shm_size: '1gb').
            # Return whatever was collected before the crash rather than losing everything.
            if "Connection closed" in str(exc) or "Target closed" in str(exc):
                logger.warning(
                    "PlaywrightAdapter: browser closed unexpectedly (shm issue?): %s — "
                    "returning %d articles collected before crash",
                    exc, len(collected),
                )
            else:
                raise

        return collected

    @staticmethod
    async def _render(browser, url: str) -> str:
        """Open a new tab, navigate to url, wait for DOM load, return HTML."""
        # no_viewport=True skips Browser.setDefaultViewport — Camoufox's protocol
        # doesn't include the isMobile field that Playwright sends by default.
        page = await browser.new_page(no_viewport=True)
        try:
            # "load" fires when all resources are fetched; "networkidle" waits for
            # zero network activity which never happens on SPAs like BBC Next.js.
            await page.goto(url, wait_until="load", timeout=15_000)
            return await page.content()
        finally:
            await page.close()
