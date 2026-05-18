# CLAUDE.md

Guidance for Claude Code sessions working in this repo.

## Project overview

Daily Financial News Agent. Runs at 07:00 `America/Chicago`, pulls news from 15+ sources, dedupes/filters/prioritizes/summarizes via Claude (`claude-opus-4-7`), and delivers one WhatsApp message via Twilio. Also archives a Markdown copy in `logs/reports/`.

**Platform:** Windows. All commands assume PowerShell. Use `Path` objects (never hard-code `/`).
**Language:** code, comments, chat are all English.
**Claude model:** `claude-opus-4-7` (set in `src/config.py` as `settings.claude_model`).

## Architecture in one paragraph

`main.py` orchestrates `Fetchers → Processors → Delivery`. Every fetcher inherits `BaseFetcher` (async, has `safe_fetch()` that swallows exceptions so one bad source doesn't kill the run). Every delivery channel inherits `BaseDelivery`. Processors are plain functions/classes that transform `list[NewsItem] → Report`. Config is loaded once at import time into `settings` (env) plus `load_sources()` / `load_watchlist()` (YAML).

## Common commands

```powershell
# Activate venv (do this in every new shell)
.venv\Scripts\Activate.ps1

# Install / update deps
pip install -r requirements.txt

# Run the agent once
python -m src.main

# Run tests
pytest

# Run a single test file
pytest tests/test_rss_fetcher.py -v
```

> There is no lint config yet; if you add one, prefer `ruff` (fast, single tool).

## How to add a new source

1. Create `src/fetchers/<source>_fetcher.py` with a class inheriting `BaseFetcher`.
2. Set `source_name` and implement `async def fetch(self) -> list[NewsItem]`.
3. Add an entry under the appropriate section of `config/sources.yaml`.
4. Wire it into the fetcher registry in `src/main.py` (added in Phase 6).
5. Add a `tests/test_<source>_fetcher.py` with at least one fixture-based test (no live network in CI).

**Picking a category** for `NewsItem.category`: `us_market | saudi_market | macro | subscription | newsletter`. Keep this set small — the prioritizer's prompt depends on it.

## How to edit the watchlist

Edit `config/watchlist.yaml`. Empty lists = pass everything. Anything in `ignored_topics` drops items even if they match. Changes take effect on the next run; no restart of the scheduler needed (config is re-read each run).

## Phase status

- ✅ Phase 1 — Foundation (structure, models, config, logger, base classes, docs)
- ✅ Phase 2 — Basic fetchers (RSS, Arabic scraping, Alpha Vantage, FRED)
- ✅ Phase 3 — Advanced fetchers (WSJ, FT, Gmail, Finnhub, Tadawul)
- ✅ Phase 4 — Processors (dedup, filter, prioritize, summarize)
- ✅ Phase 5 — Delivery (formatter, Twilio WhatsApp)
- ✅ Phase 6 — Orchestration (main + APScheduler)
- ✅ Phase 7 — Tests + Windows Task Scheduler deployment

When advancing a phase, update this checklist and the matching note in `README.md`.

## Conventions

- **Async everywhere in I/O paths.** Fetchers and delivery use `async def`. Processors are sync unless they need to call Claude.
- **Type hints on all functions.** Pydantic models for any data crossing module boundaries.
- **Logging.** `from src.utils.logger import logger`. Use `logger.exception(...)` inside `except` blocks so tracebacks land in the log file.
- **Prompt caching.** Claude calls in `processors/prioritizer.py` and `processors/summarizer.py` MUST use `cache_control` on the system prompt — same system prompt runs daily, caching cuts cost meaningfully.
- **Secrets.** Only read via `src.config.settings`. Never `os.getenv(...)` outside `config.py`.
- **Time.** Always timezone-aware (`datetime.now(ZoneInfo("America/Chicago"))`). Never naive datetimes.

## Deployment notes

- Local dev: just `python -m src.main`.
- Production on Windows: Task Scheduler (instructions in README Phase-7 section).
- APScheduler-based continuous mode also works — useful if you want to leave a terminal open while testing schedule logic.
- The `.venv\` path is hard-coded into the Task Scheduler action; if you move the project, update that action.

## Gotchas for future-Claude

- **Bloomberg** is intentionally `enabled: false` in `sources.yaml` — no usable public RSS exists. Don't "fix" this without confirming the approach with the user.
- **WSJ/FT cookies expire.** If the fetcher starts returning paywalled HTML, the fix is almost always "user needs to re-export cookies", not a code change.
- **Twilio Sandbox sessions expire after 72h** of inactivity. If WhatsApp delivery silently stops in dev, that's usually why.
- **Windows + zoneinfo:** the `tzdata` package is in `requirements.txt` precisely so `ZoneInfo("America/Chicago")` works on Windows. Don't remove it.
- **Don't translate inside the fetcher.** Fetchers store original-language text in `NewsItem.content` and set `language`. Translation (Arabic → English, since `REPORT_LANGUAGE=english`) happens in `processors/summarizer.py` via Claude.
