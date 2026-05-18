"""Configuration loader: environment variables + YAML configs.

Single source of truth for paths, API keys, and runtime settings.
Imported by every module that needs config; do not duplicate env lookups elsewhere.
"""

import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

# Project paths (resolved relative to this file, not CWD, so cron/scheduler runs work)
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR = LOGS_DIR / "reports"

# Ensure runtime directories exist (idempotent)
for _d in (CACHE_DIR, LOGS_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


class Settings(BaseModel):
    """Centralized settings loaded from environment variables."""

    # --- Claude ---
    anthropic_api_key: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    claude_model: str = "claude-opus-4-7"

    # --- Twilio WhatsApp ---
    twilio_account_sid: str = Field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = Field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    # Sandbox example: "whatsapp:+14155238886"; production: your approved WhatsApp Business sender
    twilio_whatsapp_from: str = Field(default_factory=lambda: os.getenv("TWILIO_WHATSAPP_FROM", ""))
    user_whatsapp_to: str = Field(default_factory=lambda: os.getenv("USER_WHATSAPP_TO", ""))

    # --- Subscription cookies (raw Cookie header strings) ---
    wsj_cookies: str = Field(default_factory=lambda: os.getenv("WSJ_COOKIES", ""))
    ft_cookies: str = Field(default_factory=lambda: os.getenv("FT_COOKIES", ""))

    # --- Gmail OAuth ---
    gmail_credentials_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("GMAIL_CREDENTIALS_PATH", str(CONFIG_DIR / "gmail_credentials.json"))
        )
    )
    gmail_token_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("GMAIL_TOKEN_PATH", str(CONFIG_DIR / "gmail_token.json"))
        )
    )

    # --- Market data APIs ---
    alpha_vantage_api_key: str = Field(default_factory=lambda: os.getenv("ALPHA_VANTAGE_API_KEY", ""))
    fred_api_key: str = Field(default_factory=lambda: os.getenv("FRED_API_KEY", ""))
    finnhub_api_key: str = Field(default_factory=lambda: os.getenv("FINNHUB_API_KEY", ""))

    # --- Scheduling ---
    timezone: str = Field(default_factory=lambda: os.getenv("TIMEZONE", "America/Chicago"))
    schedule_time: str = Field(default_factory=lambda: os.getenv("SCHEDULE_TIME", "07:00"))

    # --- Report ---
    # "english" = translate everything to English (Phase 1 default per user choice)
    # "mixed"   = keep source language; "arabic" = translate everything to Arabic
    report_language: str = Field(default_factory=lambda: os.getenv("REPORT_LANGUAGE", "english"))


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file from disk; return an empty dict if the file is missing."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_sources() -> Dict[str, Any]:
    """Load and return the source registry from config/sources.yaml."""
    return _load_yaml(CONFIG_DIR / "sources.yaml")


def load_watchlist() -> Dict[str, Any]:
    """Load and return the user watchlist from config/watchlist.yaml."""
    return _load_yaml(CONFIG_DIR / "watchlist.yaml")


# Module-level singleton; import as `from src.config import settings`
settings = Settings()
