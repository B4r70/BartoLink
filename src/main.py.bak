# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : main.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : FastAPI-Service für Push-Notifications.
#                  Nimmt Token-Registrierungen und Push-Requests entgegen,
#                  sendet via APNs, deaktiviert ungültige Tokens automatisch.
#  Start         : uvicorn src.main:app --host 127.0.0.1 --port 8765
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, status

from src.apns_client import PushPayload, apns
from src.auth import verify_token
from src.config import settings
from src.models import (
    PushRequest,
    PushResponse,
    TokenInfo,
    TokenRegisterRequest,
    TokenRegisterResponse,
)
from src.tokens import (
    DeviceToken,
    deactivate,
    init_db,
    list_active,
    register_or_update,
)


# ------------------------------------------------------------------------------
#  Logging
# ------------------------------------------------------------------------------

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("barto-link")


# ------------------------------------------------------------------------------
#  Lifecycle
# ------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/Shutdown-Hooks."""
    logger.info("barto-link Backend startet...")
    init_db()
    logger.info(
        "Bereit. Bundle=%s, APNs-Env=%s, Server=%s:%d",
        settings.apple_bundle_id,
        settings.apns_environment,
        settings.server_host,
        settings.server_port,
    )
    yield
    logger.info("barto-link Backend wird beendet.")


# ------------------------------------------------------------------------------
#  FastAPI-App
# ------------------------------------------------------------------------------

app = FastAPI(
    title="barto-link",
    version="0.1.0",
    description="Personal push-notification gateway for the BartoAI ecosystem",
    lifespan=lifespan,
)


# ------------------------------------------------------------------------------
#  Public Endpoint
# ------------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Liveness-Check ohne Auth — für Cloudflare/Monitoring."""
    return {
        "status": "ok",
        "service": "barto-link",
        "version": app.version,
    }


# ------------------------------------------------------------------------------
#  Token-Endpoints
# ------------------------------------------------------------------------------

@app.post(
    "/tokens/register",
    response_model=TokenRegisterResponse,
    dependencies=[Depends(verify_token)],
)
def register_token(payload: TokenRegisterRequest) -> TokenRegisterResponse:
    """iOS-App meldet einen frisch erhaltenen APNs-Token.

    Idempotent: Wenn der Token schon existiert, wird er aktualisiert.
    """
    token = register_or_update(
        token=payload.token,
        bundle_id=payload.bundle_id,
        environment=payload.environment,
        device_label=payload.device_label,
    )
    return _to_register_response(token)


@app.get(
    "/tokens/list",
    response_model=list[TokenInfo],
    dependencies=[Depends(verify_token)],
)
def list_tokens() -> list[TokenInfo]:
    """Alle aktiven Tokens — Admin/Debug."""
    return [_to_token_info(t) for t in list_active()]


# ------------------------------------------------------------------------------
#  Push-Endpoint
# ------------------------------------------------------------------------------

@app.post(
    "/push",
    response_model=PushResponse,
    dependencies=[Depends(verify_token)],
)
async def push(payload: PushRequest) -> PushResponse:
    """Schickt einen Push an alle aktiven Tokens.

    Bei BadDeviceToken-Antwort wird der Token automatisch deaktiviert,
    damit er beim nächsten Send nicht mehr angeschrieben wird.

    V1: Schickt an ALLE aktiven Tokens.
    V2: Pro-Token-Routing (welcher Source darf an welche Tokens).
    """
    tokens = list_active(bundle_id=settings.apple_bundle_id)

    if not tokens:
        logger.warning(
            "Push angefordert (source=%s), aber keine aktiven Tokens registriert.",
            payload.source,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keine aktiven Empfänger registriert",
        )

    apns_payload = PushPayload(
        title=payload.title,
        body=payload.body,
        source=payload.source,
        priority=payload.priority,
        sound=payload.sound,
        badge=payload.badge,
        meta=payload.meta,
    )

    sent = 0
    failed = 0
    details: list[dict] = []

    for device in tokens:
        try:
            result = await apns.send(device.token, apns_payload)
        except Exception as e:
            logger.exception("Send-Exception für Token %s: %s", device.token[:16], e)
            failed += 1
            details.append({
                "token_id": device.id,
                "status": "exception",
                "description": str(e),
            })
            continue

        if str(result.status) == "200":
            sent += 1
            details.append({
                "token_id": device.id,
                "status": "200",
                "description": "Success",
            })
        else:
            failed += 1
            details.append({
                "token_id": device.id,
                "status": str(result.status),
                "description": result.description or "",
            })

            # BadDeviceToken / Unregistered → Token deaktivieren
            if result.description in ("BadDeviceToken", "Unregistered"):
                deactivate(device.token)
                logger.info(
                    "Token automatisch deaktiviert (Apple-Antwort: %s): %s...",
                    result.description,
                    device.token[:16],
                )

    logger.info(
        "Push-Versand fertig (source=%s): %d gesendet, %d fehlgeschlagen",
        payload.source,
        sent,
        failed,
    )
    return PushResponse(sent_to=sent, failed=failed, details=details)


# ------------------------------------------------------------------------------
#  Helpers
# ------------------------------------------------------------------------------

def _to_register_response(t: DeviceToken) -> TokenRegisterResponse:
    return TokenRegisterResponse(
        id=t.id,
        token_preview=t.token[:16] + "...",
        is_active=t.is_active,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _to_token_info(t: DeviceToken) -> TokenInfo:
    return TokenInfo(
        id=t.id,
        token_preview=t.token[:16] + "...",
        bundle_id=t.bundle_id,
        environment=t.environment,
        device_label=t.device_label,
        created_at=t.created_at,
        updated_at=t.updated_at,
        is_active=t.is_active,
    )