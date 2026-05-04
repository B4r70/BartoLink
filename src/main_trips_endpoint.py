# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : main_trip_endpoints.py  (Snippet, NICHT als eigenes Modul deployen!)
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 04.05.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Trip-Endpoints zum Einbau in src/main.py.
#                  Imports oben hinzufügen, Endpoints unter die /push-Routen setzen.
#                  init_db()-Aufrufe in den lifespan() integrieren.
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================

# === Imports zu src/main.py oben hinzufügen ===
"""
from src import rate_limit, trips
from src.dbticker_runner import run_for_route
from src.models import (
    # ... bestehende ...
    TripEventRequest,
    TripEventResponse,
    TripDetailResponse,
    TripEventEntry,
    TripSummary,
    TripRefreshResponse,
    TripRefreshThrottled,
)
from src.trips import TripEventInput
from datetime import timedelta
"""


# === In lifespan() einfügen, direkt nach dem bestehenden init_db()-Aufruf ===
"""
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("barto-link Backend startet...")
    init_db()                # Tokens — bestehend
    trips.init_db()          # NEU: Trip-Schema
    rate_limit.init_db()     # NEU: Refresh-Log-Schema
    logger.info(...)         # bestehender Log-Block
    yield
    logger.info("barto-link Backend wird beendet.")
"""


# === Endpoints — unter den bestehenden /push-Block einfügen ===

# ------------------------------------------------------------------------------
#  POST /trips/events — dbticker meldet eine neue Beobachtung
# ------------------------------------------------------------------------------

@app.post(
    "/trips/events",
    response_model=TripEventResponse,
    dependencies=[Depends(verify_token)],
)
async def submit_trip_event(payload: TripEventRequest) -> TripEventResponse:
    """Nimmt einen Stand-Bericht von dbticker entgegen.

    Aggregations-Logik in trips.record_event entscheidet, ob ein sichtbarer
    Push raus geht. Falls ja, wird APNs an alle aktiven Tokens versendet.
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
    )

    trip, event, push_visible = trips.record_event(event_input)

    push_recipients = 0
    if push_visible:
        # Push-Banner formulieren — kurz, strukturiert, deutsch
        title, body = _format_push(trip, event)
        push_payload = PushPayload(
            title=title,
            body=body,
            source="dbticker",
            priority=10,                # sofort
            meta={
                "trip_key": trip.trip_key,
                "event_id": event.id,
                "event_type": event.event_type,
            },
        )
        push_recipients = await _send_push_to_all(push_payload)

    return TripEventResponse(
        trip_key=trip.trip_key,
        event_type=event.event_type,
        push_sent=push_visible,
        push_recipients=push_recipients,
    )


# ------------------------------------------------------------------------------
#  GET /trips — Inbox-Liste
# ------------------------------------------------------------------------------

@app.get(
    "/trips",
    response_model=list[TripSummary],
    dependencies=[Depends(verify_token)],
)
def list_trips_endpoint(limit: int = 100) -> list[TripSummary]:
    """Alle bekannten Trips, sortiert nach last_update_at DESC."""
    return [_to_summary(t) for t in trips.list_trips(limit=limit)]


# ------------------------------------------------------------------------------
#  GET /trips/{trip_key} — Detail + History
# ------------------------------------------------------------------------------

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


# ------------------------------------------------------------------------------
#  POST /trips/{trip_key}/refresh — Manueller Refresh
# ------------------------------------------------------------------------------

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
    # --- Trip muss bekannt sein, damit wir die route_id wissen ---
    trip = trips.get_trip(trip_key)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trip nicht bekannt: {trip_key}",
        )

    # --- Rate-Limit prüfen ---
    throttle = rate_limit.check_and_record(trip_key)
    if not throttle.allowed:
        # 429 mit strukturiertem Body — iOS-Client kann reason auswerten
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=TripRefreshThrottled(
                retry_after_seconds=throttle.retry_after_seconds,
                reason=throttle.reason,
            ).model_dump(),
            headers={"Retry-After": str(throttle.retry_after_seconds)},
        )

    # --- dbticker für genau diese Route ausführen ---
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

    # --- dbticker hat ggf. ein neues Event via /trips/events gepostet ---
    # Frischen Stand aus DB lesen für die Response
    refreshed = trips.get_trip(trip_key)
    if refreshed is None:
        # Sollte nicht passieren — Trip existierte ja eben noch
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

    Wiederverwendung der bestehenden Push-Pipeline aus dem alten /push-Endpoint.
    Bei BadDeviceToken wird der Token deaktiviert.
    """
    active = list_active(bundle_id=settings.apple_bundle_id)
    if not active:
        return 0

    sent = 0
    for token_row in active:
        try:
            result = await apns.send(token_row.token, push_payload)
            if result.description == "BadDeviceToken":
                deactivate(token_row.token)
                continue
            sent += 1
        except Exception as e:
            logger.error("Push-Versand fehlgeschlagen für %s...: %s",
                         token_row.token[:16], e)
    return sent