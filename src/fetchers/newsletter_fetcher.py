"""Gmail newsletter fetcher.

Reads emails labeled `Newsletters` from the last 24 hours, converts them to
NewsItems, then re-labels each with `Processed` so they are not re-summarized
on subsequent runs.

OAuth flow:
  - First run: opens browser for consent, writes refresh token to disk.
  - Subsequent runs: reads token from disk, refreshes silently when expired.
"""

import asyncio
import base64
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import load_sources, settings
from src.fetchers.base import BaseFetcher
from src.models import NewsItem
from src.utils.logger import logger

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class NewsletterFetcher(BaseFetcher):
    """Pull newsletters from a Gmail label and mark them processed after read."""

    source_name = "gmail_newsletters"

    def __init__(self) -> None:
        cfg = load_sources().get("gmail", {})
        self.source_label = cfg.get("source_label", "Newsletters")
        self.processed_label = cfg.get("processed_label", "Processed")
        self.category = "newsletter"

    async def fetch(self) -> List[NewsItem]:
        # google-api-python-client is sync; offload to a thread so we never
        # block the event loop on a Gmail round-trip.
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> List[NewsItem]:
        creds = self._load_or_create_credentials()
        if creds is None:
            return []

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        try:
            source_id = self._label_id(service, self.source_label)
            if source_id is None:
                logger.warning(
                    f"{self.source_name}: label '{self.source_label}' not found - "
                    "create it in Gmail and route newsletters into it"
                )
                return []
            processed_id = self._ensure_label(service, self.processed_label)
        except HttpError as exc:
            logger.exception(f"{self.source_name}: Gmail API error - {exc}")
            return []

        query = f"newer_than:1d -label:{self.processed_label}"
        try:
            resp = (
                service.users()
                .messages()
                .list(userId="me", labelIds=[source_id], q=query, maxResults=50)
                .execute()
            )
        except HttpError as exc:
            logger.exception(f"{self.source_name}: list messages failed - {exc}")
            return []

        items: List[NewsItem] = []
        for meta in resp.get("messages", []):
            try:
                item = self._fetch_message(service, meta["id"])
            except HttpError as exc:
                logger.warning(
                    f"{self.source_name}: skip msg {meta['id']} - {exc}"
                )
                continue
            if item is None:
                continue
            items.append(item)
            self._mark_processed(service, meta["id"], processed_id)
        return items

    # ----- Credentials --------------------------------------------------- #

    def _load_or_create_credentials(self) -> Optional[Credentials]:
        token_path: Path = settings.gmail_token_path
        creds_path: Path = settings.gmail_credentials_path

        creds: Optional[Credentials] = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"{self.source_name}: token refresh failed, re-auth needed - {exc}"
                )

        if not creds_path.exists():
            logger.warning(
                f"{self.source_name}: {creds_path} not found - "
                "download OAuth client JSON from Google Cloud Console (see README)"
            )
            return None

        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        # First-run interactive consent. Opens browser, listens on localhost.
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info(f"{self.source_name}: OAuth token saved to {token_path}")
        return creds

    # ----- Gmail helpers ------------------------------------------------- #

    @staticmethod
    def _label_id(service: Any, name: str) -> Optional[str]:
        resp = service.users().labels().list(userId="me").execute()
        for lbl in resp.get("labels", []):
            if lbl["name"].lower() == name.lower():
                return lbl["id"]
        return None

    def _ensure_label(self, service: Any, name: str) -> str:
        existing = self._label_id(service, name)
        if existing:
            return existing
        created = (
            service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        logger.info(
            f"{self.source_name}: created label '{name}' (id={created['id']})"
        )
        return created["id"]

    def _fetch_message(self, service: Any, msg_id: str) -> Optional[NewsItem]:
        msg = (
            service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in msg["payload"].get("headers", [])
        }
        subject = headers.get("subject", "(no subject)").strip()
        sender = headers.get("from", "unknown")
        published = self._parse_date(headers.get("date"))

        body_html = self._extract_body(msg["payload"])
        clean_text = self._strip_html(body_html)[:5000]  # cap for Claude context

        return NewsItem(
            id=msg_id,
            title=subject,
            url=f"https://mail.google.com/mail/u/0/#all/{msg_id}",
            source=f"newsletter:{self._sender_slug(sender)}",
            published_at=published,
            summary=clean_text[:500] if clean_text else None,
            content=clean_text or None,
            language="en",
            category=self.category,
        )

    def _mark_processed(
        self, service: Any, msg_id: str, processed_label_id: str
    ) -> None:
        try:
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": [processed_label_id]},
            ).execute()
        except HttpError as exc:
            logger.warning(
                f"{self.source_name}: failed to label msg {msg_id} - {exc}"
            )

    # ----- Parsing utilities -------------------------------------------- #

    @staticmethod
    def _extract_body(payload: dict) -> str:
        """Walk MIME tree and return the first text/plain or text/html body."""
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data")
                    if data:
                        return base64.urlsafe_b64decode(data).decode(
                            "utf-8", errors="replace"
                        )
            for part in payload["parts"]:
                if part.get("mimeType") == "text/html":
                    data = part.get("body", {}).get("data")
                    if data:
                        return base64.urlsafe_b64decode(data).decode(
                            "utf-8", errors="replace"
                        )
            for part in payload["parts"]:
                if "parts" in part:
                    inner = NewsletterFetcher._extract_body(part)
                    if inner:
                        return inner
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    @staticmethod
    def _strip_html(html: str) -> str:
        """Crude HTML → text. The summarizer rewrites this anyway."""
        text = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
        text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> datetime:
        if not date_str:
            return datetime.now(timezone.utc)
        try:
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _sender_slug(from_header: str) -> str:
        """`'Bloomberg <newsletter@bloomberg.com>'` -> `'bloomberg'`."""
        m = re.match(r"\"?([^\"<]+)\"?\s*<", from_header)
        if m:
            return m.group(1).strip().lower().replace(" ", "_")
        m = re.search(r"@([^>\s]+)", from_header)
        if m:
            return m.group(1).split(".")[0].lower()
        return "unknown"
