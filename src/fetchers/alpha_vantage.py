"""Alpha Vantage market-data fetcher.

Free-tier limits: 5 requests/minute, 500/day. The default 3-index set uses 3
requests per daily run, so we are well under quota.

Alpha Vantage does not expose major US indices directly — we use liquid ETF
proxies (SPY for S&P 500, QQQ for Nasdaq 100, DIA for Dow). The MarketIndex
returned keeps the index symbol (SPX/IXIC/DJI) so downstream rendering reads
naturally.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from src.config import settings
from src.fetchers.base import BaseIndexFetcher
from src.models import MarketIndex
from src.utils.logger import logger

_BASE_URL = "https://www.alphavantage.co/query"

# Index symbol → (ETF proxy ticker, display name)
INDEX_TO_ETF: Dict[str, Tuple[str, str]] = {
    "SPX": ("SPY", "S&P 500 (SPY ETF)"),
    "IXIC": ("QQQ", "Nasdaq 100 (QQQ ETF)"),
    "DJI": ("DIA", "Dow Jones (DIA ETF)"),
}


class AlphaVantageFetcher(BaseIndexFetcher):
    """Fetch latest quotes for US index ETF proxies."""

    source_name = "alpha_vantage"

    def __init__(self, indices: Optional[List[str]] = None) -> None:
        self.indices = indices or list(INDEX_TO_ETF.keys())

    async def fetch(self) -> List[MarketIndex]:
        if not settings.alpha_vantage_api_key:
            logger.warning(
                f"{self.source_name}: ALPHA_VANTAGE_API_KEY not set, skipping"
            )
            return []

        results: List[MarketIndex] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for index_symbol in self.indices:
                proxy = INDEX_TO_ETF.get(index_symbol)
                if proxy is None:
                    logger.warning(
                        f"{self.source_name}: no ETF proxy mapped for {index_symbol}"
                    )
                    continue
                etf_ticker, display = proxy
                try:
                    quote = await self._global_quote(client, etf_ticker)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        f"{self.source_name}: {etf_ticker} request failed - {exc}"
                    )
                    continue
                if quote is None:
                    continue
                results.append(
                    MarketIndex(
                        symbol=index_symbol,
                        name=display,
                        value=quote["price"],
                        change=quote["change"],
                        change_pct=quote["change_pct"],
                        timestamp=datetime.now(timezone.utc),
                    )
                )
        return results

    async def _global_quote(
        self, client: httpx.AsyncClient, symbol: str
    ) -> Optional[dict]:
        """Call GLOBAL_QUOTE; return parsed price/change or None on rate-limit."""
        resp = await client.get(
            _BASE_URL,
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": settings.alpha_vantage_api_key,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

        quote = payload.get("Global Quote") or {}
        if not quote:
            # Alpha Vantage signals throttling/quota via "Note" or "Information"
            note = payload.get("Note") or payload.get("Information")
            if note:
                logger.warning(f"{self.source_name}: {note}")
            return None
        try:
            return {
                "price": float(quote["05. price"]),
                "change": float(quote["09. change"]),
                "change_pct": float(str(quote["10. change percent"]).rstrip("%")),
            }
        except (KeyError, ValueError) as exc:
            logger.warning(
                f"{self.source_name}: malformed quote for {symbol} - {exc}"
            )
            return None
