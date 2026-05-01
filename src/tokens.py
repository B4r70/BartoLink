# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : tokens.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Persistenz-Layer für Device-Push-Tokens.
#                  Speichert pro Gerät: Token-Hex, Bundle-ID, Environment,
#                  Erstellungszeit, letztes Update.
#                  SQLite — eine Datei in /var/lib/barto-link/tokens.db.
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

ApnsEnvironment = Literal["sandbox", "production"]


# ------------------------------------------------------------------------------
#  Datamodel
# ------------------------------------------------------------------------------

@dataclass
class DeviceToken:
    """Ein registriertes Empfänger-Device für Push-Notifications."""
    id: int
    token: str                          # 64-Zeichen-Hex
    bundle_id: str                      # z.B. "com.barto.bartolink"
    environment: ApnsEnvironment
    device_label: Optional[str]         # frei wählbar, z.B. "Bartos iPhone 16 Pro"
    created_at: datetime
    updated_at: datetime
    is_active: bool


# ------------------------------------------------------------------------------
#  Connection-Handling
# ------------------------------------------------------------------------------

@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Liefert eine SQLite-Connection mit Row-Factory + Auto-Commit beim Exit."""
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
CREATE TABLE IF NOT EXISTS device_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token           TEXT NOT NULL UNIQUE,
    bundle_id       TEXT NOT NULL,
    environment     TEXT NOT NULL CHECK(environment IN ('sandbox', 'production')),
    device_label    TEXT,
    created_at      TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_tokens_active ON device_tokens(is_active);
CREATE INDEX IF NOT EXISTS idx_tokens_bundle ON device_tokens(bundle_id);
"""


def init_db() -> None:
    """Initialisiert das Schema, falls die DB neu ist.

    Idempotent — kann beliebig oft aufgerufen werden.
    """
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript(SCHEMA_SQL)
    logger.info("Token-DB initialisiert: %s", settings.database_path)


# ------------------------------------------------------------------------------
#  CRUD
# ------------------------------------------------------------------------------

def register_or_update(
    token: str,
    bundle_id: str,
    environment: ApnsEnvironment,
    device_label: Optional[str] = None,
) -> DeviceToken:
    """Registriert einen neuen Token oder aktualisiert einen bestehenden.

    Token sind unique — wenn schon registriert, wird `updated_at` gesetzt
    und ggf. das Label überschrieben. Falls vorher inaktiv: reaktiviert.
    """
    now = datetime.now(BERLIN)

    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM device_tokens WHERE token = ?",
            (token,),
        ).fetchone()

        if existing:
            con.execute(
                """
                UPDATE device_tokens
                   SET bundle_id    = ?,
                       environment  = ?,
                       device_label = COALESCE(?, device_label),
                       updated_at   = ?,
                       is_active    = 1
                 WHERE token = ?
                """,
                (bundle_id, environment, device_label, now, token),
            )
            logger.info("Token aktualisiert: %s...", token[:16])
        else:
            con.execute(
                """
                INSERT INTO device_tokens
                       (token, bundle_id, environment, device_label, created_at, updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (token, bundle_id, environment, device_label, now, now),
            )
            logger.info("Token registriert: %s...", token[:16])

    return _get_by_token(token)


def deactivate(token: str) -> bool:
    """Markiert einen Token als inaktiv (z.B. bei BadDeviceToken-Antwort von Apple).

    Returns:
        True wenn Token gefunden und deaktiviert, False wenn nicht in DB.
    """
    with _conn() as con:
        cursor = con.execute(
            """
            UPDATE device_tokens
               SET is_active  = 0,
                   updated_at = ?
             WHERE token = ?
            """,
            (datetime.now(BERLIN), token),
        )
        if cursor.rowcount > 0:
            logger.info("Token deaktiviert: %s...", token[:16])
            return True
    return False


def list_active(bundle_id: Optional[str] = None) -> list[DeviceToken]:
    """Alle aktiven Tokens (optional: gefiltert nach Bundle-ID)."""
    query = "SELECT * FROM device_tokens WHERE is_active = 1"
    params: tuple = ()

    if bundle_id:
        query += " AND bundle_id = ?"
        params = (bundle_id,)

    query += " ORDER BY updated_at DESC"

    with _conn() as con:
        rows = con.execute(query, params).fetchall()

    return [_row_to_token(r) for r in rows]


def list_all() -> list[DeviceToken]:
    """Alle Tokens (auch inaktive) — für Admin/Debug."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM device_tokens ORDER BY updated_at DESC"
        ).fetchall()
    return [_row_to_token(r) for r in rows]


def _get_by_token(token: str) -> DeviceToken:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM device_tokens WHERE token = ?",
            (token,),
        ).fetchone()

    if row is None:
        raise LookupError(f"Token nicht in DB: {token[:16]}...")
    return _row_to_token(row)


def _row_to_token(row: sqlite3.Row) -> DeviceToken:
    """Mappt eine SQLite-Row auf das DeviceToken-Datamodel."""
    return DeviceToken(
        id=row["id"],
        token=row["token"],
        bundle_id=row["bundle_id"],
        environment=row["environment"],
        device_label=row["device_label"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_active=bool(row["is_active"]),
    )