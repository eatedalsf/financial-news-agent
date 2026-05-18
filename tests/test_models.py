"""Tests for the pydantic models in src/models.py."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models import MarketIndex, NewsItem, Report, ReportSection


def test_news_item_minimal_valid():
    item = NewsItem(
        id="x",
        title="Headline",
        url="https://example.com/x",
        source="test",
        published_at=datetime.now(timezone.utc),
    )
    assert item.language == "en"
    assert item.tags == []
    assert item.category is None
    assert item.content is None


def test_news_item_missing_required_fields_raises():
    with pytest.raises(ValidationError):
        NewsItem(title="missing id and url")  # type: ignore[call-arg]


def test_market_index_round_trip():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    idx = MarketIndex(
        symbol="SPX",
        name="S&P 500",
        value=5000.0,
        change=10.0,
        change_pct=0.2,
        timestamp=now,
    )
    restored = MarketIndex.model_validate(idx.model_dump(mode="json"))
    assert restored == idx


def test_report_section_defaults_are_empty():
    s = ReportSection(title="X")
    assert s.indices == []
    assert s.items == []
    assert s.summary is None


def test_report_assembles_with_six_sections():
    now = datetime.now(timezone.utc)
    sections = {
        name: ReportSection(title=name)
        for name in (
            "us_market",
            "saudi_market",
            "global_macro",
            "subscriptions",
            "newsletters",
            "watch_today",
        )
    }
    report = Report(date=now, raw_item_count=5, **sections)
    assert report.us_market.title == "us_market"
    assert report.raw_item_count == 5
