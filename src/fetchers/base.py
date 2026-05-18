"""Abstract base classes for fetchers.

Two flavors:
  - BaseFetcher       → returns list[NewsItem]   (RSS, scraping, Gmail, etc.)
  - BaseIndexFetcher  → returns list[MarketIndex] (Alpha Vantage, FRED, etc.)

Both expose `safe_fetch()` so one source failing never crashes the daily run.
"""

from abc import ABC, abstractmethod
from typing import List

import httpx

from src.models import MarketIndex, NewsItem
from src.utils.logger import logger


class BaseFetcher(ABC):
    """Abstract base for any source that produces NewsItems."""

    source_name: str = "unknown"
    timeout_seconds: int = 30

    @abstractmethod
    async def fetch(self) -> List[NewsItem]:
        """Fetch the latest news items (last 24 hours) from this source."""
        raise NotImplementedError

    async def safe_fetch(self) -> List[NewsItem]:
        """Run `fetch()` with broad exception handling. Returns [] on failure."""
        try:
            items = await self.fetch()
            logger.info(f"{self.source_name}: fetched {len(items)} items")
            return items
        except httpx.HTTPStatusError as exc:
            # Expected when sites block scrapers (403) or are down (5xx).
            # Log status only — no traceback noise.
            logger.warning(
                f"{self.source_name}: HTTP {exc.response.status_code} "
                f"from {exc.request.url}"
            )
            return []
        except Exception as exc:  # noqa: BLE001 - intentional broad catch at boundary
            logger.exception(f"{self.source_name}: fetch failed - {exc}")
            return []


class BaseIndexFetcher(ABC):
    """Abstract base for market data sources returning MarketIndex objects."""

    source_name: str = "unknown"
    timeout_seconds: int = 30

    @abstractmethod
    async def fetch(self) -> List[MarketIndex]:
        """Fetch the latest index/series values from this source."""
        raise NotImplementedError

    async def safe_fetch(self) -> List[MarketIndex]:
        """Run `fetch()` with broad exception handling. Returns [] on failure."""
        try:
            items = await self.fetch()
            logger.info(f"{self.source_name}: fetched {len(items)} indices")
            return items
        except httpx.HTTPStatusError as exc:
            logger.warning(
                f"{self.source_name}: HTTP {exc.response.status_code} "
                f"from {exc.request.url}"
            )
            return []
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"{self.source_name}: fetch failed - {exc}")
            return []
