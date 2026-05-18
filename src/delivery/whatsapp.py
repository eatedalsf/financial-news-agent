"""Twilio WhatsApp delivery with exponential-backoff retry.

Works identically with the Twilio Sandbox and an approved WhatsApp Business
sender — the From/To addresses come from env vars, the call shape is the same.

Retry policy:
  - 4xx responses (bad auth, invalid number, sandbox-not-joined): hard fail.
    Retrying won't help; logged loud so the user notices.
  - 5xx and network errors: exponential backoff (1s, 2s, 4s) up to max_retries.
  - Per-chunk failure does NOT abort remaining chunks — the rest still send,
    and the overall send() returns False to signal a partial failure.
"""

import asyncio
from typing import Optional

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from src.config import settings
from src.delivery.base import BaseDelivery
from src.delivery.formatter import format_whatsapp
from src.models import Report
from src.utils.logger import logger


class WhatsAppDelivery(BaseDelivery):
    """Send a Report via Twilio WhatsApp as one or more chunked messages."""

    channel_name = "whatsapp"

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries
        if settings.twilio_account_sid and settings.twilio_auth_token:
            self.client: Optional[Client] = Client(
                settings.twilio_account_sid,
                settings.twilio_auth_token,
            )
        else:
            self.client = None

    async def send(self, report: Report) -> bool:
        if not self._preflight_ok():
            return False

        chunks = format_whatsapp(report)
        logger.info(
            f"{self.channel_name}: sending {len(chunks)} chunk(s) "
            f"to {settings.user_whatsapp_to}"
        )

        all_ok = True
        for i, chunk in enumerate(chunks, start=1):
            ok = await self._send_one(chunk, label=f"chunk {i}/{len(chunks)}")
            all_ok = all_ok and ok
            # Small inter-chunk delay so messages arrive in order on the device.
            if i < len(chunks):
                await asyncio.sleep(0.5)
        return all_ok

    # ----- Internals ----------------------------------------------------- #

    def _preflight_ok(self) -> bool:
        if self.client is None:
            logger.warning(
                f"{self.channel_name}: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN "
                "not set, skipping WhatsApp send"
            )
            return False
        if not settings.twilio_whatsapp_from:
            logger.warning(
                f"{self.channel_name}: TWILIO_WHATSAPP_FROM not set"
            )
            return False
        if not settings.user_whatsapp_to:
            logger.warning(
                f"{self.channel_name}: USER_WHATSAPP_TO not set"
            )
            return False
        return True

    async def _send_one(self, body: str, label: str) -> bool:
        """Send one message body with retry/backoff. Returns True on success."""
        for attempt in range(1, self.max_retries + 1):
            try:
                await asyncio.to_thread(self._send_blocking, body)
                logger.info(
                    f"{self.channel_name}: {label} sent ({len(body)} chars)"
                )
                return True
            except TwilioRestException as exc:
                status = exc.status or 0
                if 400 <= status < 500:
                    # Permanent: bad credentials, invalid To/From, sandbox not
                    # joined, message exceeds size. Retrying will not help.
                    logger.error(
                        f"{self.channel_name}: {label} hard-failed "
                        f"(status={status}, code={exc.code}) - {exc.msg}"
                    )
                    return False
                logger.warning(
                    f"{self.channel_name}: {label} attempt {attempt} "
                    f"failed (status={status}, code={exc.code}) - {exc.msg}"
                )
            except Exception as exc:  # noqa: BLE001 - network/SSL/transient
                logger.warning(
                    f"{self.channel_name}: {label} attempt {attempt} error - {exc}"
                )

            if attempt < self.max_retries:
                delay = 2 ** (attempt - 1)  # 1s, 2s, 4s
                await asyncio.sleep(delay)

        logger.error(
            f"{self.channel_name}: {label} exhausted {self.max_retries} retries"
        )
        return False

    def _send_blocking(self, body: str) -> None:
        """Synchronous Twilio call; wrapped via `asyncio.to_thread` above."""
        assert self.client is not None
        self.client.messages.create(
            from_=settings.twilio_whatsapp_from,
            to=settings.user_whatsapp_to,
            body=body,
        )
