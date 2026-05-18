"""Watchlist filter: narrow items to user's interests, drop ignored topics.

Rules:
  - All watchlist sections empty AND `ignored_topics` empty → pass-through.
  - `ignored_topics` ALWAYS drops a matching item, even if it also matches an include term.
  - Include terms (stocks/sectors/keywords) → keep matching items.
  - If only `ignored_topics` is populated, keep everything except matches.
  - Matching is case-insensitive substring against title + summary + content + source.
"""

from typing import Any, Dict, Iterable, List, Optional

from src.config import load_watchlist
from src.models import NewsItem
from src.utils.logger import logger


class WatchlistFilter:
    """Apply user-configured watchlist + ignore-list rules to a NewsItem stream.

    Pass `config` to inject a dict directly (used in tests). When `config` is
    None, the watchlist is loaded from `config/watchlist.yaml` via `load_watchlist()`.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config if config is not None else load_watchlist()
        stocks = cfg.get("stocks", {}) or {}
        self.stocks_us = self._normalize(stocks.get("us", []) or [])
        self.stocks_saudi = self._normalize(stocks.get("saudi", []) or [])
        self.sectors = self._normalize(cfg.get("sectors", []) or [])
        self.keywords = self._normalize(cfg.get("keywords", []) or [])
        self.ignored = self._normalize(cfg.get("ignored_topics", []) or [])

        self._include_terms: List[str] = (
            self.stocks_us + self.stocks_saudi + self.sectors + self.keywords
        )

    def is_pass_through(self) -> bool:
        """True if no rules configured — filter has no effect."""
        return not self._include_terms and not self.ignored

    def filter(self, items: List[NewsItem]) -> List[NewsItem]:
        """Return items satisfying the watchlist rules."""
        if self.is_pass_through():
            return items

        kept: List[NewsItem] = []
        dropped_ignored = 0
        dropped_no_match = 0
        for it in items:
            haystack = self._haystack(it)
            if self.ignored and self._matches_any(haystack, self.ignored):
                dropped_ignored += 1
                continue
            if not self._include_terms:
                # Only ignore list is configured → everything not ignored passes.
                kept.append(it)
                continue
            if self._matches_any(haystack, self._include_terms):
                kept.append(it)
            else:
                dropped_no_match += 1

        if dropped_ignored or dropped_no_match:
            logger.info(
                f"watchlist: {len(items)} -> {len(kept)} "
                f"(ignored={dropped_ignored}, off-watchlist={dropped_no_match})"
            )
        return kept

    @staticmethod
    def _normalize(terms: Iterable[object]) -> List[str]:
        return [str(t).strip().lower() for t in terms if str(t).strip()]

    @staticmethod
    def _haystack(item: NewsItem) -> str:
        return " ".join(
            [item.title, item.summary or "", item.content or "", item.source]
        ).lower()

    @staticmethod
    def _matches_any(haystack: str, terms: List[str]) -> bool:
        return any(t in haystack for t in terms)
