"""Tests for src/processors/deduplicator.py."""

from datetime import datetime, timezone
from typing import Optional

from src.models import NewsItem
from src.processors.deduplicator import deduplicate


def _item(
    id_: str,
    title: str,
    url: str,
    content: Optional[str] = None,
) -> NewsItem:
    return NewsItem(
        id=id_,
        title=title,
        url=url,
        source="test",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        content=content,
    )


def test_empty_input_returns_empty():
    assert deduplicate([]) == []


def test_unique_items_all_kept():
    items = [
        _item("a", "Apple beats Q4 earnings", "https://a/1"),
        _item("b", "Tesla recalls 100k vehicles", "https://b/2"),
        _item("c", "Saudi Aramco profits up 12%", "https://c/3"),
    ]
    assert len(deduplicate(items)) == 3


def test_exact_url_duplicates_collapsed():
    items = [
        _item("a", "Title One", "https://example.com/article"),
        _item("b", "Title One Different Source", "https://example.com/article"),
    ]
    result = deduplicate(items)
    assert len(result) == 1


def test_url_dup_keeps_longer_content():
    items = [
        _item("a", "Title", "https://x/1", content="short"),
        _item("b", "Title v2", "https://x/1", content="much longer content body here"),
    ]
    result = deduplicate(items)
    assert len(result) == 1
    assert result[0].content == "much longer content body here"


def test_fuzzy_title_catches_paraphrased_fed_story():
    """Reuters and CNBC reporting the same Fed decision with different wording."""
    items = [
        _item("a", "Fed Holds Rates Steady at 5.25%", "https://reuters/a"),
        _item(
            "b",
            "Federal Reserve holds rates steady at 5.25 percent",
            "https://cnbc/b",
        ),
    ]
    result = deduplicate(items)
    assert len(result) == 1


def test_distinct_apple_stories_kept_separate():
    """Different angles on Apple should NOT collapse — too few shared tokens."""
    items = [
        _item("a", "Apple iPhone 17 launch sets new sales record", "https://x/1"),
        _item("b", "Apple settles antitrust suit with European Commission", "https://x/2"),
    ]
    assert len(deduplicate(items)) == 2
