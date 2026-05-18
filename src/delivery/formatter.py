"""Render a Report into two output formats:

  1. `format_whatsapp(report)` → list of message chunks. Each section ships
     as its own short message (~400–500 chars) with bullet-pointed lines so
     the reader can scan quickly on a phone.
  2. `format_markdown(report)` → full Markdown document for the archive.
  3. `save_markdown(report, dir)` → write the Markdown to YYYY-MM-DD.md.

Dates are rendered in the user's configured timezone (settings.timezone)
rather than UTC, so "2026-05-17" reflects the reader's local morning.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

from src.config import settings
from src.models import MarketIndex, Report, ReportSection

# Hard cap is 1600 (Twilio WhatsApp); leave headroom for the chunk-number suffix.
WHATSAPP_MAX_CHARS = 1500
# Soft target: each section aims for ~400–500 chars so the message reads quickly.
WHATSAPP_TARGET_CHARS = 500
# Reserve for the trailing "\n\n_(NN/NN)_" continuation marker.
_MARKER_RESERVE = 20


# ----- WhatsApp ---------------------------------------------------------- #


def format_whatsapp(report: Report) -> List[str]:
    """Return one message per section (~400–500 chars each).

    Each section ships as its own chunk so the recipient sees the report as
    five-to-six short, scannable messages instead of one wall of text. Long
    sections are split further on bullet boundaries with a ` _(cont.)_` marker
    so the title still appears at the top of every continuation.
    """
    header = f"📊 *Daily Financial Brief* — {_local_date(report.date):%Y-%m-%d}"
    section_texts = [
        _format_section_whatsapp(s)
        for s in (
            report.us_market,
            report.saudi_market,
            report.global_macro,
            report.subscriptions,
            report.newsletters,
            report.watch_today,
        )
    ]
    section_texts = [s for s in section_texts if s]

    if not section_texts:
        return [header]

    effective_cap = WHATSAPP_MAX_CHARS - _MARKER_RESERVE
    chunks: List[str] = []
    for sec_text in section_texts:
        chunks.extend(_split_section(sec_text, WHATSAPP_TARGET_CHARS, effective_cap))

    # Inline the header with chunk 1 so it never ships alone.
    chunks[0] = f"{header}\n\n{chunks[0]}"

    # Fallback: if header + sub-chunk overflows, ship header as its own message.
    if len(chunks[0]) > effective_cap:
        chunks[0] = chunks[0][len(header) + 2:]
        chunks.insert(0, header)

    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"{c}\n\n_({i + 1}/{total})_" for i, c in enumerate(chunks)]
    return chunks


def _format_section_whatsapp(section: ReportSection) -> str:
    if not section.indices and not section.summary:
        return ""

    title = section.title
    if "Newsletters" in title and section.items:
        title = f"{title} ({len(section.items)} processed)"

    parts: List[str] = [f"*{title}*"]
    if section.indices:
        parts.append(_format_indices_compact(section.indices))
    if section.summary:
        parts.append(_bulletize(section.summary))
    return "\n".join(parts)


def _format_indices_compact(indices: List[MarketIndex]) -> str:
    """One line per index with a trend emoji: 📈/📉 SYMBOL: value (±change, ±pct%)."""
    out: List[str] = []
    for idx in indices:
        arrow = "📈" if idx.change >= 0 else "📉"
        sign = "+" if idx.change >= 0 else ""
        out.append(
            f"{arrow} `{idx.symbol}`: {idx.value:,.2f} "
            f"({sign}{idx.change:.2f}, {sign}{idx.change_pct:.1f}%)"
        )
    return "\n".join(out)


def _bulletize(text: str) -> str:
    """Turn a summary into one bullet per line for at-a-glance scannability."""
    text = text.strip()
    if not text:
        return ""

    # Prefer existing line structure if the summarizer produced multiple lines.
    raw_lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(raw_lines) >= 2:
        parts = raw_lines
    else:
        # Single paragraph — split on sentence terminators followed by a capital/digit.
        parts = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", raw_lines[0])
            if s.strip()
        ]

    return "\n".join(_with_bullet(p) for p in parts)


def _with_bullet(line: str) -> str:
    """Prefix a line with the bullet glyph, normalizing other bullet markers."""
    if line.startswith("• "):
        return line
    for marker in ("- ", "* ", "– ", "— "):
        if line.startswith(marker):
            return "• " + line[len(marker):]
    return f"• {line}"


def _split_section(text: str, target: int, hard_cap: int) -> List[str]:
    """Split a section into chunks ≤ target chars on bullet boundaries.

    Title (first line) is preserved at the top of each sub-chunk; continuation
    sub-chunks reuse the title with a ` _(cont.)_` marker so context is kept.
    Any sub-chunk that still exceeds `hard_cap` is word-split as a last resort.
    """
    if len(text) <= target:
        return _enforce_hard_cap([text], hard_cap)

    lines = text.split("\n")
    title_line = lines[0]
    body_lines = lines[1:]
    if not body_lines:
        return _enforce_hard_cap([text], hard_cap)

    cont_title = f"{title_line} _(cont.)_"

    out: List[str] = []
    current = [title_line]
    current_len = len(title_line)
    for line in body_lines:
        addition = 1 + len(line)  # \n + line
        # Flush only when there's already at least one body line in the current chunk.
        if current_len + addition > target and len(current) > 1:
            out.append("\n".join(current))
            current = [cont_title]
            current_len = len(cont_title)
            addition = 1 + len(line)
        current.append(line)
        current_len += addition
    if current:
        out.append("\n".join(current))

    return _enforce_hard_cap(out, hard_cap)


def _enforce_hard_cap(chunks: List[str], hard_cap: int) -> List[str]:
    out: List[str] = []
    for c in chunks:
        if len(c) <= hard_cap:
            out.append(c)
        else:
            out.extend(_word_split(c, hard_cap))
    return out


def _word_split(text: str, max_chars: int) -> List[str]:
    """Split text at word boundaries, falling back to char-split if no good cut exists."""
    out: List[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        out.append(remaining)
    return out


# ----- Markdown archive -------------------------------------------------- #


def format_markdown(report: Report) -> str:
    """Render the full report as a standalone Markdown document."""
    lines: List[str] = [
        f"# Daily Financial Brief — {_local_date(report.date):%Y-%m-%d}",
        "",
        f"*Generated from {report.raw_item_count} items after dedup + watchlist + prioritization.*",
        "",
    ]
    for section in (
        report.us_market,
        report.saudi_market,
        report.global_macro,
        report.subscriptions,
        report.newsletters,
        report.watch_today,
    ):
        lines.extend(_format_section_markdown(section))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_section_markdown(section: ReportSection) -> List[str]:
    title = section.title
    if "Newsletters" in title and section.items:
        title = f"{title} ({len(section.items)} processed)"

    out: List[str] = [f"## {title}", ""]

    if section.indices:
        out.append("| Symbol | Value | Change | % Change |")
        out.append("|---|---:|---:|---:|")
        for idx in section.indices:
            sign = "+" if idx.change >= 0 else ""
            out.append(
                f"| `{idx.symbol}` | {idx.value:,.2f} | "
                f"{sign}{idx.change:.2f} | {sign}{idx.change_pct:.1f}% |"
            )
        out.append("")

    if section.summary:
        out.append(section.summary.strip())
        out.append("")

    if section.items:
        out.append("**Sources:**")
        for it in section.items:
            out.append(f"- [{it.title}]({it.url}) — *{it.source}*")
        out.append("")
    return out


def save_markdown(report: Report, reports_dir: Path) -> Path:
    """Persist the Markdown report to `reports_dir/YYYY-MM-DD.md`."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{_local_date(report.date):%Y-%m-%d}.md"
    path.write_text(format_markdown(report), encoding="utf-8")
    return path


# ----- Internals --------------------------------------------------------- #


def _local_date(dt: datetime) -> datetime:
    """Convert a UTC datetime to the user's configured timezone for display."""
    return dt.astimezone(ZoneInfo(settings.timezone))
