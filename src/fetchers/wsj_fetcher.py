"""WSJ section scraper using the user's session cookies.

Cookies live in env var `WSJ_COOKIES` (raw `Cookie:` header string from
DevTools). When they expire (typically 1-4 weeks) the section page returns
the subscribe wall instead of article tiles; the fetcher logs a clear "0
article links" warning so the user knows to re-export cookies.
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


class WSJFetcher(BaseFetcher):
    """Scrape WSJ markets / economy sections with logged-in cookies."""

    source_name = "wsj"

    def __init__(
        self,
        sections: Optional[List[str]] = None,
        category: str = "subscription",
    ) -> None:
        cfg = load_sources().get("subscriptions", {}).get("wsj", {})
        self.sections = sections or cfg.get("sections") or [
            "https://www.wsj.com/news/markets",
            "https://www.wsj.com/news/economy",
        ]
        self.category = category

    async def fetch(self) -> List[NewsItem]:
        if not settings.wsj_cookies:
            logger.warning(f"{self.source_name}: WSJ_COOKIES not set, skipping")
            return []

        all_items: List[NewsItem] = []
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Cookie": settings.wsj_cookies,
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
        # WSJ rotates article-tile class names; we use the link target shape
        # (must contain "/articles/") as the stable signal across redesigns.
        candidates = soup.select(
            "article a[href*='/articles/'], "
            "h2 a[href*='/articles/'], "
            "h3 a[href*='/articles/']"
        )

        now = datetime.now(timezone.utc)
        items: List[NewsItem] = []
        seen: set[str] = set()
        for a in candidates:
            href = a.get("href")
            title = a.get_text(strip=True)
            if not href or not title or len(title) < 10:
                continue
            full_url = urljoin(url, str(href))
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
                "cookies may be expired or markup changed; "
                "re-export cookies and rerun"
            )
        return items
