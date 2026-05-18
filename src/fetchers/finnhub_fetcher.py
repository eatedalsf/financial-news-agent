"""Finnhub general-category financial news.

Free tier: 60 requests/minute. We call the news endpoint exactly once per run,
so quota is not a concern.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import List

import httpx

from src.config import load_sources, settings
from src.fetchers.base import BaseFetcher
from src.models import NewsItem
from src.utils.logger import logger

_BASE_URL = "https://finnhub.io/api/v1/news"


class FinnhubFetcher(BaseFetcher):
    """General financial news from Finnhub (mostly US-focused)."""

    source_name = "finnhub"

    def __init__(self) -> None:
        cfg = load_sources().get("apis", {}).get("finnhub", {})
        self.api_category = cfg.get("category", "general")
        self.news_category = "us_market"

    async def fetch(self) -> List[NewsItem]:
        if not settings.finnhub_api_key:
            logger.warning(
                f"{self.source_name}: FINNHUB_API_KEY not set, skipping"
            )
            return []

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(
                _BASE_URL,
                params={
                    "category": self.api_category,
                    "token": settings.finnhub_api_key,
                },
            )
            resp.raise_for_status()
            payload = resp.json()

        if not isinstance(payload, list):
            logger.warning(
                f"{self.source_name}: unexpected response shape "
                f"({type(payload).__name__})"
            )
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        items: List[NewsItem] = []
        for entry in payload:
            ts = entry.get("datetime")
            if not ts:
                continue
            published = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            if published < cutoff:
                continue
            url = entry.get("url", "")
            title = (entry.get("headline") or "").strip()
            if not url or not title:
                continue
            items.append(
                NewsItem(
                    id=hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                    title=title,
                    url=url,
                    source=self.source_name,
                    published_at=published,
                    summary=entry.get("summary") or None,
                    language="en",
                    category=self.news_category,
                )
            )
        return items
