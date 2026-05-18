"""Financial Times section scraper using the user's session cookies.

Cookies live in env var `FT_COOKIES`. Same expiry/refresh pattern as WSJ.
"""

import hashlib
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from src.config import load_sources, settings
from src.fetchers.base import BaseFetcher
from src.models import NewsItem
from src.utils.logger import logger

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class FTFetcher(BaseFetcher):
    """Scrape FT markets / companies sections with logged-in cookies."""

    source_name = "ft"

    def __init__(
        self,
        sections: Optional[List[str]] = None,
        category: str = "subscription",
    ) -> None:
        cfg = load_sources().get("subscriptions", {}).get("ft", {})
        self.sections = sections or cfg.get("sections") or [
            "https://www.ft.com/markets",
            "https://www.ft.com/companies",
        ]
        self.category = category

    async def fetch(self) -> List[NewsItem]:
        if not settings.ft_cookies:
            logger.warning(f"{self.source_name}: FT_COOKIES not set, skipping")
            return []

        all_items: List[NewsItem] = []
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Cookie": settings.ft_cookies,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            for section_url in self.sections:
                try:
                    items = await self._scrape_section(client, section_url)
                    all_items.extend(items)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        f"{self.source_name}: section {section_url} failed - {exc}"
                    )
        return all_items

    async def _scrape_section(
        self, client: httpx.AsyncClient, url: str
    ) -> List[NewsItem]:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

        soup = BeautifulSoup(html, "lxml")
        # FT uses /content/<uuid> for article URLs; we filter on that to avoid
        # picking up nav links, podcasts, and event pages.
        candidates = soup.select(
            "a.js-teaser-heading-link, "
            ".o-teaser__heading a, "
            "a[href*='/content/']"
        )

        now = datetime.now(timezone.utc)
        items: List[NewsItem] = []
        seen: set[str] = set()
        for a in candidates:
            href = a.get("href")
            title = a.get_text(strip=True)
            if not href or not title or len(title) < 10:
                continue
            full_url = urljoin("https://www.ft.com", str(href))
            if "/content/" not in full_url:
                continue
            if full_url in seen:
                continue
            seen.add(full_url)
            items.append(
                NewsItem(
                    id=hashlib.sha256(full_url.encode("utf-8")).hexdigest()[:16],
                    title=title,
                    url=full_url,
                    source=self.source_name,
                    published_at=now,
                    language="en",
                    category=self.category,
                )
            )

        if not items:
            logger.warning(
                f"{self.source_name}: 0 article links on {url} - "
                "cookies may be expired or markup changed"
            )
        return items
