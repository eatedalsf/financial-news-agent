# Financial News Agent

A daily, two-way financial-news service for a single user.

- **Morning brief.** Every day at 07:00 `America/Chicago` the agent pulls news from 15 sources, deduplicates it, prioritizes it with Claude, summarizes six sections in parallel, archives the result as Markdown, and delivers it over WhatsApp as a short, bullet-formatted message per section.
- **Q&A back-channel.** A FastAPI webhook turns WhatsApp into a chat: the user texts a question, Claude answers using that morning's archived brief as context, the reply lands in the same thread.

Built for Windows with PowerShell. Targets Claude Opus 4.7 (`claude-opus-4-7`) end-to-end with prompt caching on every Claude call.

---

## How it works

```
fetch (parallel, safe)  →  dedup  →  watchlist  →  prioritize (Claude)  →  summarize (Claude, parallel per section)
                                                                                        ↓
                                                                       archive Markdown  +  WhatsApp chunks
                                                                                                ↓
                                                            (Q&A chat reads the latest archive as context)
```

- **Fetch** — 15 sources run concurrently; each fetcher's `safe_fetch()` swallows its own errors so one source down never aborts a run.
- **Dedup** — exact URL match, then fuzzy title match (Jaccard pre-filter + `SequenceMatcher`) to collapse e.g. "Fed Holds Rates" / "Federal Reserve holds rates" into one item; keeps the version with the most content.
- **Watchlist** — pass-through when `config/watchlist.yaml` is empty; otherwise keep only items matching tickers / sectors / keywords, and always drop `ignored_topics`.
- **Prioritize** — Claude picks the top 20 most market-moving items using a long, cached system prompt. Falls back to first-20 on API error or missing key.
- **Summarize** — six section calls in parallel (US, Saudi, Macro, Subscriptions, Newsletters, Watch Today), same cached system prompt. Each section is 3–5 sentences plus 3–5 bullets, with `[source]` citations and source-language translation when `REPORT_LANGUAGE=english`.
- **Archive** — full Markdown to `logs/reports/YYYY-MM-DD.md`. Doubles as context for the chat webhook.
- **Deliver** — WhatsApp via Twilio: one short message per section, indices on their own line with 📈/📉 trend emojis, summary lines normalized to `•` bullets. Auto-splits long sections on bullet boundaries with a `_(cont.)_` marker; exponential-backoff retry on 5xx/network errors.

## Sources actually wired up

| Category | Sources | Mechanism |
|---|---|---|
| US RSS | Reuters, CNBC, MarketWatch, Yahoo Finance, Axios Markets | `feedparser` |
| Saudi/Arabic scraping | Argaam, Mubasher, Al-Eqtisadiah | `httpx` + `beautifulsoup4` (lxml) |
| Subscriptions | WSJ markets/economy, FT markets/companies | session cookies (`WSJ_COOKIES`, `FT_COOKIES`) |
| Newsletters | Anything routed into a Gmail label | OAuth + Gmail API |
| Indices | S&P 500, Nasdaq 100, Dow Jones (via SPY/QQQ/DIA ETF proxies) | Alpha Vantage |
| Macro series | CPI (CPIAUCSL), unemployment (UNRATE), fed funds (DFF), 10Y (DGS10) | FRED |
| General news API | US-market wire | Finnhub |
| Saudi disclosures | Tadawul issuer announcements | `saudiexchange.sa` scrape |

Bloomberg is in `config/sources.yaml` but `enabled: false` — they killed their public RSS years ago. The YAML also lists `cnbc_arabia` and `asharq_business`, but no fetcher class is wired for them (left in the file as future stubs).

---

## Requirements

- Windows 10/11
- Python 3.11+ (tested on 3.14.2)
- Git
- Accounts/keys for: Anthropic, Twilio, Alpha Vantage, FRED, Finnhub, Google Cloud (Gmail API)

Every secret is read once via `src/config.py` → `settings`. Never `os.getenv(...)` outside that module.

---

## Setup

### 1. Clone and create a venv

```powershell
git clone https://github.com/eatedalsf/financial-news-agent.git
cd financial-news-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> If PowerShell blocks activation: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.

### 2. Install dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure environment

```powershell
copy .env.example .env
# Edit .env in your editor; fill the keys you have. Empty keys gracefully skip their fetcher.
```

`.env` keys, all read via `src.config.settings`:

| Key | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | prioritizer, summarizer, chat handler |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | WhatsApp delivery + webhook signature check |
| `TWILIO_WHATSAPP_FROM` / `USER_WHATSAPP_TO` | WhatsApp delivery |
| `WSJ_COOKIES` / `FT_COOKIES` | subscription fetchers |
| `GMAIL_CREDENTIALS_PATH` / `GMAIL_TOKEN_PATH` | Gmail OAuth |
| `ALPHA_VANTAGE_API_KEY`, `FRED_API_KEY`, `FINNHUB_API_KEY` | respective fetchers |
| `TIMEZONE`, `SCHEDULE_TIME` | scheduler + report date rendering |
| `REPORT_LANGUAGE` | `english` (translate Arabic), `arabic`, or `mixed` |

### 4. Configure sources and watchlist

- `config/sources.yaml` — toggle `enabled` per source.
- `config/watchlist.yaml` — leave empty for everything, or add `stocks.us`, `stocks.saudi`, `sectors`, `keywords`, `ignored_topics`. Case-insensitive substring match against title + summary + content + source. `ignored_topics` always wins over an include match.

Both files are re-read on every run; no restart needed.

---

## Twilio WhatsApp setup

Same code path works for the **Sandbox** (free, dev) and a production **WhatsApp Business** sender — the From address comes from `TWILIO_WHATSAPP_FROM`.

### Sandbox (recommended to start)

1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message**.
2. Twilio shows a sandbox number (e.g. `+1 415 523 8886`) and a join code like `join sunny-river`.
3. From WhatsApp on your phone, send the join code to the sandbox number.
4. In `.env`:
   ```
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
   USER_WHATSAPP_TO=whatsapp:+<your number in E.164>
   ```
5. Sandbox sessions expire after **72 hours** of inactivity. Re-send `join <code>` to refresh.

### WhatsApp Business sender (production)

1. Complete Twilio's WhatsApp Business onboarding (Meta verification + approved sender).
2. Set `TWILIO_WHATSAPP_FROM=whatsapp:+<your approved business number>` in `.env`.
3. Free-form outbound messages only work inside Meta's 24-hour customer-service window. For cold outbound, use an approved content template.

---

## WSJ / FT subscription cookies

No public API; we reuse your logged-in session cookies.

1. Log into https://www.wsj.com (or https://www.ft.com) in Chrome.
2. DevTools (`F12`) → **Application → Storage → Cookies → `https://www.wsj.com`**.
3. Export with a *Cookie-Editor* extension (or copy as cURL) → grab the single-line `Cookie:` header value.
4. Paste into `.env`:
   ```
   WSJ_COOKIES=wsjregion=na%2Cus; ...
   FT_COOKIES=FT_Site=...
   ```

Cookies typically last 1–4 weeks. When they expire, the fetcher logs `0 article links on ... cookies may be expired` and returns nothing — re-export and rerun.

---

## Gmail (newsletters) setup

1. https://console.cloud.google.com → create/select a project.
2. **APIs & Services → Library → Gmail API → Enable**.
3. **OAuth consent screen** → External → fill app name + your email. Add yourself as a test user. Scope: `https://www.googleapis.com/auth/gmail.modify`.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download JSON → save to `config/gmail_credentials.json`.
5. In Gmail, create a label `Newsletters` and route your financial newsletters into it with filters.
6. The first run opens a browser for consent and writes `config/gmail_token.json` for future runs. Each processed email is auto-labelled `Processed` so it's not re-summarized tomorrow.

---

## Running

```powershell
# One-off: full pipeline, archive Markdown, send WhatsApp.
python -m src.main

# Dry run: full pipeline + Markdown archive, skip WhatsApp.
python -m src.main --dry-run

# Daemon: keep running, fire daily at TIMEZONE + SCHEDULE_TIME (APScheduler).
python -m src.main --schedule
```

Output per run:
- `logs/reports/YYYY-MM-DD.md` — full Markdown archive (always written, even in dry-run).
- `logs/agent_YYYY-MM-DD.log` — DEBUG-level loguru log, rotated at midnight, kept 30 days.
- WhatsApp delivery — one short message per non-empty section, with a `(n/N)` marker if a section split.

---

## WhatsApp chat (Q&A back-channel)

Beyond the daily push, a FastAPI webhook accepts inbound WhatsApp messages, asks Claude with the latest archived brief as context, and replies in TwiML so Twilio answers on the same HTTP connection.

```
WhatsApp → Twilio → POST /webhook (FastAPI)
                       → ChatHandler
                            → load_latest_report()   (logs/reports/*.md, newest)
                            → Claude (claude-opus-4-7, cached system prompt)
                       ← TwiML <Message> (≤1500 chars)
       ← WhatsApp reply
```

`src/chat/`:
- `context.py` — `latest_report_path` / `load_latest_report` / `report_age_days`. Truncates reports > 20 KB; warns Claude when the brief is more than 1 day old.
- `handler.py` — financial-advisor system prompt with `cache_control: ephemeral`, one Claude call per inbound, hard-trim replies at 1500 chars on a word boundary.
- `server.py` — FastAPI app: `GET /` health check, `POST /webhook` Twilio handler. Validates `X-Twilio-Signature` when `TWILIO_AUTH_TOKEN` is set; logs a warning and accepts unsigned when it isn't (so curl smoke-tests work).

### Run locally

```powershell
# Terminal 1 — start the webhook (uses .venv + .env via src.config).
.\scripts\run_chat_server.ps1
# Defaults to http://0.0.0.0:8000. Override with $env:CHAT_PORT / $env:CHAT_HOST.

# Terminal 2 — expose a public HTTPS URL.
ngrok http 8000
```

Smoke-test:
```powershell
curl https://<your-ngrok-id>.ngrok.io/                                    # health → {"status":"ok",...}
curl -X POST https://<your-ngrok-id>.ngrok.io/webhook `
    -d "Body=hello&From=whatsapp:+15551234567"                            # simulated inbound
```

> If `TWILIO_AUTH_TOKEN` is set, curl POSTs without a signature get 403 — leave the token unset for local curl tests, or use Twilio's WhatsApp simulator which signs requests.

### Point Twilio at the webhook

**Sandbox:**
1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message → Sandbox settings**.
2. **"When a message comes in"** = `https://<your-ngrok-id>.ngrok.io/webhook`, method `HTTP POST`. Save.
3. Send a WhatsApp to the sandbox number → the webhook fires and Claude replies inline.

**WhatsApp Business sender:**
1. **Senders → WhatsApp senders → your sender → Inbound Settings**.
2. **"A message comes in"** = your public webhook URL (POST). Free-form replies only work inside Meta's 24-hour window.

---

## Deployment on Windows (Task Scheduler)

Two production modes. **Task Scheduler is recommended** — survives reboots, no terminal to keep open.

| Mode | Pros | Cons |
|---|---|---|
| Task Scheduler | Fires even when logged out; no resident process. | Per-task setup; the venv path is hard-coded into the action. |
| `--schedule` daemon | One process running APScheduler; live logs. | Dies on reboot unless wrapped with NSSM. |

### Option A — Task Scheduler

`scripts/run_agent.ps1` handles `cd`, venv activation, `PYTHONIOENCODING=utf-8`, and a dated `logs/task_scheduler_<timestamp>.log`. Point Task Scheduler at it.

1. **`Win+R` → `taskschd.msc` → Create Task…** (not "Create Basic Task" — we need the advanced tabs).
2. **General** —
   - Name: `Financial News Agent — Daily Brief`
   - Select **Run whether user is logged on or not**
   - Check **Run with highest privileges**
   - Configure for: Windows 10 (works on 11 too).
3. **Triggers → New…** — On a schedule, **Daily**, **07:00:00**, recur every **1 day**.
4. **Actions → New…** —
   - Action: **Start a program**
   - Program/script: `powershell.exe`
   - Add arguments:
     ```
     -ExecutionPolicy Bypass -File "E:\personal_projects\financial-news-agent\scripts\run_agent.ps1"
     ```
   - Start in: `E:\personal_projects\financial-news-agent`
5. **Conditions** —
   - Uncheck **Start the task only if the computer is on AC power** (otherwise it skips on battery).
   - Check **Wake the computer to run this task** (otherwise sleep blocks the trigger).
6. **Settings** —
   - Check **Allow task to be run on demand**.
   - Check **If the running task does not end when requested, force it to stop**.
   - **If the task fails, restart every:** 5 minutes, up to 3 times.
7. OK → Windows password prompt.

Test: right-click the task → **Run**. Verify `logs/task_scheduler_<timestamp>.log` and that `logs/reports/<today>.md` was written.

> If you move the project, edit the action — the `-File` argument and `Start in` are absolute paths.

### Option B — `--schedule` daemon

```powershell
.venv\Scripts\Activate.ps1
python -m src.main --schedule
```

Logs the next fire time and blocks. Ctrl-C exits cleanly. Wrap with [NSSM](https://nssm.cc/) for a Windows service.

---

## Tests

```powershell
pytest                          # full suite, 64 tests, <1s
pytest tests/test_formatter.py  # one file
pytest -k whatsapp              # filter by name
```

What's covered (64 tests across 6 files):

| File | Tests | Covers |
|---|---:|---|
| `tests/test_models.py` | 5 | Pydantic models — required fields, defaults, six-section Report shape |
| `tests/test_deduplicator.py` | 6 | URL-exact dedup, fuzzy title dedup (Fed-rates paraphrase), content-length tiebreak |
| `tests/test_watchlist_filter.py` | 8 | Pass-through, include terms, ignored_topics precedence, content+source search |
| `tests/test_rss_fetcher.py` | 7 | Feedparser parse, summary extraction, deterministic IDs, malformed feed |
| `tests/test_formatter.py` | 18 | WhatsApp chunking, bullet normalization, trend emojis, header inline, Markdown |
| `tests/test_chat_handler.py` | 20 | Trim-on-word-boundary, system prompt assembly, ChatHandler I/O, context loader |

Tests use fixtures only — **no live network in CI**.

---

## Troubleshooting

Issues actually hit during development; check these first.

### Daily brief

- **Task fires but no WhatsApp arrives.** Check `logs/task_scheduler_<timestamp>.log`. Most common: `.env` not populated → every API skips and `WhatsAppDelivery` warns "TWILIO_… not set". Run `python -m src.main --dry-run` interactively to verify the pipeline works.
- **`zoneinfo.ZoneInfoNotFoundError`.** The `tzdata` package in `requirements.txt` ships the IANA database for Windows. If you removed it, `pip install tzdata`.
- **WSJ/FT return 0 items.** Cookies expired (1–4 weeks typical). Re-export the `Cookie:` header from DevTools into `.env`. The fetcher logs `0 article links on ... cookies may be expired` when this happens.
- **Bloomberg always 0.** Disabled in `sources.yaml` — they have no public RSS. Don't enable without first picking an alternate fetch strategy.
- **Twilio sandbox stops accepting sends.** Sandbox session times out after **72 h** of inactivity. Send `join <code>` from your phone.
- **Emojis appear as `?` or crash on cp1256.** The PowerShell wrappers set `$env:PYTHONIOENCODING = "utf-8"`. For manual runs, set it yourself before invoking Python.
- **Task doesn't fire when laptop is sleeping at 07:00.** Trigger setting "Wake the computer to run this task" must be checked AND Power Options → Advanced → Sleep → Allow wake timers → Enabled.
- **`pip install` fails with SSL errors on corporate networks.** Set `PIP_INDEX_URL` to your internal mirror or run from a personal network.
- **Claude returns garbage / unparseable JSON from the prioritizer.** The fallback path returns the first 20 unranked items and logs the failure — the run continues.

### Chat webhook

- **403 from `/webhook`.** Either `TWILIO_AUTH_TOKEN` is wrong, or you're hitting the endpoint with curl/Postman without an `X-Twilio-Signature` header. For local testing, unset `TWILIO_AUTH_TOKEN`.
- **Replies cut off mid-sentence.** Hard cap is 1500 chars per WhatsApp message (handler trims on word boundary, appends `…`). Ask follow-ups instead of demanding long-form answers.
- **"The chat assistant isn't configured yet."** `ANTHROPIC_API_KEY` is empty in `.env`. Fix and restart the server.
- **"This brief is N days old."** Chat handler is correctly warning the user that the scheduler hasn't fired recently — check Task Scheduler history.
- **ngrok URL changes every restart.** Free-plan reality. Either keep the tunnel running, pay for a reserved domain, or paste the new URL into Twilio's sandbox settings each time.
- **PowerShell error: "Cannot overwrite variable Host."** Fixed in `scripts/run_chat_server.ps1` (the script now uses `$ServerHost`). If you wrote your own wrapper, do the same — `$Host` is read-only in PowerShell.

---

## Project layout

```
src/
  main.py                 entry point: --dry-run, --schedule, default one-off
  config.py               env + YAML loader; exports `settings`, paths
  models.py               Pydantic: NewsItem, MarketIndex, ReportSection, Report
  fetchers/
    base.py               BaseFetcher / BaseIndexFetcher (with safe_fetch)
    rss_fetcher.py        generic RSS/Atom (Reuters, CNBC, MarketWatch, Yahoo, Axios)
    arabic_sources.py     Argaam, Mubasher, Al-Eqtisadiah scrapers
    wsj_fetcher.py        WSJ markets/economy via cookie
    ft_fetcher.py         FT markets/companies via cookie
    newsletter_fetcher.py Gmail OAuth + label re-tagging
    alpha_vantage.py      US indices via SPY/QQQ/DIA ETF proxies
    fred_fetcher.py       CPI, UNRATE, DFF, DGS10
    finnhub_fetcher.py    general-category news API
    tadawul_fetcher.py    Saudi Exchange issuer disclosures
  processors/
    deduplicator.py       URL-exact + Jaccard/SequenceMatcher fuzzy title
    watchlist_filter.py   include terms + ignored_topics
    prioritizer.py        top-N via Claude (cached system prompt)
    summarizer.py         6 parallel section calls via Claude (cached prompt)
  delivery/
    base.py               BaseDelivery
    formatter.py          format_whatsapp (chunks) + format_markdown + save_markdown
    whatsapp.py           Twilio send with exponential-backoff retry
  chat/
    context.py            load_latest_report, latest_report_path, report_age_days
    handler.py            ChatHandler: system prompt + Claude + 1500-char trim
    server.py             FastAPI: GET /, POST /webhook (TwiML reply)
  utils/
    logger.py             loguru: stderr + daily-rotating file in logs/

config/
  sources.yaml            source registry
  watchlist.yaml          include/exclude filters
  gmail_credentials.json  (gitignored) OAuth client
  gmail_token.json        (gitignored) OAuth refresh token

scripts/
  run_agent.ps1           Task Scheduler wrapper (daily brief)
  run_chat_server.ps1     uvicorn launcher (chat webhook)

tests/                    pytest, 64 tests, no network
logs/                     (gitignored) agent + task_scheduler logs
logs/reports/             (gitignored) YYYY-MM-DD.md archive — chat context source
data/cache/               (gitignored) reserved for future caching

.env.example              template; copy to .env
pytest.ini                pytest config (-v, short tracebacks, color)
requirements.txt          pinned floor versions
CLAUDE.md                 contributor / future-Claude notes
```

See `CLAUDE.md` for in-repo contributor conventions.
