"""Render a Report into two output formats:

  1. `format_whatsapp(report)` → list of message chunks (each ≤1500 chars,
     split at section boundaries) ready for Twilio's WhatsApp endpoint.
  2. `format_markdown(report)` → full Markdown document for the archive.
  3. `save_markdown(report, dir)` → write the Markdown to YYYY-MM-DD.md.

Dates are rendered in the user's configured timezone (settings.timezone)
rather than UTC, so "2026-05-17" reflects the reader's local morning.
"""

from datetime import datetime
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

from src.config import settings
from src.models import MarketIndex, Report, ReportSection

# Hard cap is 1600 (Twilio WhatsApp); leave headroom for the chunk-number suffix.
WHATSAPP_MAX_CHARS = 1500


# ----- WhatsApp ---------------------------------------------------------- #


def format_whatsapp(report: Report) -> List[str]:
    """Return one or more message bodies, each within the WhatsApp char limit."""
    header = f"📊 *Daily Financial Brief* — {_local_date(report.date):%Y-%m-%d}"
    sections = [
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
    return _chunk_message(header, [s for s in sections if s], WHATSAPP_MAX_CHARS)


def _format_section_whatsapp(section: ReportSection) -> str:
    title = section.title
    # Augment the newsletter section title with the processed-count per the spec.
    if "Newsletters" in title and section.items:
        title = f"{title} ({len(section.items)} processed)"

    parts: List[str] = [f"*{title}*"]
    if section.indices:
        parts.append(_format_indices_compact(section.indices))
    if section.summary:
        parts.append(section.summary)
    return "\n".join(parts)


def _format_indices_compact(indices: List[MarketIndex]) -> str:
    """One line per index: `SYMBOL`: value (±change, ±pct%)."""
    out: List[str] = []
    for idx in indices:
        sign = "+" if idx.change >= 0 else ""
        out.append(
            f"`{idx.symbol}`: {idx.value:,.2f} "
            f"({sign}{idx.change:.2f}, {sign}{idx.change_pct:.1f}%)"
        )
    return "\n".join(out)


def _chunk_message(
    header: str, sections: List[str], max_chars: int
) -> List[str]:
    """Greedy-pack sections into chunks, adding "(i/n)" suffix when split."""
    full = header + "\n\n" + "\n\n".join(sections)
    if len(full) <= max_chars:
        return [full]

    chunks: List[str] = []
    current = header
    for sec in sections:
        candidate = current + "\n\n" + sec if current else sec
        if len(candidate) <= max_chars:
            current = candidate
            continue
        # Current chunk full → flush
        if current.strip():
            chunks.append(current)
        # If a single section alone is too long, split it on paragraphs / hard.
        if len(sec) > max_chars:
            chunks.extend(_split_long_section(sec, max_chars))
            current = ""
        else:
            current = sec
    if current.strip():
        chunks.append(current)

    # Annotate so the recipient knows there's more to come.
    total = len(chunks)
    if total > 1:
        chunks = [f"{c}\n\n_({i + 1}/{total})_" for i, c in enumerate(chunks)]
    return chunks


def _split_long_section(sec: str, max_chars: int) -> List[str]:
    """Fallback when a single section exceeds the chunk limit."""
    out: List[str] = []
    current = ""
    for para in sec.split("\n\n"):
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            out.append(current)
        if len(para) > max_chars:
            # Hard char-split as absolute last resort
            for i in range(0, len(para), max_chars):
                out.append(para[i : i + max_chars])
            current = ""
        else:
            current = para
    if current:
        out.append(current)
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
