"""Generic RSS / Atom feed fetcher.

One class handles every feed listed under `global_rss:` in `config/sources.yaml`.
Instantiate per-feed; the orchestrator iterates the YAML and constructs one
RSSFetcher for each enabled entry.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, List, Optional

import feedparser
import httpx

from src.fetchers.base import BaseFetcher
from src.models import NewsItem
from src.utils.logger import logger

_USER_AGENT = "FinancialNewsAgent/0.1 (+https://github.com/local)"


class RSSFetcher(BaseFetcher):
    """Generic RSS feed fetcher.

    Items older than `max_age_hours` are dropped so each daily run only returns
    fresh content. If a feed has no timestamp on an entry, the entry is kept
    (some feeds omit dates entirely — better to keep than to silently drop).
    """

    def __init__(
        self,
        name: str,
        url: str,
        category: Optional[str] = None,
        language: str = "en",
        max_age_hours: int = 24,
    ) -> None:
        self.source_name = name
        self.url = url
        self.category = category
        self.language = language
        self.max_age_hours = max_age_hours

    @classmethod
    def from_config(cls, cfg: dict, category: Optional[str] = None) -> "RSSFetcher":
        """Build from a `sources.yaml` entry (`{name, url, enabled}`)."""
        return cls(name=cfg["name"], url=cfg["url"], category=category)

    async def fetch(self) -> List[NewsItem]:
        if not self.url:
            logger.warning(f"{self.source_name}: empty URL, skipping")
            return []
        raw = await self._download()
        return self._parse(raw)

    async def _download(self) -> bytes:
        """Fetch the raw feed bytes. Split out from `_parse` to keep parsing testable."""
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(self.url)
            resp.raise_for_status()
            return resp.content

    def _parse(self, raw: bytes) -> List[NewsItem]:
        """Parse raw RSS/Atom bytes into NewsItems, dropping items older than cutoff."""
        parsed = feedparser.parse(raw)
        if parsed.bozo and not parsed.entries:
            logger.warning(
                f"{self.source_name}: feed parse error - {parsed.bozo_exception}"
            )
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.max_age_hours)
        items: List[NewsItem] = []
        for entry in parsed.entries:
            url = entry.get("link", "")
            title = (entry.get("title") or "").strip()
            if not url or not title:
                continue
            published = self._parse_date(entry)
            if published is not None and published < cutoff:
                continue
            items.append(
                NewsItem(
                    id=self._make_id(url),
                    title=title,
                    url=url,
                    source=self.source_name,
                    published_at=published or datetime.now(timezone.utc),
                    summary=entry.get("summary") or entry.get("description"),
                    language=self.language,
                    category=self.category,
                )
            )
        return items

    @staticmethod
    def _parse_date(entry: Any) -> Optional[datetime]:
        """Try several date fields in order of preference."""
        for attr in ("published_parsed", "updated_parsed"):
            ts = entry.get(attr)
            if ts:
                try:
                    return datetime(*ts[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass
        for attr in ("published", "updated"):
            raw = entry.get(attr)
            if raw:
                try:
                    dt = parsedate_to_datetime(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except (TypeError, ValueError):
                    pass
        return None

    @staticmethod
    def _make_id(url: str) -> str:
        """Stable 16-char hash of the URL for dedup keys."""
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
