#!/usr/bin/env python3
# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend / CLI
#  Datei . . . . : tools/test_trips.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 04.05.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : End-to-End-Test für die neuen Trip-Endpoints.
#                  Spielt das Szenario aus den Screenshots durch (RB23 mit
#                  wachsender Verspätung), prüft Push-Verhalten und History.
#
#  Verwendung    : python tools/test_trips.py
#                  Setzt voraus, dass barto-link auf 127.0.0.1:8765 läuft.
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx


# Damit "from src.config" funktioniert, wenn aus /opt/barto-link/ ausgeführt
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings


BASE_URL = f"http://{settings.server_host}:{settings.server_port}"
TOKEN = settings.barto_link_api_token
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


# Szenario aus den Screenshots: RB23 nach Nassau(Lahn) am 4.5.2026,
# Verspätung wächst von +6 → +8 → +13 → +15
SCENARIO = [
    # (timestamp_label, status, delay, platform, message)
    ("15:55", "delayed", 6,  "3", "Verzögerung im Betriebsablauf"),
    ("16:00", "delayed", 8,  "3", "Verzögerung im Betriebsablauf"),
    ("16:05", "delayed", 13, "3", "Verzögerung im Betriebsablauf"),
    ("16:10", "delayed", 15, "3", "Verzögerung im Betriebsablauf"),
    # Edge-Case: gleiches Delta, aber Gleiswechsel — DAS hat die echte App
    # neulich nicht gemeldet. Mit der neuen Logik soll das einen Push triggern.
    ("16:15", "delayed", 15, "5", "Gleisänderung"),
]

BASE_PAYLOAD = {
    "train_number": "12623",
    "route_id": "test-rb23",
    "departure_date": "2026-05-04",
    "line": "RB23",
    "direction": "Nassau(Lahn)",
    "planned_departure": "16:16",
    "departure_station": "Niederlahnstein",
    "arrival_station": "Bad Ems",
    "planned_platform": "3",
}


def section(title: str) -> None:
    print(f"\n── {title} ──")


def main() -> int:
    client = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=10.0)

    # --- Health-Check ---
    section("Health")
    r = client.get("/health")
    print(f"  {r.status_code}: {r.json()}")
    if r.status_code != 200:
        print("  ❌ Backend nicht erreichbar.")
        return 1

    # --- Szenario abspielen ---
    section("Events einspielen (Szenario aus Screenshots)")
    trip_key = None
    for label, status, delay, platform, msg in SCENARIO:
        payload = {
            **BASE_PAYLOAD,
            "status": status,
            "delay_min": delay,
            "current_platform": platform,
            "message": msg,
        }
        r = client.post("/trips/events", json=payload)
        if r.status_code != 200:
            print(f"  [{label}] ❌ {r.status_code}: {r.text}")
            return 2
        data = r.json()
        trip_key = data["trip_key"]
        push_marker = "📣" if data["push_sent"] else "  "
        print(f"  [{label}] {push_marker} type={data['event_type']:18s} "
              f"recipients={data['push_recipients']}")

    # --- Inbox-Liste ---
    section("GET /trips (Inbox)")
    r = client.get("/trips")
    for t in r.json():
        platform = t.get("current_platform") or "-"
        delay = t.get("current_delay_min")
        delay_str = f"+{delay}" if delay else "0"
        print(f"  {t['line']:6s} → {t['direction']:20s} "
              f"Gleis {platform} {delay_str:>4s} Min")

    # --- Detail mit History ---
    section(f"GET /trips/{trip_key} (DetailView)")
    r = client.get(f"/trips/{trip_key}")
    detail = r.json()
    print(f"  Trip: {detail['trip']['line']} → {detail['trip']['direction']}")
    print(f"  History ({len(detail['events'])} events):")
    for e in detail["events"]:
        push_marker = "📣" if e["pushed_visible"] else "  "
        platform = e.get("platform") or "-"
        delay = e.get("delay_min")
        print(f"    {push_marker} {e['received_at'][:19]} | "
              f"{e['event_type']:18s} | Gleis {platform} | +{delay or 0} Min")

    # --- Refresh-Throttle ---
    section("Rate-Limit (1/60s pro Trip)")
    print("  Erster Refresh — sollte erlaubt sein:")
    r1 = client.post(f"/trips/{trip_key}/refresh")
    print(f"    {r1.status_code}")
    print("  Zweiter sofort — sollte 429 sein:")
    r2 = client.post(f"/trips/{trip_key}/refresh")
    if r2.status_code == 429:
        body = r2.json()
        print(f"    429 ✓  reason={body['reason']}  "
              f"retry_after={body['retry_after_seconds']}s")
    else:
        print(f"    ⚠️  unerwartet: {r2.status_code} {r2.text}")

    print("\n✅ Sprint-1-Test durchgelaufen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())