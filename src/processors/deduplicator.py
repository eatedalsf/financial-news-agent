"""Deduplicate news items by URL and fuzzy title match.

Strategy:
  1. Exact URL match: collapse to one entry, keep the one with the longest content.
  2. Fuzzy title match: two-stage so we get good recall without O(N^2) cost on
     a slow metric.
       - Stage 2a (cheap): Jaccard token overlap as a pre-filter. If < 0.2 the
         pair is definitely not a duplicate; skip the expensive metric.
       - Stage 2b (precise): `difflib.SequenceMatcher.ratio()` on normalized
         title strings. Sequence-aware, so it catches word-substitution variants
         like "Fed Holds Rates" vs "Federal Reserve holds rates". Threshold
         defaults to 0.6.

Pure-Python; no extra dependencies. The Jaccard pre-filter keeps this fast
for the 200-500 items we expect after fetching.
"""

import re
from difflib import SequenceMatcher
from typing import List, Set

from src.models import NewsItem
from src.utils.logger import logger

# Common English stop-words plus a handful of news-headline filler that
# otherwise inflates Jaccard similarity between unrelated items.
_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "as", "is", "are", "was", "were", "be", "by", "from", "that", "this",
    "it", "its", "has", "have", "had", "will", "would", "could", "should", "may",
    "says", "say", "said", "new", "more", "after", "before", "amid", "over",
}

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def deduplicate(
    items: List[NewsItem],
    title_threshold: float = 0.6,
) -> List[NewsItem]:
    """Drop duplicates by URL (exact) and title (fuzzy).

    For each conflict, keep the item with the most informational content
    (longest `content` + `summary`). Returns a new list; input is untouched.
    """
    if not items:
        return []

    # Stage 1: collapse exact URL duplicates
    by_url: dict[str, NewsItem] = {}
    for it in items:
        prev = by_url.get(it.url)
        if prev is None or _info_length(it) > _info_length(prev):
            by_url[it.url] = it
    stage1 = list(by_url.values())

    # Stage 2: fuzzy title dedup (Jaccard pre-filter → SequenceMatcher precise)
    kept: List[NewsItem] = []
    kept_tokens: List[Set[str]] = []
    kept_norms: List[str] = []
    dropped = 0
    for item in stage1:
        tokens = _tokenize(item.title)
        norm = " ".join(sorted(tokens))  # stable normalized form for SequenceMatcher
        match_idx = None
        for idx, prev_tokens in enumerate(kept_tokens):
            if _jaccard(tokens, prev_tokens) < 0.2:
                continue  # cheap reject — definitely not a duplicate
            if SequenceMatcher(None, norm, kept_norms[idx]).ratio() >= title_threshold:
                match_idx = idx
                break
        if match_idx is None:
            kept.append(item)
            kept_tokens.append(tokens)
            kept_norms.append(norm)
            continue
        # Conflict: keep the more informative version
        if _info_length(item) > _info_length(kept[match_idx]):
            kept[match_idx] = item
            kept_tokens[match_idx] = tokens
            kept_norms[match_idx] = norm
        dropped += 1

    if dropped or len(stage1) != len(items):
        logger.info(
            f"deduplicator: {len(items)} -> {len(kept)} "
            f"(url-dup={len(items) - len(stage1)}, title-dup={dropped})"
        )
    return kept


def _tokenize(title: str) -> Set[str]:
    """Lowercase, strip punctuation, drop stop-words, return token set."""
    norm = _PUNCT_RE.sub(" ", title.lower())
    return {t for t in norm.split() if len(t) > 2 and t not in _STOP_WORDS}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _info_length(item: NewsItem) -> int:
    return len(item.content or "") + len(item.summary or "")
