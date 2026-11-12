"""Vercel Python serverless function: FastAPI app handling Meta + Telegram webhooks."""

from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response

from tender_bhai import obs
from tender_bhai.config import load_settings
from tender_bhai.db import Database
from tender_bhai.messaging.factory import get_messenger
from tender_bhai.webhook import (
    WebhookService,
    parse_meta_payload,
    parse_telegram_payload,
    verify_meta_signature,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tender_bhai.api")

app = FastAPI(title="Tender Bhai webhook", docs_url=None, redoc_url=None)


@lru_cache(maxsize=1)
def _service() -> WebhookService:
    cfg = load_settings()
    obs.init(workflow_name="tender-bhai", tags=[*cfg.env_tags, "webhook"])
    db = Database(cfg.supabase_url, cfg.supabase_service_key)
    return WebhookService(cfg, db, get_messenger(cfg))


@app.get("/api/webhook")
def verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    cfg = load_settings()
    if hub_mode == "subscribe" and hub_verify_token and hub_verify_token == cfg.meta_verify_token:
        return Response(content=hub_challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="verification failed")


@app.post("/api/webhook")
async def meta_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, str]:
    cfg = load_settings()
    body = await request.body()
    if not verify_meta_signature(cfg.meta_app_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="bad signature")
    payload = await request.json()
    outcomes = []
    for inbound in parse_meta_payload(payload):
        try:
            outcomes.append(_service().handle(inbound))
        except Exception as exc:  # never let Meta retry-storm us over our own bug
            log.exception("meta handling failed: %s", exc)
            outcomes.append("error")
    return {"status": "ok", "handled": ",".join(outcomes) or "none"}


@app.post("/api/webhook/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict[str, str]:
    cfg = load_settings()
    if not cfg.telegram_webhook_secret or secret != cfg.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="bad token")
    payload = await request.json()
    outcomes = []
    for inbound in parse_telegram_payload(payload):
        try:
            outcomes.append(_service().handle(inbound))
        except Exception as exc:
            log.exception("telegram handling failed: %s", exc)
            outcomes.append("error")
    return {"status": "ok", "handled": ",".join(outcomes) or "none"}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
