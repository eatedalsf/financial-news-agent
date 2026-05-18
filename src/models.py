"""Domain models shared across fetchers, processors, and delivery."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    """A single news article from any source."""

    id: str  # Stable hash derived from URL (or title+source if URL missing)
    title: str
    url: str
    source: str  # e.g., "reuters", "wsj", "argaam"
    published_at: datetime
    summary: Optional[str] = None
    content: Optional[str] = None  # Full article text if scraped
    language: str = "en"  # "en" | "ar"
    category: Optional[str] = None  # us_market | saudi_market | macro | subscription | newsletter
    tags: List[str] = Field(default_factory=list)


class MarketIndex(BaseModel):
    """A market index, ETF, or stock snapshot."""

    symbol: str  # e.g., "SPX", "IXIC", "TASI"
    name: str
    value: float
    change: float
    change_pct: float
    timestamp: datetime


class ReportSection(BaseModel):
    """One section of the daily report (e.g., US Market, Saudi Market)."""

    title: str  # Section heading as rendered (emoji + label)
    indices: List[MarketIndex] = Field(default_factory=list)
    items: List[NewsItem] = Field(default_factory=list)
    summary: Optional[str] = None  # Claude-generated section narrative


class Report(BaseModel):
    """The full daily report assembled before delivery."""

    date: datetime
    us_market: ReportSection
    saudi_market: ReportSection
    global_macro: ReportSection
    subscriptions: ReportSection
    newsletters: ReportSection
    watch_today: ReportSection
    raw_item_count: int = 0  # Pre-dedup count for diagnostics
