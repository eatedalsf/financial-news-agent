"""Arabic financial-news scrapers: Argaam, Mubasher, Al-Eqtisadiah.

These sites do not expose RSS, so we scrape the listing page HTML. The CSS
selectors below are best-effort defaults; financial-news sites redesign often
and the selectors WILL drift. If a fetcher starts returning 0 items, open the
listing URL in a browser, inspect the markup, and update the selectors here.
"""

import hashlib
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from src.fetchers.base import BaseFetcher
from src.models import NewsItem
from src.utils.logger import logger

# Some Arabic sites block default httpx user-agents.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class _ScrapingFetcher(BaseFetcher):
    """Shared HTTP + parse pipeline. Subclasses configure URL and selectors."""

    listing_url: str = ""
    language: str = "ar"
    category: Optional[str] = "saudi_market"
    max_items: int = 30

    # Subclasses override these CSS selectors.
    article_selector: str = ""
    title_selector: str = ""
    link_selector: str = ""
    summary_selector: str = ""

    async def fetch(self) -> List[NewsItem]:
        if not self.listing_url:
            return []

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            },
        ) as client:
            resp = await client.get(self.listing_url)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "lxml")
        articles = soup.select(self.article_selector)
        if not articles:
            logger.warning(
                f"{self.source_name}: 0 articles matched selector "
                f"'{self.article_selector}' - markup may have changed"
            )
            return []

        now = datetime.now(timezone.utc)
        seen: set[str] = set()
        items: List[NewsItem] = []
        for art in articles[: self.max_items]:
            parsed = self._parse_article(art, now)
            if parsed is None:
                continue
            if parsed.url in seen:
                continue
            seen.add(parsed.url)
            items.append(parsed)
        return items

    def _parse_article(self, art: Tag, now: datetime) -> Optional[NewsItem]:
        """Extract one article. Returns None if required fields are missing."""
        title_el = art.select_one(self.title_selector) if self.title_selector else art
        link_el = art.select_one(self.link_selector) if self.link_selector else title_el
        summary_el = (
            art.select_one(self.summary_selector) if self.summary_selector else None
        )

        title = title_el.get_text(strip=True) if title_el else ""
        href = link_el.get("href") if link_el else None
        if not title or not href:
            return None

        url = urljoin(self.listing_url, str(href))
        # Listing pages rarely carry per-item timestamps that we can rely on;
        # use current run time so cutoff/dedup logic keeps fresh items.
        # Phase 3+ can add per-article date scraping if needed.
        return NewsItem(
            id=hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
            title=title,
            url=url,
            source=self.source_name,
            published_at=now,
            summary=summary_el.get_text(strip=True) if summary_el else None,
            language=self.language,
            category=self.category,
        )


class ArgaamFetcher(_ScrapingFetcher):
    """Argaam — Saudi/Gulf markets coverage in Arabic."""

    source_name = "argaam"
    listing_url = "https://www.argaam.com/ar"
    article_selector = "article, .article-item, .news-item, .list-item"
    title_selector = "h2 a, h3 a, .title a, a.title"
    link_selector = "h2 a, h3 a, .title a, a.title"
    summary_selector = ".summary, .description, p"


class MubasherFetcher(_ScrapingFetcher):
    """Mubasher — pan-Arab markets news."""

    source_name = "mubasher"
    listing_url = "https://www.mubasher.info/news/markets"
    article_selector = "article, .news-item, li.list-item, .news-card"
    title_selector = "h2 a, h3 a, .title a, a.title"
    link_selector = "h2 a, h3 a, .title a, a.title"
    summary_selector = ".excerpt, .summary, p"


class AlEqtisadiahFetcher(_ScrapingFetcher):
    """Al-Eqtisadiah — Saudi business daily."""

    source_name = "aleqtisadiah"
    listing_url = "https://www.aleqt.com/"
    article_selector = "article, .news-item, .post, .article"
    title_selector = "h2 a, h3 a, .entry-title a, .title a"
    link_selector = "h2 a, h3 a, .entry-title a, .title a"
    summary_selector = ".excerpt, .entry-summary, p"


# Registry consumed by the orchestrator in Phase 6.
ALL_ARABIC_FETCHERS = [ArgaamFetcher, MubasherFetcher, AlEqtisadiahFetcher]
