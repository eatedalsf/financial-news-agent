"""Summarize prioritized news items into a Report via Claude.

One call per section, in parallel. The system prompt is identical across all
calls and marked `cache_control: ephemeral` so calls 2-N hit the cache once
call 1 lands (best-effort — under simultaneous fire some may race and write).

Section mapping:
  us_market     ← items with category=us_market + Alpha Vantage indices
  saudi_market  ← items with category=saudi_market (Arabic sources + Tadawul)
  global_macro  ← items with category=macro + FRED macro series
  subscriptions ← items with category=subscription (WSJ + FT)
  newsletters   ← items with category=newsletter (Gmail)
  watch_today   ← inferred by Claude from the full prioritized item list
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from src.config import settings
from src.models import MarketIndex, NewsItem, Report, ReportSection
from src.utils.logger import logger

SECTION_TITLES: Dict[str, str] = {
    "us_market": "🇺🇸 US Market",
    "saudi_market": "🇸🇦 Saudi Market",
    "global_macro": "🌍 Global Macro",
    "subscriptions": "📰 From Your Subscriptions",
    "newsletters": "📧 From Your Newsletters",
    "watch_today": "🎯 Watch Today",
}

# FRED series ids route to the global_macro section regardless of source.
_MACRO_INDEX_SYMBOLS = {"CPIAUCSL", "UNRATE", "DFF", "DGS10"}


def _build_system_prompt(report_language: str) -> str:
    """Compose the system prompt with the user's preferred output language."""
    if report_language == "english":
        lang_directive = (
            "OUTPUT LANGUAGE: English only. Translate any Arabic titles, summaries, "
            "or content into clear, natural English. Keep proper nouns in their "
            "standard English form (e.g., 'Aramco', 'PIF', 'Tadawul')."
        )
    elif report_language == "arabic":
        lang_directive = (
            "OUTPUT LANGUAGE: Arabic only. Translate any English content into "
            "clear Modern Standard Arabic."
        )
    else:  # mixed
        lang_directive = (
            "OUTPUT LANGUAGE: Keep each item in its source language. The output "
            "report may mix Arabic and English content."
        )

    return f"""You are a senior financial-news editor producing a daily morning brief for a sophisticated investor. Each user message asks you to write ONE section of the brief.

{lang_directive}

WRITING STYLE:
- Tight, factual, financial-journalism register. No filler words ("notably", "interestingly", "in conclusion"). No marketing voice.
- Lead each paragraph with what happened, then the number/consequence. Pyramid structure.
- Cite source names inline in square brackets at the end of the relevant sentence, e.g., "...the Fed held rates [Reuters]." Use the short source name (reuters, wsj, ft, argaam, mubasher, finnhub, etc.).
- Numbers: one decimal place for percentages (4.3%), thousands separator for index values (5,420), full names spelled out the first time (S&P 500, then SPX is fine).
- 3-5 sentences for the narrative paragraph, optionally followed by a short bullet list (3-5 bullets) of the most important specific items.
- If the section has no material content, output exactly one line: "Nothing material today."

INPUT:
Each user message is a JSON object with:
  - section: one of us_market, saudi_market, global_macro, subscriptions, newsletters, watch_today
  - indices: array of MarketIndex objects (may be empty)
  - items: array of NewsItem objects with title/source/summary/content

SECTION-SPECIFIC GUIDANCE:
- us_market: Open with the index moves (if present). Then narrate the top stories. Bullet list highlights the top 3-5 individual stories.
- saudi_market: TASI and major Tadawul disclosures first, then macro/Aramco/PIF news.
- global_macro: Lead with the headline macro number (CPI, fed funds, 10Y). Cover oil, currencies, central banks.
- subscriptions: WSJ and FT highlights side-by-side. 3 items each as bullets is ideal.
- newsletters: Synthesize across all newsletters — find common themes, don't list one newsletter per bullet.
- watch_today: Scan the items for explicit upcoming events (earnings calls, central bank meetings, data releases, scheduled votes). 3-5 bullets max. If none, "Nothing material today."

OUTPUT FORMAT:
Plain Markdown for the section BODY only. No section title (the orchestrator adds it). Start directly with the narrative or "Nothing material today." Do not add concluding remarks. Do not invent facts not present in the payload."""


class Summarizer:
    """Produce per-section narratives via Claude (parallel calls + cached prompt)."""

    def __init__(self) -> None:
        self.client: Optional[AsyncAnthropic] = (
            AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key
            else None
        )
        self.system_prompt = _build_system_prompt(settings.report_language)

    async def summarize(
        self,
        items: List[NewsItem],
        indices: List[MarketIndex],
    ) -> Report:
        """Produce a full Report from prioritized items + market indices."""
        sections = self._group(items, indices)

        if self.client is None:
            logger.warning(
                "summarizer: ANTHROPIC_API_KEY not set, returning raw sections"
            )
            return self._assemble(sections, summaries={}, item_count=len(items))

        # Parallel section calls. Same system prompt → calls 2-N may hit cache.
        tasks = {
            sec_id: asyncio.create_task(self._summarize_section(sec_id, payload))
            for sec_id, payload in sections.items()
        }
        raw_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        summaries: Dict[str, Optional[str]] = {}
        for sec_id, result in zip(tasks.keys(), raw_results):
            if isinstance(result, BaseException):
                logger.exception(
                    f"summarizer: section '{sec_id}' failed - {result}"
                )
                summaries[sec_id] = None
            else:
                summaries[sec_id] = result

        return self._assemble(sections, summaries, item_count=len(items))

    # ----- Section grouping --------------------------------------------- #

    @staticmethod
    def _group(
        items: List[NewsItem],
        indices: List[MarketIndex],
    ) -> Dict[str, Dict[str, Any]]:
        """Bucket items and split indices into US-equity vs macro."""
        us_indices = [i for i in indices if i.symbol not in _MACRO_INDEX_SYMBOLS]
        macro_indices = [i for i in indices if i.symbol in _MACRO_INDEX_SYMBOLS]

        groups: Dict[str, Dict[str, Any]] = {
            "us_market": {"items": [], "indices": us_indices},
            "saudi_market": {"items": [], "indices": []},
            "global_macro": {"items": [], "indices": macro_indices},
            "subscriptions": {"items": [], "indices": []},
            "newsletters": {"items": [], "indices": []},
            # watch_today reads the full prioritized list (cross-section lookahead)
            "watch_today": {"items": list(items), "indices": []},
        }
        for it in items:
            cat = it.category or "us_market"
            if cat == "saudi_market":
                groups["saudi_market"]["items"].append(it)
            elif cat == "macro":
                groups["global_macro"]["items"].append(it)
            elif cat == "subscription":
                groups["subscriptions"]["items"].append(it)
            elif cat == "newsletter":
                groups["newsletters"]["items"].append(it)
            else:  # us_market or unknown
                groups["us_market"]["items"].append(it)
        return groups

    # ----- Claude call -------------------------------------------------- #

    async def _summarize_section(self, sec_id: str, payload: Dict[str, Any]) -> str:
        items: List[NewsItem] = payload["items"]
        indices: List[MarketIndex] = payload["indices"]

        if not items and not indices:
            return "Nothing material today."

        user_msg = {
            "section": sec_id,
            "indices": [i.model_dump(mode="json") for i in indices],
            "items": [
                {
                    "id": it.id,
                    "title": it.title,
                    "source": it.source,
                    "language": it.language,
                    "summary": (it.summary or "")[:600],
                    "content": (it.content or "")[:1500],
                }
                for it in items[:20]
            ],
        }

        assert self.client is not None
        msg = await self.client.messages.create(
            model=settings.claude_model,
            max_tokens=800,
            system=[
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(user_msg, ensure_ascii=False, default=str),
                }
            ],
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
        return text or "Nothing material today."

    # ----- Report assembly ---------------------------------------------- #

    @staticmethod
    def _assemble(
        sections: Dict[str, Dict[str, Any]],
        summaries: Dict[str, Optional[str]],
        item_count: int,
    ) -> Report:
        def section(sec_id: str) -> ReportSection:
            payload = sections[sec_id]
            return ReportSection(
                title=SECTION_TITLES[sec_id],
                indices=payload.get("indices", []),
                items=payload.get("items", [])[:10],
                summary=summaries.get(sec_id),
            )

        return Report(
            date=datetime.now(timezone.utc),
            us_market=section("us_market"),
            saudi_market=section("saudi_market"),
            global_macro=section("global_macro"),
            subscriptions=section("subscriptions"),
            newsletters=section("newsletters"),
            watch_today=section("watch_today"),
            raw_item_count=item_count,
        )
