"""Prioritize news items using Claude. Returns the top-N most important.

The system prompt is long-ish and identical across runs, so it is sent with
`cache_control: ephemeral`. Within a single day's run this is the only call
to the prioritizer, so the cache primarily helps if the user reruns the agent
within the 5-minute TTL.
"""

import json
from typing import Any, List

from anthropic import AsyncAnthropic

from src.config import settings
from src.models import NewsItem
from src.utils.logger import logger

# Long enough to clear the prompt-caching minimum-tokens threshold for Opus.
_SYSTEM_PROMPT = """You are a senior financial-news editor preparing a morning brief for a US-based investor who has significant interests in BOTH US markets and the Saudi/Gulf region. The reader is sophisticated: a buy-side analyst or a private investor with a real portfolio. They have 5 minutes to read the brief before their day starts. Your job: from a candidate list of news items, choose the stories the reader MUST know today.

SELECTION CRITERIA, in priority order:

1. Market-moving events. Anything that materially moves equity, bond, or commodity prices:
   - Central bank rate decisions and FOMC commentary.
   - Major corporate earnings (mega-cap or sector bellwethers; ignore small-cap).
   - M&A announcements over $1B, regulatory blocks/approvals, sanctions, anti-trust.
   - Geopolitical shocks with direct market impact (war, oil supply disruption, tariffs, election results).
   - Surprise macro prints (CPI, NFP, GDP) that beat or miss consensus.

2. Macroeconomic indicators with policy implications.
   - Inflation, unemployment, fed funds, treasury yields.
   - Oil and energy supply/demand shifts, OPEC actions.
   - Currency moves outside normal range.

3. Saudi/Gulf-specific:
   - TASI moves, Tadawul disclosures from major issuers (Aramco, Al-Rajhi, SABIC, etc.).
   - Saudi macro: PIF moves, Vision 2030 milestones, oil revenue, NEOM-related news.
   - Regional events affecting Gulf markets (UAE, Qatar) when material.

4. Breaking news in flight with material consequences for the day's trading.

DE-PRIORITIZE (keep only if no better alternative):
- Opinion pieces, "explainers", "5 things to know", "lifestyle" articles.
- Repetitive coverage: if 3 sources report the same story, you only need 1 (pick the most authoritative — Reuters > Yahoo, WSJ > MarketWatch).
- Low-impact corporate updates (small-cap earnings, minor product launches).
- Sports, entertainment, celebrity finance, crypto-without-macro-context.

CROSS-LANGUAGE NOTE:
- Items may have Arabic titles and summaries (from Argaam, Mubasher, Al-Eqtisadiah, CNBC Arabia, Asharq, Tadawul). Treat them with the same priority as English items — these are the user's primary Saudi-market signal.

OUTPUT — STRICT JSON, no markdown, no commentary outside the JSON:
{
  "selected_ids": ["id1", "id2", ...],
  "reasoning": "one sentence summarizing what made the cut and what notably did not"
}

The selected_ids array MUST:
- Contain at most %d items.
- Be ordered by importance (most important first).
- Only include ids that appear verbatim in the input list."""


class Prioritizer:
    """Ask Claude to pick the top-N most important items from a candidate list."""

    def __init__(self, top_n: int = 20) -> None:
        self.top_n = top_n
        self.client = (
            AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key
            else None
        )

    async def prioritize(self, items: List[NewsItem]) -> List[NewsItem]:
        if not items:
            return []
        if self.client is None:
            logger.warning(
                "prioritizer: ANTHROPIC_API_KEY not set, "
                f"returning first {self.top_n} items unranked"
            )
            return items[: self.top_n]
        if len(items) <= self.top_n:
            return items

        payload = [
            {
                "id": it.id,
                "title": it.title,
                "source": it.source,
                "category": it.category,
                "language": it.language,
                "summary": (it.summary or "")[:400],
            }
            for it in items
        ]

        try:
            response = await self._call_claude(payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                f"prioritizer: Claude call failed, falling back to first "
                f"{self.top_n} items - {exc}"
            )
            return items[: self.top_n]

        selected_ids = response.get("selected_ids", [])
        by_id = {it.id: it for it in items}
        ordered = [by_id[i] for i in selected_ids if i in by_id]
        if not ordered:
            logger.warning(
                "prioritizer: Claude returned no valid ids, "
                f"falling back to first {self.top_n}"
            )
            return items[: self.top_n]

        reasoning = response.get("reasoning", "")
        logger.info(
            f"prioritizer: selected {len(ordered)}/{len(items)} - {reasoning}"
        )
        return ordered[: self.top_n]

    async def _call_claude(self, payload: list) -> dict:
        assert self.client is not None
        msg = await self.client.messages.create(
            model=settings.claude_model,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT % self.top_n,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            ],
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        return self._extract_json(text)

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Parse the first balanced JSON object from Claude's output."""
        text = text.strip()
        if text.startswith("```"):
            # Strip ```json ... ``` markdown fences if present
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
