"""Entry point for the Financial News Agent.

Modes (via CLI):
  python -m src.main              → one-off run, send WhatsApp + archive Markdown
  python -m src.main --dry-run    → one-off run, archive Markdown, skip WhatsApp
  python -m src.main --schedule   → daemon: cron at TIMEZONE + SCHEDULE_TIME (from .env)

Pipeline (per run):
  1. Fetch all sources in parallel.
  2. Deduplicate (URL exact + fuzzy title).
  3. Watchlist filter (pass-through when config is empty).
  4. Prioritize via Claude (top-N by importance).
  5. Summarize via Claude (parallel per-section narratives).
  6. Archive Markdown to logs/reports/YYYY-MM-DD.md.
  7. Deliver via Twilio WhatsApp.

Each fetcher uses `safe_fetch()` so one source failing never aborts the run.
Same for prioritizer/summarizer/notifier: every stage degrades gracefully.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from typing import List
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import REPORTS_DIR, load_sources, settings
from src.delivery.formatter import save_markdown
from src.delivery.whatsapp import WhatsAppDelivery
from src.fetchers.alpha_vantage import AlphaVantageFetcher
from src.fetchers.arabic_sources import ALL_ARABIC_FETCHERS
from src.fetchers.base import BaseFetcher, BaseIndexFetcher
from src.fetchers.finnhub_fetcher import FinnhubFetcher
from src.fetchers.fred_fetcher import FredFetcher
from src.fetchers.ft_fetcher import FTFetcher
from src.fetchers.newsletter_fetcher import NewsletterFetcher
from src.fetchers.rss_fetcher import RSSFetcher
from src.fetchers.tadawul_fetcher import TadawulFetcher
from src.fetchers.wsj_fetcher import WSJFetcher
from src.models import MarketIndex, NewsItem
from src.processors.deduplicator import deduplicate
from src.processors.prioritizer import Prioritizer
from src.processors.summarizer import Summarizer
from src.processors.watchlist_filter import WatchlistFilter
from src.utils.logger import logger


# ----- Fetcher registry built from sources.yaml ------------------------- #


def _build_news_fetchers() -> List[BaseFetcher]:
    """Instantiate all enabled NewsItem fetchers from sources.yaml."""
    sources = load_sources()
    fetchers: List[BaseFetcher] = []

    for entry in sources.get("global_rss", []) or []:
        if entry.get("enabled") and entry.get("url"):
            fetchers.append(RSSFetcher.from_config(entry, category="us_market"))

    arabic_entries = {
        s.get("name"): s
        for s in (sources.get("arabic_scraping", []) or [])
        if s.get("name")
    }
    for cls in ALL_ARABIC_FETCHERS:
        entry = arabic_entries.get(cls.source_name)
        if entry and entry.get("enabled"):
            fetchers.append(cls())

    sub_cfg = sources.get("subscriptions", {}) or {}
    if sub_cfg.get("wsj", {}).get("enabled"):
        fetchers.append(WSJFetcher())
    if sub_cfg.get("ft", {}).get("enabled"):
        fetchers.append(FTFetcher())

    apis = sources.get("apis", {}) or {}
    if apis.get("finnhub", {}).get("enabled"):
        fetchers.append(FinnhubFetcher())

    if (sources.get("official", {}) or {}).get("tadawul", {}).get("enabled"):
        fetchers.append(TadawulFetcher())

    if (sources.get("gmail", {}) or {}).get("enabled"):
        fetchers.append(NewsletterFetcher())

    return fetchers


def _build_index_fetchers() -> List[BaseIndexFetcher]:
    """Instantiate all enabled MarketIndex fetchers from sources.yaml."""
    sources = load_sources()
    apis = sources.get("apis", {}) or {}
    fetchers: List[BaseIndexFetcher] = []

    av_cfg = apis.get("alpha_vantage", {}) or {}
    if av_cfg.get("enabled"):
        fetchers.append(AlphaVantageFetcher(indices=av_cfg.get("indices")))

    fred_cfg = apis.get("fred", {}) or {}
    if fred_cfg.get("enabled"):
        fetchers.append(FredFetcher(series=fred_cfg.get("series")))

    return fetchers


# ----- Pipeline --------------------------------------------------------- #


async def run_once(*, send: bool = True) -> bool:
    """Execute one full pipeline. Returns True on successful WhatsApp delivery
    (or True in dry-run mode when the Markdown archive was written)."""
    start = datetime.now(timezone.utc)
    logger.info(f"Agent run starting at {start.isoformat()}")

    news_fetchers = _build_news_fetchers()
    index_fetchers = _build_index_fetchers()
    logger.info(
        f"Loaded {len(news_fetchers)} news fetchers + "
        f"{len(index_fetchers)} index fetchers"
    )

    # 1. Fetch in parallel — every fetcher's safe_fetch swallows its own errors.
    news_results, index_results = await asyncio.gather(
        asyncio.gather(*(f.safe_fetch() for f in news_fetchers)),
        asyncio.gather(*(f.safe_fetch() for f in index_fetchers)),
    )
    raw_items: List[NewsItem] = [it for batch in news_results for it in batch]
    indices: List[MarketIndex] = [ix for batch in index_results for ix in batch]
    logger.info(
        f"Fetched {len(raw_items)} news items + {len(indices)} indices"
    )

    if not raw_items and not indices:
        logger.warning("Nothing fetched from any source - skipping delivery")
        return False

    # 2-3. Dedup + watchlist filter
    deduped = deduplicate(raw_items)
    filtered = WatchlistFilter().filter(deduped)

    # 4. Prioritize (Claude). Falls back to first-N if no API key / error.
    prioritized = await Prioritizer(top_n=20).prioritize(filtered)

    # 5. Summarize (Claude, parallel per section).
    report = await Summarizer().summarize(prioritized, indices)

    # 6. Archive Markdown (always, even in dry-run).
    try:
        md_path = save_markdown(report, REPORTS_DIR)
        logger.info(f"Archived report to {md_path}")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Failed to archive Markdown - {exc}")

    # 7. Deliver
    if not send:
        logger.info("Dry-run mode: skipping WhatsApp delivery")
        ok = True
    else:
        ok = await WhatsAppDelivery().send(report)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info(
        f"Agent run finished in {elapsed:.1f}s - delivery "
        f"{'OK' if ok else 'FAILED'}"
    )
    return ok


# ----- Scheduler -------------------------------------------------------- #


async def _scheduler_loop() -> None:
    """Start APScheduler in the current event loop and block forever."""
    tz = ZoneInfo(settings.timezone)
    hour_str, minute_str = settings.schedule_time.split(":")

    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        run_once,
        CronTrigger(hour=int(hour_str), minute=int(minute_str), timezone=tz),
        name="daily_brief",
        misfire_grace_time=3600,  # if the host was asleep, still fire within 1h
        coalesce=True,
    )
    scheduler.start()

    next_run = scheduler.get_job("daily_brief").next_run_time
    logger.info(
        f"Scheduler armed: '{settings.schedule_time}' {settings.timezone} daily. "
        f"Next run at {next_run.isoformat()}"
    )

    try:
        # Block forever; AsyncIOScheduler fires the job on this loop when due.
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        scheduler.shutdown()


def run_scheduled() -> None:
    """Daemon entrypoint. Ctrl-C exits cleanly."""
    try:
        asyncio.run(_scheduler_loop())
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")


# ----- CLI -------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="financial-news-agent",
        description="Daily financial news brief — fetch, prioritize, summarize, deliver.",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help=(
            f"Run as a daemon, firing daily at {settings.schedule_time} "
            f"{settings.timezone}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run the full pipeline and archive Markdown, but do NOT send "
            "via Twilio WhatsApp."
        ),
    )
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
        return 0

    ok = asyncio.run(run_once(send=not args.dry_run))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
