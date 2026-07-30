# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : config.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Lädt alle Konfigurationswerte aus /etc/barto-link/barto-link.env
#                  und validiert sie typisch via Pydantic Settings.
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration für barto-link.

    Lädt Werte aus der .env (per Symlink: /etc/barto-link/barto-link.env)
    und validiert Typen + Pflichtfelder beim Start. Wenn was fehlt:
    klare Fehlermeldung, kein Halbzustand.
    """

    # --- Apple APNs ---
    apple_team_id: str = Field(..., description="Apple Developer Team ID (10 Zeichen)")
    apple_key_id: str = Field(..., description="Key ID des P8-Push-Keys")
    apple_key_path: Path = Field(..., description="Absoluter Pfad zur AuthKey_*.p8-Datei")
    apple_bundle_id: str = Field(..., description="Bundle-ID der iOS-App, z.B. 'ai.barto.bartolink'")
    apns_environment: Literal["sandbox", "production"] = Field(
        default="sandbox",
        description="'sandbox' für Debug-Builds aus Xcode, 'production' für TestFlight/AppStore",
    )

    # --- Backend ---
    barto_link_api_token: str = Field(..., min_length=20, description="Master-Token für Tool-Aufrufe")

    # --- Server ---
    server_host: str = Field(default="127.0.0.1")
    server_port: int = Field(default=8765, ge=1, le=65535)

    # --- dbticker ---
    dbticker_bin_path: Path = Field(
        default=Path("/home/barto/developments/projects/dbticker/.venv/bin/dbticker"),
        description="Entry-Point von dbticker; wird für den manuellen Trip-Refresh aufgerufen",
    )

    # --- Datenbank ---
    database_path: Path = Field(default=Path("/var/lib/barto-link/tokens.db"))

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    log_dir: Path = Field(default=Path("/opt/barto-link/log"))

    model_config = SettingsConfigDict(
        env_file=".env",                  # nutzt den Symlink → /etc/barto-link/barto-link.env
        env_file_encoding="utf-8",
        case_sensitive=False,             # APPLE_TEAM_ID == apple_team_id
        extra="ignore",                   # zusätzliche env vars werden nicht gemeckert
    )


# Singleton: Wird einmal beim Import erzeugt, dann überall verwendet.
# Falls .env defekt ist, schlägt der Import fehl mit klarer Fehlermeldung.
settings = Settings()