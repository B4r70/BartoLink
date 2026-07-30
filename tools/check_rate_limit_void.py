# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Tools
#  Datei . . . . : check_rate_limit_void.py
#  Autor . . . . : Bartosz Stryjewski
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Selbstcheck für rate_limit.void() — ein fehlgeschlagener Refresh
#                  darf keinen Cooldown kosten. Läuft gegen eine temporäre DB und
#                  fasst die Produktionsdaten nicht an.
#
#  Aufruf        : cd /opt/barto-link && ./.venv/bin/python tools/check_rate_limit_void.py
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
import tempfile
from pathlib import Path

from src.config import settings

settings.database_path = Path(tempfile.mkdtemp()) / "check.db"

from src import rate_limit  # noqa: E402  — erst nach dem DB-Pfad-Wechsel importieren

rate_limit.init_db()

KEY = "99999_2026-01-01_selftest"

first = rate_limit.check_and_record(KEY)
assert first.allowed, "erster Refresh muss erlaubt sein"
assert first.row_id is not None, "erlaubter Refresh muss eine row_id liefern"

blocked = rate_limit.check_and_record(KEY)
assert not blocked.allowed, "zweiter Refresh muss im Cooldown hängen"
assert blocked.reason == "per_trip", f"falscher Grund: {blocked.reason}"

rate_limit.void(first.row_id)

again = rate_limit.check_and_record(KEY)
assert again.allowed, "nach void() muss der Trip sofort wieder frei sein"

print("ok — void() hebt den Cooldown auf")
