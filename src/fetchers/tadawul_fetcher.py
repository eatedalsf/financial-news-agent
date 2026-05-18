"""Tadawul (Saudi Exchange) issuer announcements fetcher.

The official saudiexchange.sa portal is a WebSphere-rendered site that
changes layout occasionally. This scraper is best-effort: if it stops
returning rows, the documented fallback is to scrape Argaam's disclosures
section (which mirrors Tadawul announcements within minutes).
"""

import hashlib
from datetime import datetime, timezone
from typing import List
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from src.config import load_sources
from src.fetchers.base import BaseFetcher
from src.models import NewsItem
from src.utils.logger import logger

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class TadawulFetcher(BaseFetcher):
    """Scrape the latest issuer disclosures from saudiexchange.sa."""

    source_name = "tadawul"

    DISCLOSURES_URL = (
        "https://www.saudiexchange.sa/wps/portal/saudiexchange/"
        "news-and-reports/issuer-news/issuer-announcements"
    )

    def __init__(self) -> None:
        cfg = load_sources().get("official", {}).get("tadawul", {})
        self.base_url = cfg.get("base_url", "https://www.saudiexchange.sa/")
        self.category = "saudi_market"
        self.max_items = 30

    async def fetch(self) -> List[NewsItem]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            },
        ) as client:
            resp = await client.get(self.DISCLOSURES_URL)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "lxml")
        # WebSphere portlet IDs are generated; selectors must be broad.
        rows = soup.select(
            "table tr, .announcement-row, .disclosure-item, .news-item"
        )
        if not rows:
            logger.warning(
                f"{self.source_name}: 0 rows found - portal markup may have "
                "changed; consider falling back to Argaam disclosures scrape"
            )
            return []

        now = datetime.now(timezone.utc)
        items: List[NewsItem] = []
        seen: set[str] = set()
        for row in rows[: self.max_items * 2]:  # rows includes header/empty rows
            link = row.select_one("a[href]")
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href")
            if not title or not href or len(title) < 5:
                continue
            url = urljoin(self.base_url, str(href))
            if url in seen:
                continue
            seen.add(url)
            items.append(
                NewsItem(
                    id=hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                    title=title,
                    url=url,
                    source=self.source_name,
                    published_at=now,
                    language="ar",  # Most disclosures are bilingual; default to AR
                    category=self.category,
                )
            )
            if len(items) >= self.max_items:
                break
        return items
