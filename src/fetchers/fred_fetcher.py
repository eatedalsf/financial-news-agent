"""FRED (Federal Reserve Economic Data) macroeconomic series fetcher.

FRED is free and has generous limits (~120 req/min). We reuse the MarketIndex
model: `symbol` = series id, `value` = latest observation, `change` = delta vs
the previous observation, `timestamp` = observation date.

Default series set covers the four macro indicators the morning brief calls out:
CPI, unemployment, fed funds, and the 10-year Treasury yield. Override via
`config/sources.yaml` → `apis.fred.series`.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from src.config import settings
from src.fetchers.base import BaseIndexFetcher
from src.models import MarketIndex
from src.utils.logger import logger

_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

SERIES_NAMES: Dict[str, str] = {
    "CPIAUCSL": "CPI - All Urban Consumers (SA)",
    "UNRATE": "US Unemployment Rate",
    "DFF": "Federal Funds Effective Rate",
    "DGS10": "10-Year Treasury Constant Maturity",
}


class FredFetcher(BaseIndexFetcher):
    """Fetch latest + previous observation for each FRED series."""

    source_name = "fred"

    def __init__(self, series: Optional[List[str]] = None) -> None:
        self.series = series or list(SERIES_NAMES.keys())

    async def fetch(self) -> List[MarketIndex]:
        if not settings.fred_api_key:
            logger.warning(f"{self.source_name}: FRED_API_KEY not set, skipping")
            return []

        results: List[MarketIndex] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for series_id in self.series:
                try:
                    point = await self._latest_two(client, series_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        f"{self.source_name}: {series_id} failed - {exc}"
                    )
                    continue
                if point is None:
                    continue
                results.append(point)
        return results

    async def _latest_two(
        self, client: httpx.AsyncClient, series_id: str
    ) -> Optional[MarketIndex]:
        resp = await client.get(
            _BASE_URL,
            params={
                "series_id": series_id,
                "api_key": settings.fred_api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
            },
        )
        resp.raise_for_status()
        observations = (resp.json() or {}).get("observations", [])

        # FRED uses "." as the placeholder for missing data points.
        valid = [o for o in observations if o.get("value") not in (".", "", None)]
        if not valid:
            logger.warning(
                f"{self.source_name}: no valid observations for {series_id}"
            )
            return None

        latest = valid[0]
        previous = valid[1] if len(valid) > 1 else None
        latest_value = float(latest["value"])
        prev_value = float(previous["value"]) if previous else latest_value
        change = latest_value - prev_value
        change_pct = (change / prev_value * 100.0) if prev_value else 0.0

        return MarketIndex(
            symbol=series_id,
            name=SERIES_NAMES.get(series_id, series_id),
            value=latest_value,
            change=change,
            change_pct=change_pct,
            timestamp=datetime.fromisoformat(latest["date"]).replace(
                tzinfo=timezone.utc
            ),
        )
