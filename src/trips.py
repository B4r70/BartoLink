# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : trips.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 04.05.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Persistenz-Layer für DBTicker-Trips und ihre Event-History.
#                  Eine Zeile pro Fahrt (trip_updates) plus eine Event-Liste
#                  (trip_events) für die DetailView-History.
#
#                  Trip-Key-Format: {train_number}_{date}_{route_id}
#                                   z.B. "12623_2026-05-04_hin-0631"
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Literal, Optional
from zoneinfo import ZoneInfo

from src.config import settings


logger = logging.getLogger(__name__)
BERLIN = ZoneInfo("Europe/Berlin")


# ------------------------------------------------------------------------------
#  TZ-Aware-Datetime-Adapter
# ------------------------------------------------------------------------------
# SQLite's PARSE_DECLTYPES-Default liefert TIMESTAMP-Spalten als tz-naive
# datetime — auch wenn wir tz-aware Werte reinschreiben. Wir registrieren einen
# Converter, der die Berlin-Zeitzone beim Lesen wieder anhängt. Sonst knallt
# jede Subtraktion zwischen `datetime.now(BERLIN)` und einem DB-Wert mit:
#   TypeError: can't subtract offset-naive and offset-aware datetimes
def _tz_aware_timestamp(val: bytes) -> datetime:
    s = val.decode()
    return datetime.fromisoformat(s).replace(tzinfo=BERLIN)


sqlite3.register_converter("TIMESTAMP", _tz_aware_timestamp)
sqlite3.register_converter("timestamp", _tz_aware_timestamp)


# Mögliche Status-Werte einer Fahrt.
# "not_found" = Zug ist nicht im Plan zu finden (z.B. Linienänderung).
TripStatus = Literal["on_time", "delayed", "cancelled", "not_found"]

# Event-Typen für die History.
# Wird in trip_events.event_type gespeichert.
EventType = Literal[
    "delay",            # Verspätung gemeldet/aktualisiert
    "platform_change",  # Gleiswechsel
    "cancelled",        # Zug fällt aus
    "on_time",          # zurück zu pünktlich (Entwarnung)
    "not_found",        # Zug nicht im Plan
    "manual_refresh",   # Nutzer hat Refresh-Button gedrückt
]

# Mapping Status → Event-Type. Als Modul-Konstante mit explizitem Type-Hint,
# damit Pyright die EventType-Literale durch den Dict-Lookup hindurch verfolgt.
_STATUS_TO_EVENT_TYPE: dict[TripStatus, EventType] = {
    "delayed": "delay",
    "cancelled": "cancelled",
    "on_time": "on_time",
    "not_found": "not_found",
}


# ------------------------------------------------------------------------------
#  Datamodel
# ------------------------------------------------------------------------------

@dataclass
class TripUpdate:
    """Aktueller Stand einer konkreten Fahrt (Zugnummer + Datum + Route)."""
    trip_key: str                          # PK: "12623_2026-05-04_hin-0631"
    line: str                              # "RB23"
    train_number: str                      # "12623"
    direction: str                         # "Nassau(Lahn)"
    route_id: str                          # "hin-0631" — dbticker-Route-ID
    planned_departure: str                 # "16:16" (HH:MM)
    current_status: TripStatus             # aktueller Stand
    current_delay_min: Optional[int]       # NULL bei on_time/cancelled/not_found
    current_platform: Optional[str]        # z.B. "3"
    planned_platform: Optional[str]        # für Gleiswechsel-Erkennung
    departure_station: Optional[str]       # "Niederlahnstein"
    arrival_station: Optional[str]         # "Bad Ems"
    last_update_at: datetime               # letzter Touch (egal ob Push oder silent)
    created_at: datetime                   # erste Anlage


@dataclass
class TripEvent:
    """Eine einzelne Statusänderung — chronologisch in der DetailView."""
    id: int
    trip_key: str
    event_type: EventType
    delay_min: Optional[int]
    platform: Optional[str]
    message: Optional[str]                 # Roh-Text, optional (z.B. Messagecode)
    pushed_visible: bool                   # wurde dafür ein sichtbarer Push verschickt?
    received_at: datetime


# ------------------------------------------------------------------------------
#  Connection-Handling
# ------------------------------------------------------------------------------

@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Liefert eine SQLite-Connection mit Row-Factory + Auto-Commit beim Exit.

    Identisch zum Pattern in tokens.py — gleicher Stil, gleiche DB-Datei.
    """
    connection = sqlite3.connect(
        settings.database_path,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# ------------------------------------------------------------------------------
#  Schema
# ------------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trip_updates (
    trip_key            TEXT PRIMARY KEY,
    line                TEXT NOT NULL,
    train_number        TEXT NOT NULL,
    direction           TEXT NOT NULL,
    route_id            TEXT NOT NULL,
    planned_departure   TEXT NOT NULL,
    current_status      TEXT NOT NULL CHECK(current_status IN
                            ('on_time', 'delayed', 'cancelled', 'not_found')),
    current_delay_min   INTEGER,
    current_platform    TEXT,
    planned_platform    TEXT,
    departure_station   TEXT,
    arrival_station     TEXT,
    last_update_at      TIMESTAMP NOT NULL,
    created_at          TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trip_updates_recent
    ON trip_updates(last_update_at DESC);
CREATE INDEX IF NOT EXISTS idx_trip_updates_route
    ON trip_updates(route_id);

CREATE TABLE IF NOT EXISTS trip_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_key        TEXT NOT NULL REFERENCES trip_updates(trip_key) ON DELETE CASCADE,
    event_type      TEXT NOT NULL CHECK(event_type IN
                        ('delay', 'platform_change', 'cancelled',
                         'on_time', 'not_found', 'manual_refresh')),
    delay_min       INTEGER,
    platform        TEXT,
    message         TEXT,
    pushed_visible  INTEGER NOT NULL DEFAULT 0,
    received_at     TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trip_events_trip
    ON trip_events(trip_key, received_at DESC);
"""


def init_db() -> None:
    """Initialisiert das Trip-Schema, falls die DB neu ist.

    Idempotent — kann beliebig oft aufgerufen werden.
    Wird vom Lifecycle-Hook in main.py mit aufgerufen.
    """
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript(SCHEMA_SQL)
    logger.info("Trip-Schema initialisiert.")


# ------------------------------------------------------------------------------
#  Trip-Upsert + Event-Logging
# ------------------------------------------------------------------------------

@dataclass
class TripEventInput:
    """Was dbticker (oder der Refresh-Endpoint) als neues Event reinreicht."""
    # Identifikation der Fahrt
    train_number: str
    route_id: str                       # dbticker-Route-ID, z.B. "hin-0631"
    departure_date: str                 # ISO-Date "YYYY-MM-DD"

    # Stammdaten der Fahrt
    line: str
    direction: str
    planned_departure: str              # "HH:MM"
    departure_station: Optional[str] = None
    arrival_station: Optional[str] = None
    planned_platform: Optional[str] = None

    # Aktueller Stand
    status: TripStatus = "on_time"
    delay_min: Optional[int] = None
    current_platform: Optional[str] = None
    message: Optional[str] = None       # z.B. Messagecode-Text

    # Wenn True: Event ist ein manueller Refresh (auch wenn nichts neu ist)
    is_manual_refresh: bool = False

    # NEU: Hint von dbticker. "force_push" überspringt den Initial-on_time-Filter
    # (z.B. für All-Clear-Meldungen, die explizit gewollt sind).
    event_intent: Literal["regular", "force_push"] = "regular"


def build_trip_key(train_number: str, departure_date: str, route_id: str) -> str:
    """Konstruiert den Trip-Key nach vereinbartem Format.

    Beispiel:
        build_trip_key("12623", "2026-05-04", "hin-0631")
        → "12623_2026-05-04_hin-0631"
    """
    return f"{train_number}_{departure_date}_{route_id}"


def record_event(event: TripEventInput) -> tuple[TripUpdate, TripEvent, bool]:
    """Speichert ein neues Event und upsertet den Trip.

    Returns:
        (trip_update, trip_event, push_visible) — push_visible sagt dem Caller,
        ob ein sichtbares Push-Banner geschickt werden soll. Die Logik dafür
        sitzt hier zentral, damit dbticker und der Refresh-Endpoint sich gleich
        verhalten.
    """
    trip_key = build_trip_key(event.train_number, event.departure_date, event.route_id)
    now = datetime.now(BERLIN)

    with _conn() as con:
        # Bestehenden Trip laden, falls vorhanden — für Vergleich (Push-Entscheidung)
        prev_row = con.execute(
            "SELECT * FROM trip_updates WHERE trip_key = ?",
            (trip_key,),
        ).fetchone()

        # --- Push-Entscheidung treffen ---
        event_type, push_visible = _classify_event(prev_row, event)

        # --- Trip upserten ---
        if prev_row is None:
            con.execute(
                """
                INSERT INTO trip_updates (
                    trip_key, line, train_number, direction, route_id,
                    planned_departure, current_status, current_delay_min,
                    current_platform, planned_platform,
                    departure_station, arrival_station,
                    last_update_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trip_key, event.line, event.train_number, event.direction,
                    event.route_id, event.planned_departure, event.status,
                    event.delay_min, event.current_platform, event.planned_platform,
                    event.departure_station, event.arrival_station,
                    now, now,
                ),
            )
        else:
            con.execute(
                """
                UPDATE trip_updates
                   SET line               = ?,
                       direction          = ?,
                       planned_departure  = ?,
                       current_status     = ?,
                       current_delay_min  = ?,
                       current_platform   = ?,
                       planned_platform   = COALESCE(?, planned_platform),
                       departure_station  = COALESCE(?, departure_station),
                       arrival_station    = COALESCE(?, arrival_station),
                       last_update_at     = ?
                 WHERE trip_key = ?
                """,
                (
                    event.line, event.direction, event.planned_departure,
                    event.status, event.delay_min, event.current_platform,
                    event.planned_platform,
                    event.departure_station, event.arrival_station,
                    now, trip_key,
                ),
            )

        # --- Event in History eintragen ---
        cursor = con.execute(
            """
            INSERT INTO trip_events
                   (trip_key, event_type, delay_min, platform,
                    message, pushed_visible, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trip_key, event_type, event.delay_min, event.current_platform,
                event.message, int(push_visible), now,
            ),
        )
        event_id = cursor.lastrowid
        assert event_id is not None, "INSERT INTO trip_events lieferte keine lastrowid"

        # --- Resultat zusammenbauen für Return ---
        trip = _row_to_trip(con.execute(
            "SELECT * FROM trip_updates WHERE trip_key = ?",
            (trip_key,),
        ).fetchone())

        trip_event = TripEvent(
            id=event_id,
            trip_key=trip_key,
            event_type=event_type,
            delay_min=event.delay_min,
            platform=event.current_platform,
            message=event.message,
            pushed_visible=push_visible,
            received_at=now,
        )

    logger.info(
        "Event aufgezeichnet: trip=%s, event=%s, push_visible=%s",
        trip_key, event_type, push_visible,
    )
    return trip, trip_event, push_visible


def _classify_event(
    prev_row: Optional[sqlite3.Row],
    new: TripEventInput,
) -> tuple[EventType, bool]:
    """Klassifiziert das Event und entscheidet, ob ein sichtbarer Push raus geht.

    Logik (Reihenfolge wichtig — höchste Priorität zuerst):
      - Manueller Refresh: nie sichtbar pushen (Nutzer schaut eh hin).
      - Erste Meldung: sichtbar pushen (außer on_time + initial — kein Spam).
      - Statuswechsel: sichtbar pushen.
      - Gleisänderung: sichtbar pushen (Edge-Case-Fix!).
      - Verspätungs-Update: nur wenn Delta ≥ 3 Min.
      - Sonst: silent (DB-Update, kein Banner).
    """
    if new.is_manual_refresh:
        return ("manual_refresh", False)

    event_type: EventType = _STATUS_TO_EVENT_TYPE[new.status]

    # Force-Push überspringt alle weiteren Filter.
    if new.event_intent == "force_push":
        return (event_type, True)

    # --- Erstmeldung ---
    if prev_row is None:
        # Initiale on_time-Anlage ist nicht alarmwürdig → silent
        if new.status == "on_time":
            return (event_type, False)
        return (event_type, True)

    # --- Vergleich mit vorherigem Stand ---
    prev_status = prev_row["current_status"]
    prev_delay = prev_row["current_delay_min"]
    prev_platform = prev_row["current_platform"]

    # Statuswechsel ist immer sichtbar (delayed→on_time, on_time→cancelled, etc.)
    if new.status != prev_status:
        return (event_type, True)

    # Gleisänderung ist immer sichtbar — auch bei sonst gleichem Status.
    # Wenn der Event-Typ "delay" wäre, überschreiben wir auf "platform_change",
    # damit die DetailView-History das eindeutig markiert.
    if (new.current_platform is not None
            and prev_platform is not None
            and new.current_platform != prev_platform):
        return ("platform_change", True)

    # Verspätungs-Delta-Check (nur relevant bei status=delayed)
    if new.status == "delayed" and new.delay_min is not None and prev_delay is not None:
        if abs(new.delay_min - prev_delay) >= 3:
            return (event_type, True)

    # Sonst: silent
    return (event_type, False)


# ------------------------------------------------------------------------------
#  Lese-Funktionen
# ------------------------------------------------------------------------------

def list_trips(*, limit: int = 100) -> list[TripUpdate]:
    """Alle Trips, sortiert nach last_update_at DESC — für Inbox-Liste."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trip_updates ORDER BY last_update_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_trip(r) for r in rows]


def get_trip(trip_key: str) -> Optional[TripUpdate]:
    """Einzelner Trip — None wenn unbekannt."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM trip_updates WHERE trip_key = ?",
            (trip_key,),
        ).fetchone()
    return _row_to_trip(row) if row else None


def get_events(trip_key: str, *, limit: int = 100) -> list[TripEvent]:
    """Event-History eines Trips — älteste zuerst (chronologisch für UI)."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT * FROM trip_events
             WHERE trip_key = ?
             ORDER BY received_at ASC
             LIMIT ?
            """,
            (trip_key, limit),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


# ------------------------------------------------------------------------------
#  Row-Mapper
# ------------------------------------------------------------------------------

def _row_to_trip(row: sqlite3.Row) -> TripUpdate:
    return TripUpdate(
        trip_key=row["trip_key"],
        line=row["line"],
        train_number=row["train_number"],
        direction=row["direction"],
        route_id=row["route_id"],
        planned_departure=row["planned_departure"],
        current_status=row["current_status"],
        current_delay_min=row["current_delay_min"],
        current_platform=row["current_platform"],
        planned_platform=row["planned_platform"],
        departure_station=row["departure_station"],
        arrival_station=row["arrival_station"],
        last_update_at=row["last_update_at"],
        created_at=row["created_at"],
    )


def _row_to_event(row: sqlite3.Row) -> TripEvent:
    return TripEvent(
        id=row["id"],
        trip_key=row["trip_key"],
        event_type=row["event_type"],
        delay_min=row["delay_min"],
        platform=row["platform"],
        message=row["message"],
        pushed_visible=bool(row["pushed_visible"]),
        received_at=row["received_at"],
    )