"""Load the most recent daily report as Claude context for the chat handler.

The morning agent writes `logs/reports/YYYY-MM-DD.md`. The chat handler treats
that file as the user's "today" briefing — when someone asks "what's the SPX
doing?" Claude can answer from this context rather than having no idea what
report the user just received.

`load_latest_report()` returns the file body or `None` if nothing is archived
yet. `latest_report_path()` is exposed for logging / debugging.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import REPORTS_DIR
from src.utils.logger import logger

# Cap how much of the report we inject into the system prompt. Reports are
# usually 5-15 KB; the cap is a safety net for runaway long reports.
_MAX_REPORT_CHARS = 20_000


def latest_report_path(reports_dir: Path = REPORTS_DIR) -> Optional[Path]:
    """Return the path to the newest `YYYY-MM-DD.md` in `reports_dir`, or None."""
    if not reports_dir.exists():
        return None
    candidates = sorted(reports_dir.glob("*.md"))
    return candidates[-1] if candidates else None


def load_latest_report(reports_dir: Path = REPORTS_DIR) -> Optional[str]:
    """Return the body of the most recent archived report, or None if none exists."""
    path = latest_report_path(reports_dir)
    if path is None:
        logger.info("chat.context: no archived report found in {}", reports_dir)
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.exception(f"chat.context: failed to read {path} - {exc}")
        return None
    if len(text) > _MAX_REPORT_CHARS:
        text = text[:_MAX_REPORT_CHARS] + "\n\n[... report truncated for context ...]"
    return text


def report_age_days(reports_dir: Path = REPORTS_DIR) -> Optional[int]:
    """Days between the latest archived report's date and today, or None if missing.

    Used by the handler to warn the user when the most recent briefing is stale
    (e.g., scheduler didn't fire). Returns `0` for today's report.
    """
    path = latest_report_path(reports_dir)
    if path is None:
        return None
    try:
        report_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (datetime.now().date() - report_date).days
