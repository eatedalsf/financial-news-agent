"""FastAPI app that hosts the Twilio WhatsApp webhook for the chat handler.

Twilio POSTs incoming WhatsApp messages to `/webhook` as form-encoded data
(`Body`, `From`, `To`, `MessageSid`, ...). We hand `Body` + `From` to
`ChatHandler.handle()` and answer with TwiML so Twilio replies on the same
connection — no second REST call back.

Run locally:
    uvicorn src.chat.server:app --reload --port 8000

Expose to Twilio:
    ngrok http 8000        # then paste the https URL + "/webhook" into Twilio

Security:
    If `TWILIO_AUTH_TOKEN` is set we validate the `X-Twilio-Signature` header
    on every inbound request. Misconfigured / unsigned requests get a 403.
    With no auth token set (dev) we log a warning and accept anyway, so
    local testing without ngrok still works.
"""

from typing import Optional

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from src.chat.handler import ChatHandler
from src.config import settings
from src.utils.logger import logger

app = FastAPI(title="Financial News Agent — Chat Webhook")

# Single handler shared across requests so the Anthropic client (and its
# underlying httpx connection pool) is reused.
_handler = ChatHandler()


@app.get("/", include_in_schema=False)
async def health() -> dict:
    """Cheap health check for uptime monitors and ngrok smoke-tests."""
    return {"status": "ok", "service": "financial-news-agent-chat"}


@app.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(default=""),
    From: str = Form(default=""),
    x_twilio_signature: Optional[str] = Header(default=None),
) -> Response:
    """Twilio inbound-message webhook. Replies inline as TwiML."""
    await _verify_twilio_signature(request, x_twilio_signature)

    sender = From or "unknown"
    logger.info(
        f"chat.server: inbound from={sender} body_len={len(Body)}"
    )

    try:
        reply_text = await _handler.handle(Body, sender=sender)
    except Exception as exc:  # noqa: BLE001 - never 500 to Twilio; reply gracefully
        logger.exception(f"chat.server: handler raised - {exc}")
        reply_text = "Sorry, I hit an error. Try again in a minute."

    twiml = MessagingResponse()
    twiml.message(reply_text)
    return Response(content=str(twiml), media_type="application/xml")


async def _verify_twilio_signature(
    request: Request, signature: Optional[str]
) -> None:
    """Reject requests that aren't signed by Twilio when an auth token is configured."""
    if not settings.twilio_auth_token:
        logger.warning(
            "chat.server: TWILIO_AUTH_TOKEN not set, skipping signature validation"
        )
        return

    if not signature:
        logger.warning("chat.server: missing X-Twilio-Signature header")
        raise HTTPException(status_code=403, detail="Missing Twilio signature")

    validator = RequestValidator(settings.twilio_auth_token)
    # Twilio signs the full public URL + sorted form params. Reconstruct the URL
    # from the request so it matches what Twilio used to compute the signature.
    url = str(request.url)
    form = await request.form()
    params = {k: v for k, v in form.items()}

    if not validator.validate(url, params, signature):
        logger.warning(
            f"chat.server: invalid Twilio signature for {url}"
        )
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
