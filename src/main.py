# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : main.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
#  Geändert am . : 04.05.2026  — Trip-Endpoints für DBTicker-Erweiterung hinzugefügt
# ------------------------------------------------------------------------------------------
#  Beschreibung  : FastAPI-Service für Push-Notifications + Trip-Aggregation.
#                  Token-Registrierungen, Push-Requests, plus DBTicker-Trips
#                  (Inbox-Liste, DetailView-History, manueller Refresh).
#  Start         : uvicorn src.main:app --host 127.0.0.1 --port 8765
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from src import rate_limit, trips
from src.apns_client import PushPayload, apns
from src.auth import verify_token
from src.config import settings
from src.dbticker_runner import run_for_route
from src.models import (
    PushRequest,
    PushResponse,
    TokenInfo,
    TokenRegisterRequest,
    TokenRegisterResponse,
    TripDetailResponse,
    TripEventEntry,
    TripEventRequest,
    TripEventResponse,
    TripRefreshResponse,
    TripRefreshThrottled,
    TripSummary,
)
from src.tokens import (
    DeviceToken,
    deactivate,
    init_db,
    list_active,
    register_or_update,
)
from src.trips import TripEventInput


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
    init_db()                # Tokens-Schema (bestehend)
    trips.init_db()          # Trip-Schema (NEU)
    rate_limit.init_db()     # Refresh-Log-Schema (NEU)
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
    version="0.2.0",
    description="Personal push-notification gateway + DBTicker-Aggregation",
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
#  Push-Endpoint (generisch)
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


# ==============================================================================
#  Trip-Endpoints (DBTicker-Erweiterung)
# ==============================================================================

@app.post(
    "/trips/events",
    response_model=TripEventResponse,
    dependencies=[Depends(verify_token)],
)
async def submit_trip_event(payload: TripEventRequest) -> TripEventResponse:
    """Nimmt einen Stand-Bericht von dbticker entgegen.

    Aggregations-Logik in trips.record_event entscheidet, ob ein sichtbarer
    Push raus geht. Falls ja, wird APNs an alle aktiven Tokens versendet.

    Sonderfall event_intent="silent_observation":
        Reine Statistik-Beobachtung — kein trip_event, kein Push, nur ein
        trip_observations-Eintrag und ein last_update_at-Touch.
    """
    event_input = TripEventInput(
        train_number=payload.train_number,
        route_id=payload.route_id,
        departure_date=payload.departure_date,
        line=payload.line,
        direction=payload.direction,
        planned_departure=payload.planned_departure,
        departure_station=payload.departure_station,
        arrival_station=payload.arrival_station,
        planned_platform=payload.planned_platform,
        status=payload.status,
        delay_min=payload.delay_min,
        current_platform=payload.current_platform,
        message=payload.message,
        is_manual_refresh=False,
        event_intent=payload.event_intent,
        minutes_to_departure=payload.minutes_to_departure,
    )

    # --- Silent observation: früh raus, kein Event, kein Push ---
    if payload.event_intent == "silent_observation":
        trip = trips.record_silent_observation(event_input)
        return TripEventResponse(
            trip_key=trip.trip_key,
            event_type="on_time",   # Dummy — Client nutzt den Wert nicht
            push_sent=False,
            push_recipients=0,
        )

    # --- Regulärer Pfad: klassifizieren, eintragen, ggf. pushen ---
    trip, event, push_visible = trips.record_event(event_input)

    push_recipients = 0
    if push_visible:
        title, body = _format_push(trip, event)
        push_payload = PushPayload(
            title=title,
            body=body,
            source="dbticker",
            priority=10,
            meta=_build_trip_meta(trip, event),
        )
        push_recipients = await _send_push_to_all(push_payload)

    return TripEventResponse(
        trip_key=trip.trip_key,
        event_type=event.event_type,
        push_sent=push_visible,
        push_recipients=push_recipients,
    )


@app.get(
    "/trips",
    response_model=list[TripSummary],
    dependencies=[Depends(verify_token)],
)
def list_trips_endpoint(limit: int = 100) -> list[TripSummary]:
    """Alle bekannten Trips, sortiert nach last_update_at DESC — für Inbox."""
    return [_to_summary(t) for t in trips.list_trips(limit=limit)]


@app.get(
    "/trips/{trip_key}",
    response_model=TripDetailResponse,
    dependencies=[Depends(verify_token)],
)
def get_trip_endpoint(trip_key: str) -> TripDetailResponse:
    """Trip-Detail mit chronologischer Event-Liste für die DetailView."""
    trip = trips.get_trip(trip_key)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trip nicht bekannt: {trip_key}",
        )
    events = trips.get_events(trip_key)
    return TripDetailResponse(
        trip=_to_summary(trip),
        planned_platform=trip.planned_platform,
        departure_station=trip.departure_station,
        arrival_station=trip.arrival_station,
        created_at=trip.created_at,
        events=[_to_event_entry(e) for e in events],
    )


@app.post(
    "/trips/{trip_key}/refresh",
    dependencies=[Depends(verify_token)],
)
async def refresh_trip_endpoint(trip_key: str):
    """Triggert einen sofortigen Refresh für genau diese Fahrt.

    Antworten:
      200: TripRefreshResponse — neuer Stand
      404: Trip unbekannt
      429: Throttled (mit retry_after_seconds + reason)
      502: dbticker-Aufruf fehlgeschlagen
    """
    trip = trips.get_trip(trip_key)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trip nicht bekannt: {trip_key}",
        )

    throttle = rate_limit.check_and_record(trip_key)
    if not throttle.allowed:
        assert throttle.reason is not None, "ThrottleResult mit !allowed muss reason haben"
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=TripRefreshThrottled(
                retry_after_seconds=throttle.retry_after_seconds,
                reason=throttle.reason,
            ).model_dump(),
            headers={"Retry-After": str(throttle.retry_after_seconds)},
        )

    runner_result = await run_for_route(trip.route_id)
    if not runner_result.success:
        logger.error(
            "Refresh fehlgeschlagen für trip=%s, route=%s: rc=%d, stderr=%s",
            trip_key, trip.route_id, runner_result.return_code,
            runner_result.stderr[:500],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="dbticker-Aufruf fehlgeschlagen — versuch's gleich nochmal.",
        )

    refreshed = trips.get_trip(trip_key)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Trip nach Refresh nicht mehr in DB.",
        )

    now = refreshed.last_update_at
    next_allowed = now + timedelta(seconds=rate_limit.PER_TRIP_COOLDOWN_SECONDS)

    return TripRefreshResponse(
        trip=_to_summary(refreshed),
        refreshed_at=now,
        next_refresh_allowed_at=next_allowed,
    )


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


def _to_summary(t: trips.TripUpdate) -> TripSummary:
    return TripSummary(
        trip_key=t.trip_key,
        line=t.line,
        train_number=t.train_number,
        direction=t.direction,
        route_id=t.route_id,
        planned_departure=t.planned_departure,
        current_status=t.current_status,
        current_delay_min=t.current_delay_min,
        current_platform=t.current_platform,
        last_update_at=t.last_update_at,
    )


def _to_event_entry(e: trips.TripEvent) -> TripEventEntry:
    return TripEventEntry(
        id=e.id,
        event_type=e.event_type,
        delay_min=e.delay_min,
        platform=e.platform,
        message=e.message,
        pushed_visible=e.pushed_visible,
        received_at=e.received_at,
    )


def _build_trip_meta(trip: trips.TripUpdate, event: trips.TripEvent) -> dict:
    """Baut das vollständige meta-Dict für einen Trip-Push.

    Die iOS-App nutzt diese Felder, um:
      - Notifications nach trip_key zu gruppieren (Inbox)
      - DetailView mit Trip-Stammdaten zu rendern
      - Event-History korrekt zu typisieren (event_type)

    Datums-/Zeitfelder werden als ISO-Strings serialisiert, damit sie
    durch APNs-JSON kommen. iOS decodiert sie via ISO8601DateFormatter.
    """
    return {
        # Identifikation der Fahrt
        "trip_key": trip.trip_key,
        "event_id": event.id,
        "event_type": event.event_type,

        # Trip-Stammdaten (für DetailView-Header)
        "line": trip.line,
        "train_number": trip.train_number,
        "direction": trip.direction,
        "route_id": trip.route_id,
        "planned_departure": trip.planned_departure,        # "HH:MM"
        "planned_platform": trip.planned_platform,
        "departure_station": trip.departure_station,
        "arrival_station": trip.arrival_station,

        # Aktueller Stand (für Inbox-Badge + DetailView-Hauptkachel)
        "current_status": trip.current_status,
        "current_delay_min": trip.current_delay_min,
        "current_platform": trip.current_platform,

        # Event-spezifische Felder (was hat sich GENAU geändert?)
        "event_delay_min": event.delay_min,
        "event_platform": event.platform,
        "event_message": event.message,
    }


def _format_push(trip: trips.TripUpdate, event: trips.TripEvent) -> tuple[str, str]:
    """Formuliert Title/Body für die iOS-Push-Notification.

    Title:   knapp, identifiziert die Fahrt.
    Body:    sagt, was sich geändert hat — höchstens 2 Zeilen.
    """
    title = f"{trip.line} → {trip.direction}"

    if event.event_type == "platform_change":
        body = f"Gleisänderung: jetzt Gleis {event.platform}"
    elif event.event_type == "cancelled":
        body = "Zug fällt aus"
    elif event.event_type == "on_time":
        body = "Wieder pünktlich"
    elif event.event_type == "not_found":
        body = "Zug nicht im Plan"
    elif event.event_type == "delay" and event.delay_min is not None:
        body = f"Verspätung: +{event.delay_min} Min"
        if event.platform and trip.planned_platform and event.platform != trip.planned_platform:
            body += f" · Gleis {event.platform}"
    else:
        body = "Update"

    body += f" · ab {trip.planned_departure}"
    return title, body


async def _send_push_to_all(push_payload: PushPayload) -> int:
    """Sendet einen Push an alle aktiven Tokens. Gibt Empfängerzahl zurück.

    Wiederverwendung der Push-Pipeline aus dem /push-Endpoint, aber kompakter:
    keine Detail-Liste — wir interessieren uns nur für den Count.
    Bei BadDeviceToken/Unregistered wird der Token deaktiviert.
    """
    active = list_active(bundle_id=settings.apple_bundle_id)
    if not active:
        return 0

    sent = 0
    for device in active:
        try:
            result = await apns.send(device.token, push_payload)
        except Exception as e:
            logger.error("Push-Versand fehlgeschlagen für %s...: %s",
                         device.token[:16], e)
            continue

        if str(result.status) == "200":
            sent += 1
        elif result.description in ("BadDeviceToken", "Unregistered"):
            deactivate(device.token)
            logger.info(
                "Token automatisch deaktiviert (Apple-Antwort: %s): %s...",
                result.description, device.token[:16],
            )

    return sent