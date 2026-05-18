"""Tests for src/chat/handler.py and src/chat/context.py."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock

import pytest

from src.chat import context as ctx_mod
from src.chat import handler as handler_mod  # for monkeypatching post-import bindings
from src.chat.handler import (
    WHATSAPP_REPLY_MAX,
    ChatHandler,
    _build_system_prompt,
    _trim_reply,
)


def _patch_context(monkeypatch, report: object, age: object) -> None:
    """Patch the context loaders where the handler binds them (post-import)."""
    monkeypatch.setattr(handler_mod, "load_latest_report", lambda: report)
    monkeypatch.setattr(handler_mod, "report_age_days", lambda: age)


# ----- Fakes ----------------------------------------------------------- #


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content: List[_FakeTextBlock] = [_FakeTextBlock(text)]


def _fake_client(reply_text: str) -> AsyncMock:
    """Build an AsyncAnthropic stand-in that returns `reply_text` for any call."""
    client = AsyncMock()
    client.messages = AsyncMock()
    client.messages.create = AsyncMock(return_value=_FakeMessage(reply_text))
    return client


# ----- _trim_reply ----------------------------------------------------- #


def test_trim_short_reply_passes_through():
    assert _trim_reply("Hello.") == "Hello."


def test_trim_long_reply_caps_at_limit():
    long = "word " * 1000  # 5000 chars
    trimmed = _trim_reply(long)
    assert len(trimmed) <= WHATSAPP_REPLY_MAX
    assert trimmed.endswith("…")


def test_trim_prefers_word_boundary():
    # Build a string whose last word straddles the cap so the cut moves backwards.
    text = ("alpha bravo charlie delta " * 100).rstrip()
    trimmed = _trim_reply(text, max_chars=50)
    # Trim must not slice a word in half — ellipsis is the only mid-word char.
    assert trimmed.endswith("…")
    assert " " in trimmed  # ended at a word boundary, not a forced char cut


# ----- _build_system_prompt ------------------------------------------- #


def test_system_prompt_without_report_explains_missing_brief():
    prompt = _build_system_prompt(None, None)
    assert "No daily brief has been archived yet" in prompt
    assert "<latest_brief>" in prompt


def test_system_prompt_with_fresh_report_embeds_body():
    body = "# Daily Brief — 2026-05-18\n\nSPX up 0.3%."
    prompt = _build_system_prompt(body, age_days=0)
    assert "SPX up 0.3%" in prompt
    assert "days old" not in prompt  # fresh, no staleness warning


def test_system_prompt_warns_when_report_is_stale():
    prompt = _build_system_prompt("body", age_days=4)
    assert "4 days old" in prompt


# ----- ChatHandler.handle --------------------------------------------- #


@pytest.mark.asyncio
async def test_handle_empty_message_returns_prompt(monkeypatch):
    _patch_context(monkeypatch, None, None)

    handler = ChatHandler(client=_fake_client("unused"))
    reply = await handler.handle("   ", sender="whatsapp:+15551234567")
    assert "didn't catch any text" in reply


@pytest.mark.asyncio
async def test_handle_without_anthropic_key_returns_stub():
    handler = ChatHandler(client=None)  # no client wired
    # Force the auto-init path to find no key
    handler.client = None
    reply = await handler.handle("What's the SPX?", sender="x")
    assert "isn't configured" in reply.lower()


@pytest.mark.asyncio
async def test_handle_calls_claude_and_returns_trimmed_reply(monkeypatch):
    _patch_context(monkeypatch, "# Brief\nSPX up.", 0)

    client = _fake_client("SPX closed at 5,421. Tech led [WSJ].")
    handler = ChatHandler(client=client)

    reply = await handler.handle("How did SPX do?", sender="whatsapp:+1555")

    assert "SPX closed at 5,421" in reply
    assert len(reply) <= WHATSAPP_REPLY_MAX
    # The handler must inject the report into the system prompt.
    call_kwargs = client.messages.create.call_args.kwargs
    system_blocks = call_kwargs["system"]
    assert any("SPX up." in b["text"] for b in system_blocks)
    # Prompt caching is required by the project conventions.
    assert system_blocks[0].get("cache_control") == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_handle_trims_over_long_claude_reply(monkeypatch):
    _patch_context(monkeypatch, None, None)

    long_text = "alpha " * 1000  # ~6000 chars
    handler = ChatHandler(client=_fake_client(long_text))
    reply = await handler.handle("Tell me everything.", sender="x")

    assert len(reply) <= WHATSAPP_REPLY_MAX
    assert reply.endswith("…")


@pytest.mark.asyncio
async def test_handle_returns_friendly_error_when_claude_raises(monkeypatch):
    _patch_context(monkeypatch, None, None)

    client = AsyncMock()
    client.messages = AsyncMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("network blip"))

    handler = ChatHandler(client=client)
    reply = await handler.handle("Hi", sender="x")
    assert "error" in reply.lower()


@pytest.mark.asyncio
async def test_handle_returns_fallback_when_claude_replies_empty(monkeypatch):
    _patch_context(monkeypatch, None, None)

    handler = ChatHandler(client=_fake_client("   "))
    reply = await handler.handle("Vague question?", sender="x")
    assert "confident answer" in reply.lower()


# ----- context loader -------------------------------------------------- #


def test_latest_report_path_returns_none_when_dir_missing(tmp_path: Path):
    assert ctx_mod.latest_report_path(tmp_path / "nope") is None


def test_latest_report_path_picks_newest_filename(tmp_path: Path):
    (tmp_path / "2025-01-01.md").write_text("old", encoding="utf-8")
    (tmp_path / "2026-05-18.md").write_text("new", encoding="utf-8")
    (tmp_path / "2026-05-17.md").write_text("mid", encoding="utf-8")
    latest = ctx_mod.latest_report_path(tmp_path)
    assert latest is not None
    assert latest.name == "2026-05-18.md"


def test_load_latest_report_returns_file_body(tmp_path: Path):
    (tmp_path / "2026-05-18.md").write_text("# Brief\nBody.", encoding="utf-8")
    assert ctx_mod.load_latest_report(tmp_path) == "# Brief\nBody."


def test_load_latest_report_none_when_empty_dir(tmp_path: Path):
    assert ctx_mod.load_latest_report(tmp_path) is None


def test_load_latest_report_truncates_oversized_file(tmp_path: Path):
    big = "x" * 25_000
    (tmp_path / "2026-05-18.md").write_text(big, encoding="utf-8")
    out = ctx_mod.load_latest_report(tmp_path)
    assert out is not None
    assert "[... report truncated for context ...]" in out


def test_report_age_days_returns_zero_for_today(tmp_path: Path):
    today_name = f"{date.today().isoformat()}.md"
    (tmp_path / today_name).write_text("ok", encoding="utf-8")
    assert ctx_mod.report_age_days(tmp_path) == 0


def test_report_age_days_returns_positive_for_old_report(tmp_path: Path):
    old = (datetime.now() - timedelta(days=5)).date().isoformat()
    (tmp_path / f"{old}.md").write_text("ok", encoding="utf-8")
    assert ctx_mod.report_age_days(tmp_path) == 5


def test_report_age_days_none_when_missing(tmp_path: Path):
    assert ctx_mod.report_age_days(tmp_path) is None
