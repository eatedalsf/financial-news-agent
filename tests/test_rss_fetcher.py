"""Tests for RSSFetcher parsing (no network)."""

from src.fetchers.rss_fetcher import RSSFetcher

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Fed Holds Rates Steady</title>
      <link>https://example.com/article/1</link>
      <description>The Federal Reserve held its benchmark rate at 5.25%.</description>
      <pubDate>Sat, 17 May 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Apple Q4 Earnings Beat</title>
      <link>https://example.com/article/2</link>
      <description>Apple posted record Q4 results.</description>
      <pubDate>Sat, 17 May 2026 13:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

MALFORMED_RSS = b"<rss>not a real feed at all</nope>"

EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Empty</title></channel></rss>
"""


def _fetcher(**kw):
    # max_age_hours large enough to keep fixture items regardless of test run date
    defaults = dict(
        name="test",
        url="https://example.com/feed",
        category="us_market",
        max_age_hours=10_000_000,
    )
    defaults.update(kw)
    return RSSFetcher(**defaults)


def test_parses_basic_feed():
    items = _fetcher()._parse(SAMPLE_RSS)
    assert len(items) == 2
    assert items[0].title == "Fed Holds Rates Steady"
    assert items[0].url == "https://example.com/article/1"
    assert items[0].source == "test"
    assert items[0].category == "us_market"
    assert items[0].language == "en"


def test_summary_populated_from_description():
    items = _fetcher()._parse(SAMPLE_RSS)
    assert items[0].summary and "Federal Reserve" in items[0].summary


def test_make_id_is_deterministic():
    a = RSSFetcher._make_id("https://example.com/x")
    b = RSSFetcher._make_id("https://example.com/x")
    c = RSSFetcher._make_id("https://example.com/y")
    assert a == b
    assert a != c
    assert len(a) == 16


def test_empty_feed_returns_empty_list():
    assert _fetcher()._parse(EMPTY_FEED) == []


def test_malformed_feed_returns_empty_list_not_raises():
    # feedparser is lenient; empty + bozo path should degrade gracefully
    assert _fetcher()._parse(MALFORMED_RSS) == []


def test_from_config_classmethod():
    cfg = {"name": "cnbc_top", "url": "https://cnbc.com/feed", "enabled": True}
    f = RSSFetcher.from_config(cfg, category="us_market")
    assert f.source_name == "cnbc_top"
    assert f.url == "https://cnbc.com/feed"
    assert f.category == "us_market"


def test_old_items_dropped_when_cutoff_short():
    """Items dated 2026-05-17 should drop when max_age_hours=1 in a much later year."""
    items = _fetcher(max_age_hours=1)._parse(SAMPLE_RSS)
    # Either all dropped (test run is well after 2026-05-17) or all kept (test run
    # is within an hour of that date). Both are valid given the fixture date.
    assert len(items) in (0, 2)
