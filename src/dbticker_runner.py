# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : dbticker_runner.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 04.05.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Subprocess-Wrapper für dbticker im Single-Route-Modus.
#                  Wird vom /trips/{trip_key}/refresh-Endpoint aufgerufen,
#                  wenn ein manueller Refresh erlaubt wurde.
#
#                  Ruft den dbticker-Entry-Point aus dessen venv direkt auf.
#                  (Der frühere openclaw-Wrapper dbticker.sh ist entfallen —
#                   barto-link läuft bereits als User barto, das sudo -u des
#                   Wrappers war sein einziger Zweck.)
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from src.config import settings


logger = logging.getLogger(__name__)


# Pfad zum dbticker-Entry-Point. Default + Override siehe src/config.py.
DBTICKER_BIN = settings.dbticker_bin_path

# Hartes Timeout, falls dbticker hängt. DB-API ist normalerweise <2s; wir geben ihm 15.
DBTICKER_TIMEOUT_SECONDS = 15


@dataclass
class RunnerResult:
    """Ergebnis eines dbticker-Subprocess-Aufrufs."""
    success: bool
    return_code: int
    stdout: str
    stderr: str
    # Strukturierter Fehlergrund — None bei Erfolg
    error_kind: Optional[Literal["route_not_found", "timeout", "script_missing", "other"]] = field(default=None)


async def run_for_route(route_id: str) -> RunnerResult:
    """Führt dbticker für genau eine Route aus.

    dbticker-Exit-Codes: 0 = ok, 1 = Config/Credentials, 2 = Route nicht in
    routes.toml. Alles != 0 kommt als RunnerResult.success=False raus.

    Args:
        route_id: dbticker-Route-Identifier (z.B. "hin-0631").

    Returns:
        RunnerResult mit success-Flag, return_code, stdout/stderr.
    """
    if not DBTICKER_BIN.exists():
        logger.error("dbticker-Binary nicht gefunden: %s", DBTICKER_BIN)
        return RunnerResult(
            success=False,
            return_code=-1,
            stdout="",
            stderr=f"dbticker-Binary nicht gefunden: {DBTICKER_BIN}",
            error_kind="script_missing",
        )

    logger.info("Starte dbticker für Route %s", route_id)

    try:
        proc = await asyncio.create_subprocess_exec(
            str(DBTICKER_BIN), "--route", route_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=DBTICKER_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error("dbticker-Timeout nach %ds für Route %s",
                     DBTICKER_TIMEOUT_SECONDS, route_id)
        # Process killen, damit nicht im Hintergrund weiterläuft
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return RunnerResult(
            success=False,
            return_code=-2,
            stdout="",
            stderr=f"dbticker-Timeout nach {DBTICKER_TIMEOUT_SECONDS}s",
            error_kind="timeout",
        )

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    success = proc.returncode == 0
    log_fn = logger.info if success else logger.warning
    log_fn(
        "dbticker für Route %s beendet: rc=%d (stdout=%dB, stderr=%dB)",
        route_id, proc.returncode, len(stdout), len(stderr),
    )

    error_kind: Optional[Literal["route_not_found", "timeout", "script_missing", "other"]] = None
    if not success:
        # rc=2 ist dbtickers Kontrakt für 'Route nicht in routes.toml'.
        error_kind = "route_not_found" if proc.returncode == 2 else "other"

    return RunnerResult(
        success=success,
        return_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        error_kind=error_kind,
    )