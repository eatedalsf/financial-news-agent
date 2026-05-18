"""Tests for src/delivery/formatter.py."""

from datetime import datetime, timezone
from typing import Optional

from src.delivery.formatter import (
    WHATSAPP_MAX_CHARS,
    format_markdown,
    format_whatsapp,
)
from src.models import MarketIndex, NewsItem, Report, ReportSection


def _section(
    title: str,
    summary: Optional[str] = None,
    indices: Optional[list] = None,
    items: Optional[list] = None,
) -> ReportSection:
    return ReportSection(
        title=title,
        summary=summary,
        indices=indices or [],
        items=items or [],
    )


def _report(**overrides) -> Report:
    """Build a synthetic Report with sensible defaults for tests."""
    now = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    defaults = dict(
        us_market=_section("🇺🇸 US Market", summary="US is up."),
        saudi_market=_section("🇸🇦 Saudi Market", summary="Saudi steady."),
        global_macro=_section("🌍 Global Macro", summary="Macro flat."),
        subscriptions=_section("📰 From Your Subscriptions", summary="WSJ + FT."),
        newsletters=_section("📧 From Your Newsletters", summary="Newsletters ok."),
        watch_today=_section("🎯 Watch Today", summary="Watch NVDA earnings."),
    )
    defaults.update(overrides)
    return Report(date=now, raw_item_count=10, **defaults)


# ----- WhatsApp --------------------------------------------------------- #


def test_whatsapp_chunks_all_under_limit():
    chunks = format_whatsapp(_report())
    assert len(chunks) >= 1
    for c in chunks:
        assert len(c) <= WHATSAPP_MAX_CHARS


def test_whatsapp_header_has_date_and_title():
    chunks = format_whatsapp(_report())
    assert "Daily Financial Brief" in chunks[0]
    assert "2026-05-17" in chunks[0]


def test_whatsapp_splits_when_too_long():
    long_summary = "x " * 1200  # ~2400 chars, way over the chunk limit
    rep = _report(us_market=_section("🇺🇸 US Market", summary=long_summary))
    chunks = format_whatsapp(rep)
    assert len(chunks) >= 2
    # Continuation marker appears on the FIRST chunk too
    assert "(1/" in chunks[0]
    assert f"/{len(chunks)})" in chunks[0]


def test_whatsapp_header_inlined_with_first_section():
    """Header must never be a chunk on its own when multi-chunking."""
    # Force multi-chunk: one section large enough to push us past the limit.
    big = "Some narrative. " * 200  # ~3200 chars
    rep = _report(us_market=_section("🇺🇸 US Market", summary=big))
    chunks = format_whatsapp(rep)
    assert len(chunks) >= 2
    # Chunk 1 must contain both the header AND content from the first section.
    assert "Daily Financial Brief" in chunks[0]
    assert "🇺🇸 US Market" in chunks[0]
    # The header alone is ~50 chars; chunk 1 must be substantially larger.
    assert len(chunks[0]) > 500, (
        f"Header was sent as a stand-alone chunk ({len(chunks[0])} chars) — "
        "regression of the header-inlining fix."
    )


def test_whatsapp_index_line_format():
    spx = MarketIndex(
        symbol="SPX",
        name="S&P 500",
        value=5421.30,
        change=18.42,
        change_pct=0.34,
        timestamp=datetime.now(timezone.utc),
    )
    rep = _report(us_market=_section("🇺🇸 US Market", indices=[spx], summary="up"))
    body = format_whatsapp(rep)[0]
    assert "`SPX`" in body
    assert "5,421.30" in body
    assert "+18.42" in body
    assert "+0.3%" in body


# ----- Markdown --------------------------------------------------------- #


def test_markdown_has_top_heading_and_all_sections():
    md = format_markdown(_report())
    assert md.startswith("# Daily Financial Brief")
    for section_title in (
        "## 🇺🇸 US Market",
        "## 🇸🇦 Saudi Market",
        "## 🌍 Global Macro",
        "## 📰 From Your Subscriptions",
        "## 📧 From Your Newsletters",
        "## 🎯 Watch Today",
    ):
        assert section_title in md


def test_markdown_renders_indices_as_table():
    spx = MarketIndex(
        symbol="SPX",
        name="S&P 500",
        value=5421.30,
        change=18.42,
        change_pct=0.34,
        timestamp=datetime.now(timezone.utc),
    )
    rep = _report(us_market=_section("🇺🇸 US Market", indices=[spx], summary="up"))
    md = format_markdown(rep)
    assert "| Symbol |" in md
    assert "| `SPX` |" in md
    assert "5,421.30" in md


def test_markdown_newsletter_section_shows_processed_count():
    items = [
        NewsItem(
            id=str(i),
            title=f"Item {i}",
            url=f"https://example.com/{i}",
            source="news",
            published_at=datetime.now(timezone.utc),
            category="newsletter",
        )
        for i in range(3)
    ]
    rep = _report(
        newsletters=_section("📧 From Your Newsletters", items=items, summary="ok")
    )
    md = format_markdown(rep)
    assert "(3 processed)" in md


def test_markdown_source_links_rendered():
    items = [
        NewsItem(
            id="x",
            title="Big news",
            url="https://example.com/x",
            source="reuters",
            published_at=datetime.now(timezone.utc),
        )
    ]
    rep = _report(us_market=_section("🇺🇸 US Market", items=items, summary="up"))
    md = format_markdown(rep)
    assert "[Big news](https://example.com/x)" in md
    assert "*reuters*" in md
