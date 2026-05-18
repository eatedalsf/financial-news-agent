"""Inbound-message handler: routes a WhatsApp message to Claude and returns a reply.

The handler is intentionally thin — it owns:
  - The financial-advisor system prompt (cached so daily chatter is cheap).
  - The latest report injected into the system prompt as `<latest_brief>`.
  - The 1500-char trim so replies always fit one WhatsApp message.

Persistence (per-sender history, rate limits) is out of scope here; add a store
in `src/chat/` and pass it through `ChatHandler.__init__` if you need it.
"""

from typing import List, Optional

from anthropic import AsyncAnthropic

from src.chat.context import load_latest_report, report_age_days
from src.config import settings
from src.utils.logger import logger

# WhatsApp's per-message hard cap. Leave headroom so trim never produces 1601.
WHATSAPP_REPLY_MAX = 1500

_BASE_SYSTEM_PROMPT = """You are a senior financial-markets assistant chatting with a sophisticated investor over WhatsApp. The user already receives a daily morning brief; this is a back-and-forth follow-up channel.

STYLE:
- Tight, factual, financial-journalism register. No filler ("notably", "in conclusion", "I hope this helps"). No marketing voice.
- Lead with the answer, then the supporting numbers/context. Pyramid structure.
- One decimal place for percentages (4.3%), thousands separator for index values (5,420), spell out names on first mention (S&P 500, then SPX).
- When the user asks about something covered in <latest_brief>, draw on it and cite the source short-name in square brackets (e.g., [WSJ], [Reuters]) the same way the brief does.
- Hard cap: 1500 characters total. Be concise. Use short paragraphs or bullets (•) for scannability on a phone screen.
- If the user asks about something the brief doesn't cover and you have no reliable knowledge: say so plainly. Never invent prices, levels, or quotes.

SCOPE:
- Stocks, equity indices, bonds/yields, FX, commodities, macro data, central banks, earnings, Saudi/Tadawul, geopolitics-as-it-moves-markets.
- Politely decline non-financial requests in one line.

OUTPUT:
- Plain text suitable for WhatsApp. Use *bold* and _italic_ sparingly. Use • for bullets. No Markdown headings, no code fences."""


def _build_system_prompt(latest_report: Optional[str], age_days: Optional[int]) -> str:
    if not latest_report:
        return (
            _BASE_SYSTEM_PROMPT
            + "\n\n<latest_brief>No daily brief has been archived yet. Answer from general knowledge and say so when relevant.</latest_brief>"
        )
    age_note = ""
    if age_days is not None and age_days > 1:
        age_note = f"\n\nNOTE: This brief is {age_days} days old; flag that to the user when citing time-sensitive numbers."
    return (
        f"{_BASE_SYSTEM_PROMPT}{age_note}\n\n"
        f"<latest_brief>\n{latest_report}\n</latest_brief>"
    )


def _trim_reply(text: str, max_chars: int = WHATSAPP_REPLY_MAX) -> str:
    """Trim a reply to fit WhatsApp's single-message cap, preserving word boundaries."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text.rfind(" ", 0, max_chars - 1)
    if cut < max_chars // 2:
        cut = max_chars - 1
    return text[:cut].rstrip() + "…"


class ChatHandler:
    """Claude-backed Q&A handler for a single inbound WhatsApp message."""

    def __init__(self, client: Optional[AsyncAnthropic] = None) -> None:
        if client is not None:
            self.client: Optional[AsyncAnthropic] = client
        elif settings.anthropic_api_key:
            self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        else:
            self.client = None

    async def handle(self, message: str, sender: str = "unknown") -> str:
        """Return a WhatsApp-ready reply (≤1500 chars) for one inbound message."""
        text = (message or "").strip()
        if not text:
            return "I didn't catch any text — try sending your question again."

        if self.client is None:
            logger.warning("chat.handler: ANTHROPIC_API_KEY not set, returning stub reply")
            return (
                "The chat assistant isn't configured yet (missing ANTHROPIC_API_KEY). "
                "Your morning brief will still arrive on schedule."
            )

        report = load_latest_report()
        age = report_age_days()
        system_prompt = _build_system_prompt(report, age)

        logger.info(
            "chat.handler: in={sender!r} len={length} report={has_report}",
            sender=sender,
            length=len(text),
            has_report=report is not None,
        )

        try:
            msg = await self.client.messages.create(
                model=settings.claude_model,
                max_tokens=800,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        # Prompt is identical across all messages for one day's brief,
                        # so caching cuts the per-message cost meaningfully.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": text}],
            )
        except Exception as exc:  # noqa: BLE001 - log+gracefully degrade for the user
            logger.exception(f"chat.handler: Claude call failed - {exc}")
            return "Sorry, I hit an error reaching the model. Try again in a minute."

        reply = _extract_text(msg.content).strip()
        if not reply:
            return "I don't have a confident answer for that. Try rephrasing or asking about a specific ticker."
        return _trim_reply(reply)


def _extract_text(content_blocks: List[object]) -> str:
    """Concatenate `text` from Anthropic content blocks, ignoring other block types."""
    return "".join(b.text for b in content_blocks if hasattr(b, "text"))
