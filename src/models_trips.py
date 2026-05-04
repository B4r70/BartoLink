# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : models_trips.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 04.05.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Pydantic-Schemas für die Trip-Endpoints.
#                  Trennung von SQLite-DataClasses (in trips.py): hier nur API.
#
#  Hinweis       : Inhalte am Ende von src/models.py einfügen (oder als
#                  separate Datei und in main.py importieren — Geschmackssache).
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


TripStatusLiteral = Literal["on_time", "delayed", "cancelled", "not_found"]
EventTypeLiteral = Literal[
    "delay", "platform_change", "cancelled",
    "on_time", "not_found", "manual_refresh",
]


# ------------------------------------------------------------------------------
#  POST /trips/events  —  dbticker meldet eine neue Beobachtung
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
    message: Optional[str] = Field(default=None, max_length=500)


class TripEventResponse(BaseModel):
    """Antwort an dbticker: was BartoLink mit dem Event gemacht hat."""
    trip_key: str
    event_type: EventTypeLiteral
    push_sent: bool
    push_recipients: int = 0


# ------------------------------------------------------------------------------
#  GET /trips  —  Inbox-Liste (gruppiert pro Trip)
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
#  GET /trips/{trip_key}  —  Detail mit voller History
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
#  POST /trips/{trip_key}/refresh  —  Manueller Refresh
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