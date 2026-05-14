# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : models.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
#  Geändert am . : 04.05.2026  — Trip-Schemas für DBTicker-Erweiterung hinzugefügt
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Pydantic-Schemas für Request/Response der HTTP-API.
#                  Trennung von SQLite-DataClasses (in tokens.py / trips.py):
#                  hier nur API, dort die Persistenz. Frontend-Backend-Vertrag.
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------------------------
#  /tokens/register
# ------------------------------------------------------------------------------

class TokenRegisterRequest(BaseModel):
    """Was die iOS-App sendet, wenn sie ihren APNs-Token meldet."""
    token: str = Field(..., min_length=64, max_length=200, description="APNs-Hex-Token")
    bundle_id: str = Field(..., description="Bundle-ID der App, z.B. 'com.barto.bartolink'")
    environment: Literal["sandbox", "production"]
    device_label: Optional[str] = Field(default=None, max_length=80)


class TokenRegisterResponse(BaseModel):
    id: int
    token_preview: str         # nur erste 16 Zeichen — voller Token nicht zurückspielen
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------------------
#  /push
# ------------------------------------------------------------------------------

class PushRequest(BaseModel):
    """Was Tools (dbticker, mailcontrol, …) schicken, wenn sie einen Push wollen."""
    title: str = Field(..., max_length=200)
    body: str = Field(..., max_length=2000)
    source: str = Field(default="system", max_length=50, description="z.B. 'dbticker'")
    priority: int = Field(default=5, ge=1, le=10, description="APNs-Priority")
    sound: str = Field(default="default")
    badge: Optional[int] = Field(default=None, ge=0)
    meta: Optional[dict] = None


class PushResponse(BaseModel):
    sent_to: int
    failed: int
    details: list[dict]


# ------------------------------------------------------------------------------
#  /tokens/list (Admin)
# ------------------------------------------------------------------------------

class TokenInfo(BaseModel):
    id: int
    token_preview: str
    bundle_id: str
    environment: str
    device_label: Optional[str]
    created_at: datetime
    updated_at: datetime
    is_active: bool


# ==============================================================================
#  Trip-Endpoints (DBTicker-Erweiterung — Sprint 1)
# ==============================================================================

TripStatusLiteral = Literal["on_time", "delayed", "cancelled", "not_found"]
EventTypeLiteral = Literal[
    "delay", "platform_change", "cancelled",
    "on_time", "not_found", "manual_refresh",
]


# ------------------------------------------------------------------------------
#  POST /trips/events
# ------------------------------------------------------------------------------

class TripEventRequest(BaseModel):
    """Was dbticker schickt, wenn es einen neuen Stand für eine Fahrt hat."""
    train_number: str = Field(..., max_length=20)
    route_id: str = Field(..., max_length=50, description="dbticker-Route-ID")
    departure_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$",
                                description="ISO-Date YYYY-MM-DD")

    line: str = Field(..., max_length=20)
    direction: str = Field(..., max_length=120)
    planned_departure: str = Field(..., pattern=r"^\d{2}:\d{2}$")

    departure_station: Optional[str] = Field(default=None, max_length=120)
    arrival_station: Optional[str] = Field(default=None, max_length=120)
    planned_platform: Optional[str] = Field(default=None, max_length=20)

    status: TripStatusLiteral = "on_time"
    delay_min: Optional[int] = Field(default=None, ge=0, le=600)
    current_platform: Optional[str] = Field(default=None, max_length=20)
    # DBTicker Hint: Filter umgehen oder nicht? — z.B. bei All-Clear/Entwarnung, damit die Pushs trotzdem rausgehen.
    message: Optional[str] = Field(default=None, max_length=500)
    event_intent: Literal["regular", "force_push", "silent_observation"] = Field(
        default="regular",
        description=(
            "dbticker-Hint, wie das Event behandelt werden soll. "
            "'force_push' = umgeht den Filter (All-Clear). "
            "'silent_observation' = nur Statistik, kein Event/Push."
        ),
    )


class TripEventResponse(BaseModel):
    """Antwort an dbticker: was BartoLink mit dem Event gemacht hat."""
    trip_key: str
    event_type: EventTypeLiteral
    push_sent: bool
    push_recipients: int = 0


# ------------------------------------------------------------------------------
#  GET /trips
# ------------------------------------------------------------------------------

class TripSummary(BaseModel):
    """Eine Trip-Zeile für die Inbox-Liste."""
    trip_key: str
    line: str
    train_number: str
    direction: str
    route_id: str
    planned_departure: str
    current_status: TripStatusLiteral
    current_delay_min: Optional[int]
    current_platform: Optional[str]
    last_update_at: datetime


# ------------------------------------------------------------------------------
#  GET /trips/{trip_key}
# ------------------------------------------------------------------------------

class TripEventEntry(BaseModel):
    """Ein einzelner Eintrag der DetailView-History."""
    id: int
    event_type: EventTypeLiteral
    delay_min: Optional[int]
    platform: Optional[str]
    message: Optional[str]
    pushed_visible: bool
    received_at: datetime


class TripDetailResponse(BaseModel):
    """Vollständige Trip-Daten + chronologische Event-History."""
    trip: TripSummary
    planned_platform: Optional[str]
    departure_station: Optional[str]
    arrival_station: Optional[str]
    created_at: datetime
    events: list[TripEventEntry]


# ------------------------------------------------------------------------------
#  POST /trips/{trip_key}/refresh
# ------------------------------------------------------------------------------

class TripRefreshResponse(BaseModel):
    """Antwort bei erfolgreichem Refresh."""
    trip: TripSummary
    refreshed_at: datetime
    next_refresh_allowed_at: datetime


class TripRefreshThrottled(BaseModel):
    """Antwort bei geblocktem Refresh (HTTP 429)."""
    retry_after_seconds: int
    reason: Literal["per_trip", "global"]