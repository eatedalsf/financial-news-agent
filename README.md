# Financial News Agent

A daily agent that aggregates financial news from 15+ sources, prioritizes and summarizes them with Claude, and delivers a single morning briefing to WhatsApp.

- **Schedule:** every day at 07:00 `America/Chicago` (Minnesota).
- **Sources:** Reuters, CNBC, MarketWatch, Yahoo Finance, Axios Markets, Argaam, Mubasher, Al-Eqtisadiah, CNBC Arabia, Asharq Business, WSJ (subscription), FT (subscription), Gmail newsletters, Alpha Vantage, FRED, Finnhub, Tadawul.
- **Delivery:** Twilio WhatsApp + a Markdown copy saved to `logs/reports/YYYY-MM-DD.md`.

> **Status:** Phase 1 (foundation) complete. Fetchers, processors, delivery, and the scheduler are implemented in Phases 2–6.

---

## Requirements

- Windows 10/11
- Python 3.11+ (tested on 3.14.2)
- Git
- Accounts/keys for: Anthropic, Twilio, Alpha Vantage, FRED, Finnhub, Google Cloud (Gmail API)

---

## Setup

### 1. Clone and create a virtual environment

```powershell
git clone <repo-url> financial-news-agent
cd financial-news-agent
python -m venv .venv
.venv\Scripts\Activate.ps1     # PowerShell
# or: .venv\Scripts\activate.bat   (cmd.exe)
```

> If PowerShell blocks activation with an execution-policy error:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

### 2. Install dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure environment

```powershell
copy .env.example .env
# Then open .env in your editor and fill in the keys.
```

### 4. Configure sources and watchlist

- `config/sources.yaml` — enable/disable individual sources.
- `config/watchlist.yaml` — leave empty to receive everything, or add tickers/keywords to narrow the report.

---

## Twilio WhatsApp setup

You can run with either the **Sandbox** (free, for development) or a **WhatsApp Business** sender (production). The notifier reads `TWILIO_WHATSAPP_FROM`, so the same code path works for both.

### Option A — Sandbox (recommended for first run)

1. Sign in at https://console.twilio.com → **Messaging → Try it out → Send a WhatsApp message**.
2. Twilio shows a sandbox number (e.g., `+1 415 523 8886`) and a join code like `join sunny-river`.
3. From the WhatsApp app on your phone, send that join code to the sandbox number.
4. In `.env`, set:
   ```
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
   USER_WHATSAPP_TO=whatsapp:+<your number in E.164>
   ```
5. The sandbox session expires after 72 hours of inactivity; just resend `join <code>` to refresh.

### Option B — WhatsApp Business sender (production)

1. Complete Twilio's WhatsApp Business onboarding (Meta Business verification, approved sender).
2. In `.env`, set `TWILIO_WHATSAPP_FROM=whatsapp:+<your approved business number>`.
3. Free-form messages can only be sent to users who messaged you in the last 24h. Outside that window, you must use an approved **content template** — Phase 5 will add a template id env var if you need this.

---

## WSJ / FT subscription cookies

These sites have no public API; we use your logged-in session cookies.

1. Log into https://www.wsj.com in Chrome.
2. Open DevTools (F12) → **Application → Storage → Cookies → `https://www.wsj.com`**.
3. Right-click the cookie list → **Copy all as cURL (cmd)** (or use an extension like *Cookie-Editor* → Export → Header format).
4. Extract just the `Cookie:` header value (a single line of `name=value; name=value; ...`).
5. Paste it into `.env`:
   ```
   WSJ_COOKIES=wsjregion=na%2Cus; ...
   ```
6. Repeat for https://www.ft.com → `FT_COOKIES`.

> Cookies expire (typically 1–4 weeks). The fetcher will log a clear warning when WSJ/FT start returning paywalled HTML; refresh the cookies and rerun.

---

## Gmail (newsletters) setup

1. Go to https://console.cloud.google.com → create or select a project.
2. **APIs & Services → Library → Gmail API → Enable.**
3. **APIs & Services → OAuth consent screen** → External → fill in app name + your email. Add yourself as a test user. Scope: `https://www.googleapis.com/auth/gmail.modify`.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app.**
5. Download the JSON and save it to `config/gmail_credentials.json`.
6. In Gmail, create a label named `Newsletters` and route your financial newsletters into it (filters).
7. The first run will open a browser for OAuth consent and write `config/gmail_token.json` for future runs.

---

## Running

```powershell
# One-off run: full pipeline, send to WhatsApp, archive Markdown.
python -m src.main

# Dry run: full pipeline + Markdown archive, skip WhatsApp send.
python -m src.main --dry-run

# Daemon: keep running, fire daily at TIMEZONE+SCHEDULE_TIME (from .env).
python -m src.main --schedule
```

The report is sent to WhatsApp and also written to `logs/reports/YYYY-MM-DD.md`.
Run logs land in `logs/agent_<date>.log`.

---

## WhatsApp chat (Q&A back-channel)

In addition to the daily push, the agent ships a FastAPI webhook that turns
WhatsApp into a two-way chat. The user texts a question, Claude answers using
the latest archived brief as context, and the reply lands in the same thread.

### How it fits together

```
WhatsApp -> Twilio -> POST /webhook (FastAPI on your machine)
                         -> src.chat.handler.ChatHandler
                              -> Claude (claude-opus-4-7) + latest report
                         <- TwiML reply
       <- WhatsApp reply
```

`src/chat/`:
- `context.py` — loads the newest `logs/reports/YYYY-MM-DD.md` as Claude context.
- `handler.py` — financial-advisor system prompt + Claude call; trims to 1500 chars.
- `server.py` — FastAPI app exposing `POST /webhook` (TwiML response).

### Run locally

```powershell
# Terminal 1 — start the webhook (uses .venv + the Anthropic/Twilio keys in .env).
.\scripts\run_chat_server.ps1
# Listens on http://0.0.0.0:8000 (override with $env:CHAT_PORT / $env:CHAT_HOST).

# Terminal 2 — tunnel a public HTTPS URL to your local port.
ngrok http 8000
# ngrok prints something like: Forwarding  https://abc123.ngrok.io -> http://localhost:8000
```

Smoke-test the webhook is up:
```powershell
curl https://abc123.ngrok.io/                            # health check
curl -X POST https://abc123.ngrok.io/webhook `
    -d "Body=hello&From=whatsapp:+15551234567"           # simulated inbound
```

> The first POST will 403 if `TWILIO_AUTH_TOKEN` is set (signature check). For
> manual curl testing, either leave the token unset locally or use Twilio's
> WhatsApp simulator which signs requests correctly.

### Point Twilio at the webhook

**Sandbox** (matches the daily-push setup):
1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message → Sandbox settings**.
2. In **"When a message comes in"**, paste `https://<your-ngrok-id>.ngrok.io/webhook`.
3. Method: `HTTP POST`. Save.
4. From WhatsApp, send a message to the sandbox number — the webhook fires and Claude replies in-thread.

**WhatsApp Business sender** (production):
1. Twilio Console → **Messaging → Senders → WhatsApp senders → your sender**.
2. Set **Inbound Settings → "A message comes in"** to your public webhook URL (`POST`).
3. Free-form replies only work inside Meta's 24-hour customer-service window; for cold outbound, use an approved template.

### Gotchas

- **ngrok URL changes every restart on the free plan.** Either keep the tunnel
  running or pay for a reserved domain. Twilio caches nothing — paste the new
  URL into the sandbox settings each time.
- **403 from `/webhook`.** Means signature validation rejected the request.
  Either the `TWILIO_AUTH_TOKEN` is wrong, or you're hitting the webhook with
  curl/Postman without a signature. Unset the token for local testing.
- **Replies cut off.** Replies are hard-capped at 1500 chars (one WhatsApp
  message). Ask follow-ups instead of demanding long-form answers.
- **"The chat assistant isn't configured."** `ANTHROPIC_API_KEY` is empty in
  `.env`. Fix and restart the server.

---

## Tests

```powershell
pytest                          # full suite (~1s, 34 tests)
pytest tests/test_formatter.py  # one file
pytest -k dedup                 # filter by name
```

---

## Deployment on Windows (Task Scheduler)

There are two viable production modes:

| Mode | What it does | When to choose it |
|---|---|---|
| **Task Scheduler** (recommended) | Windows wakes the script daily at 07:00. No persistent process. | You want it to "just work" — survives reboots, no terminal to leave open. |
| **`--schedule` daemon** | A long-lived Python process running APScheduler. | You want logs in a terminal you watch, or you can't use Task Scheduler. |

### Option A — Task Scheduler (recommended)

This repo ships `scripts/run_agent.ps1`, a small PowerShell wrapper that handles
venv activation, UTF-8 stdio, and per-run logging. Point Task Scheduler at it.

1. **Win+R → `taskschd.msc`** → in the right pane, **Create Task…** (not "Create Basic Task" — we need the advanced options).
2. **General tab:**
   - Name: `Financial News Agent — Daily Brief`
   - Select **Run whether user is logged on or not** (so it fires even when you're logged out).
   - Check **Run with highest privileges** (avoids permission issues in `logs/`).
   - Configure for: **Windows 10** (works on 11 too).
3. **Triggers tab → New…:**
   - Begin the task: **On a schedule**
   - Settings: **Daily**, at **07:00:00**.
   - Recur every **1 day**.
   - Enabled: ✓
4. **Actions tab → New…:**
   - Action: **Start a program**
   - Program/script: `powershell.exe`
   - Add arguments:
     ```
     -ExecutionPolicy Bypass -File "E:\personal_projects\financial-news-agent\scripts\run_agent.ps1"
     ```
   - Start in: `E:\personal_projects\financial-news-agent`
5. **Conditions tab:**
   - Uncheck **Start the task only if the computer is on AC power** (otherwise it skips on battery).
   - Check **Wake the computer to run this task** (otherwise sleep blocks the trigger).
6. **Settings tab:**
   - Check **Allow task to be run on demand**.
   - Check **If the running task does not end when requested, force it to stop**.
   - **If the task fails, restart every:** 5 minutes, up to 3 times.
7. Click **OK**, enter your Windows password when prompted.

**Test it:** right-click the task → **Run**. Check `logs/task_scheduler_<timestamp>.log` and `logs/reports/YYYY-MM-DD.md`.

### Option B — `--schedule` daemon

```powershell
.venv\Scripts\Activate.ps1
python -m src.main --schedule
```

The process logs the next fire time on startup, then blocks. Ctrl-C to stop.
To run it as a background Windows service, use [NSSM](https://nssm.cc/) to wrap
the command — that's out of scope for this README.

### Common Windows gotchas

- **Task runs but produces nothing.** Check `logs/task_scheduler_<timestamp>.log`.
  Most common cause: `.env` not populated, so every fetcher/API skips. Run
  `python -m src.main --dry-run` interactively first to verify everything works.
- **PowerShell execution policy error.** The Task Scheduler action above passes
  `-ExecutionPolicy Bypass`. For manual runs, set policy once with:
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.
- **Emojis appear as `?` or the script crashes on emojis.** The PowerShell
  wrapper sets `PYTHONIOENCODING=utf-8` to avoid this. For manual runs, set it
  yourself: `$env:PYTHONIOENCODING="utf-8"`.
- **Task doesn't fire when laptop is sleeping at 07:00.** Trigger setting
  "Wake the computer to run this task" must be checked AND your power plan must
  allow wake timers (Power Options → Advanced → Sleep → Allow wake timers → Enabled).
- **`zoneinfo` raises `ZoneInfoNotFoundError`.** The `tzdata` package in
  `requirements.txt` ships the IANA database; if you removed it, reinstall.

---

## Project layout

```
src/
  main.py           # Entry point (Phase 6)
  config.py         # Env + YAML loader
  models.py         # NewsItem, MarketIndex, Report
  fetchers/         # One module per source, all inherit BaseFetcher
  processors/       # dedup, watchlist filter, prioritize, summarize
  delivery/         # WhatsApp + formatter
  chat/             # FastAPI webhook + Claude-backed Q&A handler
  utils/            # logger, cache
config/
  sources.yaml      # Source registry
  watchlist.yaml    # User watchlist
data/cache/         # Dedup hashes, fetch state
logs/               # Daily logs + reports/
tests/              # pytest
```

See `CLAUDE.md` for contributor / future-Claude notes.
