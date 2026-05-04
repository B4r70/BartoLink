# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : rate_limit.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 04.05.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Rate-Limiting für /trips/{trip_key}/refresh.
#                  Zwei Ebenen:
#                    - per_trip:   1 Refresh / 60 Sek pro Trip-Key
#                    - global:     30 Refreshes / Stunde insgesamt
#                  Schützt das DB-API-Free-Tier vor Überlastung durch Tap-Spam.
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator, Literal, Optional
from zoneinfo import ZoneInfo

from src.config import settings


logger = logging.getLogger(__name__)
BERLIN = ZoneInfo("Europe/Berlin")


# ------------------------------------------------------------------------------
#  TZ-Aware-Datetime-Adapter
# ------------------------------------------------------------------------------
# SQLite's PARSE_DECLTYPES-Default liefert TIMESTAMP-Spalten als tz-naive
# datetime. Wir registrieren einen Converter, der beim Lesen die Berlin-Zeit-
# zone wieder anhängt — sonst knallt die Subtraktion mit `datetime.now(BERLIN)`.
# (Mehrfach-Registrierung über mehrere Module ist harmlos und idempotent.)
def _tz_aware_timestamp(val: bytes) -> datetime:
    s = val.decode()
    return datetime.fromisoformat(s).replace(tzinfo=BERLIN)


sqlite3.register_converter("TIMESTAMP", _tz_aware_timestamp)
sqlite3.register_converter("timestamp", _tz_aware_timestamp)


# Limits — könnten später in settings wandern, fürs Erste hartkodiert.
PER_TRIP_COOLDOWN_SECONDS = 60
GLOBAL_LIMIT_PER_HOUR = 30


ThrottleReason = Literal["per_trip", "global"]


# ------------------------------------------------------------------------------
#  Datamodel
# ------------------------------------------------------------------------------

@dataclass
class ThrottleResult:
    """Ergebnis einer Refresh-Anfrage-Prüfung."""
    allowed: bool
    retry_after_seconds: int = 0        # Wenn !allowed: wann darf wieder?
    reason: Optional[ThrottleReason] = None


# ------------------------------------------------------------------------------
#  Connection-Handling
# ------------------------------------------------------------------------------

@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
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
CREATE TABLE IF NOT EXISTS refresh_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_key        TEXT,                       -- NULL bei global-throttle-hits
    requested_at    TIMESTAMP NOT NULL,
    was_throttled   INTEGER NOT NULL DEFAULT 0,
    throttle_reason TEXT                        -- 'per_trip' | 'global' | NULL
);

CREATE INDEX IF NOT EXISTS idx_refresh_log_recent
    ON refresh_log(requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_refresh_log_trip
    ON refresh_log(trip_key, requested_at DESC);
"""


def init_db() -> None:
    """Initialisiert das Refresh-Log-Schema. Idempotent."""
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript(SCHEMA_SQL)
    logger.info("Refresh-Log-Schema initialisiert.")


# ------------------------------------------------------------------------------
#  Check + Record
# ------------------------------------------------------------------------------

def check_and_record(trip_key: str) -> ThrottleResult:
    """Prüft, ob ein Refresh erlaubt ist, und loggt die Anfrage.

    Wichtig: Diese Funktion loggt JEDEN Aufruf — auch geblockte. So sehen wir
    in den Daten, wie oft Nutzer trotz Cooldown auf den Button tappen.

    Reihenfolge der Checks:
      1. Per-Trip-Cooldown (häufigster Fall — Tap-Spam auf einer DetailView)
      2. Globales Stunden-Limit
    Wenn beide passen, wird der erfolgreiche Refresh geloggt und True zurück.
    """
    now = datetime.now(BERLIN)

    with _conn() as con:
        # --- Check 1: Per-Trip-Cooldown ---
        cutoff_per_trip = now - timedelta(seconds=PER_TRIP_COOLDOWN_SECONDS)
        last_for_trip = con.execute(
            """
            SELECT requested_at FROM refresh_log
             WHERE trip_key = ?
               AND was_throttled = 0
               AND requested_at > ?
             ORDER BY requested_at DESC
             LIMIT 1
            """,
            (trip_key, cutoff_per_trip),
        ).fetchone()

        if last_for_trip is not None:
            elapsed = (now - last_for_trip["requested_at"]).total_seconds()
            retry_after = max(1, int(PER_TRIP_COOLDOWN_SECONDS - elapsed))
            _log(con, trip_key, now, throttled=True, reason="per_trip")
            return ThrottleResult(
                allowed=False,
                retry_after_seconds=retry_after,
                reason="per_trip",
            )

        # --- Check 2: Globales Stunden-Limit ---
        cutoff_hour = now - timedelta(hours=1)
        recent_count = con.execute(
            """
            SELECT COUNT(*) AS n FROM refresh_log
             WHERE was_throttled = 0
               AND requested_at > ?
            """,
            (cutoff_hour,),
        ).fetchone()["n"]

        if recent_count >= GLOBAL_LIMIT_PER_HOUR:
            # Zeit bis zum ältesten Eintrag im Stundenfenster + 1 Sek
            oldest = con.execute(
                """
                SELECT requested_at FROM refresh_log
                 WHERE was_throttled = 0
                   AND requested_at > ?
                 ORDER BY requested_at ASC
                 LIMIT 1
                """,
                (cutoff_hour,),
            ).fetchone()
            retry_after = (
                int((oldest["requested_at"] + timedelta(hours=1) - now).total_seconds()) + 1
                if oldest else 60
            )
            _log(con, trip_key, now, throttled=True, reason="global")
            return ThrottleResult(
                allowed=False,
                retry_after_seconds=max(1, retry_after),
                reason="global",
            )

        # --- Erlaubt — loggen ---
        _log(con, trip_key, now, throttled=False, reason=None)

    return ThrottleResult(allowed=True)


def _log(
    con: sqlite3.Connection,
    trip_key: str,
    when: datetime,
    *,
    throttled: bool,
    reason: Optional[ThrottleReason],
) -> None:
    """Schreibt eine Zeile in refresh_log."""
    con.execute(
        """
        INSERT INTO refresh_log (trip_key, requested_at, was_throttled, throttle_reason)
        VALUES (?, ?, ?, ?)
        """,
        (trip_key, when, int(throttled), reason),
    )