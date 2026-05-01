#!/usr/bin/env python3
# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend / CLI
#  Datei . . . . : tools/send_test_push.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : CLI zum direkten Senden eines Test-Pushes über apns_client.
#                  Erwartet ein Device-Token als Argument; gibt das Ergebnis aus.
#
#  Verwendung    : python tools/send_test_push.py <DEVICE_TOKEN>
#                  (Device-Token bekommst du von der iOS-App, sobald die existiert.
#                  Vorher kannst du auch einen Fake-Token nutzen, um zu sehen, dass
#                  Apple antwortet — der Push wird zwar als "BadDeviceToken" abgelehnt,
#                  aber wir wissen: unser Stack funktioniert.)
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path


# Damit "from src.foo" funktioniert, wenn das Skript aus /opt/barto-link/ ausgeführt wird
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from src.apns_client import apns, PushPayload


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tools/send_test_push.py <DEVICE_TOKEN>")
        print("       (Hex-String, 64 Zeichen, von der iOS-App registriert)")
        return 1

    device_token = sys.argv[1].strip()

    payload = PushPayload(
        title="🧪 Test-Push",
        body="Wenn du das siehst, läuft der Stack.",
        source="barto-link-test",
    )

    print(f"Sende Test-Push an {device_token[:16]}...")

    try:
        result = await apns.send(device_token, payload)
    except Exception as e:
        print(f"❌ Fehler: {type(e).__name__}: {e}")
        return 2

    print(f"✅ Status:      {result.status}")
    print(f"✅ Description: {result.description}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))