"""Tests for src/processors/watchlist_filter.py."""

from datetime import datetime, timezone

from src.models import NewsItem
from src.processors.watchlist_filter import WatchlistFilter


def _item(title: str, content: str = "", source: str = "test") -> NewsItem:
    return NewsItem(
        id="x",
        title=title,
        url="https://example.com/x",
        source=source,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        content=content,
    )


def test_empty_config_is_pass_through():
    f = WatchlistFilter(config={})
    assert f.is_pass_through()
    items = [_item("Apple news"), _item("Tesla news"), _item("Saudi news")]
    assert f.filter(items) == items


def test_keyword_keeps_only_matching():
    f = WatchlistFilter(config={"keywords": ["Apple"]})
    items = [_item("Apple Q4 earnings"), _item("Tesla recall")]
    out = f.filter(items)
    assert len(out) == 1
    assert "Apple" in out[0].title


def test_keyword_match_is_case_insensitive():
    f = WatchlistFilter(config={"keywords": ["aPPle"]})
    items = [_item("APPLE Earnings")]
    assert len(f.filter(items)) == 1


def test_ignored_topic_drops_match():
    f = WatchlistFilter(config={"ignored_topics": ["crypto"]})
    items = [
        _item("Bitcoin hits new high (crypto news)"),
        _item("Apple Q4 earnings"),
    ]
    out = f.filter(items)
    assert len(out) == 1
    assert "Apple" in out[0].title


def test_ignored_wins_over_include():
    """If an item matches BOTH an include term and an ignored term, drop it."""
    f = WatchlistFilter(
        config={"keywords": ["bitcoin"], "ignored_topics": ["crypto"]}
    )
    items = [_item("Bitcoin (crypto) hits new high")]
    assert f.filter(items) == []


def test_only_ignored_configured_keeps_rest():
    f = WatchlistFilter(config={"ignored_topics": ["sports"]})
    items = [_item("Apple earnings"), _item("NBA finals recap sports")]
    out = f.filter(items)
    assert len(out) == 1
    assert "Apple" in out[0].title


def test_stocks_us_acts_as_include_terms():
    f = WatchlistFilter(config={"stocks": {"us": ["NVDA"]}})
    items = [_item("NVDA hits new high"), _item("Apple earnings")]
    out = f.filter(items)
    assert len(out) == 1
    assert "NVDA" in out[0].title


def test_match_searches_content_and_source_too():
    f = WatchlistFilter(config={"keywords": ["aramco"]})
    items = [
        _item("Saudi market update", content="Aramco posted Q3 profits up 12%"),
        _item("Generic market wrap", content="No specific company mentioned"),
    ]
    out = f.filter(items)
    assert len(out) == 1
    assert "Saudi" in out[0].title
