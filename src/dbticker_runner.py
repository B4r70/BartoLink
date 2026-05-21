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
#                  V1: Subprocess via dbticker.sh (gleicher Pfad wie systemd).
#                  V2 (später): dbticker als Library importieren.
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


# Pfad zum dbticker-Wrapper. Konfigurierbar via settings, mit Default.
DBTICKER_SCRIPT = Path(
    getattr(settings, "dbticker_script_path", "/var/lib/openclaw/skills/dbticker/dbticker.sh")
)

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

    Erwartet, dass dbticker.sh den Modus 'run --route <id>' unterstützt
    (siehe Sprint 2 — dbticker-Anpassung). Bis dahin liefert das hier einen
    return_code != 0, was als RunnerResult.success=False rauskommt.

    Args:
        route_id: dbticker-Route-Identifier (z.B. "hin-0631").

    Returns:
        RunnerResult mit success-Flag, return_code, stdout/stderr.
    """
    if not DBTICKER_SCRIPT.exists():
        logger.error("dbticker-Script nicht gefunden: %s", DBTICKER_SCRIPT)
        return RunnerResult(
            success=False,
            return_code=-1,
            stdout="",
            stderr=f"dbticker-Script nicht gefunden: {DBTICKER_SCRIPT}",
            error_kind="script_missing",
        )

    logger.info("Starte dbticker für Route %s", route_id)

    try:
        proc = await asyncio.create_subprocess_exec(
            str(DBTICKER_SCRIPT), "run", "--route", route_id,
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
        if "nicht in routes.toml gefunden" in stderr:
            error_kind = "route_not_found"
        else:
            error_kind = "other"

    return RunnerResult(
        success=success,
        return_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        error_kind=error_kind,
    )