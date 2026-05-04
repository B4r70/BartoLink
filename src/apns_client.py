# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : apns_client.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Verkapselt den eigentlichen Push-Versand an Apple's APNs.
#                  Lädt den P8-Key, baut JWT-Token, sendet via aioapns.
#                  Wer einen Push schicken will, ruft 'send_push()' — fertig.
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import logging
from dataclasses import dataclass

from aioapns import APNs, NotificationRequest, PushType
from aioapns.common import NotificationResult

from src.config import settings
from typing import Optional


logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
#  Datenmodell für einen Push
# ------------------------------------------------------------------------------

@dataclass
class PushPayload:
    """Was an einen Endnutzer gesendet wird.

    Pro Push entstehen damit:
      - Title und Body in der Notification (sichtbar)
      - Optional Source und Priority (für App-seitige Filterung/Verhalten)
    """
    title: str
    body: str
    source: str = "system"          # z.B. "dbticker", "mailcontrol"
    priority: int = 5               # APNs-Priority: 5 = normal, 10 = sofort
    sound: str = "default"          # 'default' oder Custom-Sound-Name
    badge: Optional[int] = None     # Nummer auf App-Icon, None = unverändert
    meta: Optional[dict] = None     # Strukturierte Zusatzdaten für die App

# ------------------------------------------------------------------------------
#  APNs-Client (lazy)
# ------------------------------------------------------------------------------

class APNsClient:
    """Wrapper um aioapns.APNs — lazy initialisiert.

    Der unterliegende aioapns.APNs-Konstruktor will eine laufende Event-Loop
    finden (asyncio.get_event_loop). In Python 3.14 ist das außerhalb von
    async-Code ein Hard-Error. Daher initialisieren wir erst beim ersten
    send() — dort sind wir garantiert im async-Context.
    """

    def __init__(self):
        self._client: Optional[APNs] = None

    def _ensure_client(self) -> APNs:
        if self._client is None:
         # Key-Inhalt direkt einlesen statt Pfad zu übergeben
         # Umgehung für aioapns 4.x + Python 3.14 PEM-Parsing-Bug
            key_content = settings.apple_key_path.read_text(encoding="utf-8")

            self._client = APNs(
                key=key_content,                        # ← Inhalt statt Pfad
                key_id=settings.apple_key_id,
                team_id=settings.apple_team_id,
                topic=settings.apple_bundle_id,
                use_sandbox=(settings.apns_environment == "sandbox"),
            )
            logger.info(
              "APNsClient lazy initialisiert (env=%s, bundle=%s)",
                settings.apns_environment,
                settings.apple_bundle_id,
            )
        return self._client

    async def send(
        self,
        device_token: str,
        payload: PushPayload,
    ) -> NotificationResult:
        """Sendet einen Push an ein konkretes Device.

        Args:
            device_token: APNs-Token des Empfängers (vom iPhone via App registriert).
            payload: Was angezeigt werden soll.

        Returns:
            NotificationResult von aioapns mit Status, Description, etc.
        """
        client = self._ensure_client()

        # Message-Dict aufbauen
        message = {
            "aps": {
                "alert": {
                    "title": payload.title,
                    "body": payload.body,
                },
                "sound": payload.sound,
                **({"badge": payload.badge} if payload.badge is not None else {}),
                "mutable-content": 1,
            },
            "source": payload.source,
        }

        # Strukturierte Metadaten dranhängen, falls vorhanden
        if payload.meta is not None:
            message["meta"] = payload.meta

        notification = NotificationRequest(
            device_token=device_token,
            message=message,
            push_type=PushType.ALERT,
            priority=payload.priority,
        )

        result = await client.send_notification(notification)

        logger.info(
            "Push gesendet (token=%s..., source=%s, status=%s, desc=%s)",
            device_token[:16],
            payload.source,
            result.status,
            result.description,
        )
        return result


# ------------------------------------------------------------------------------
#  Singleton
# ------------------------------------------------------------------------------

# Shell um den lazy-init APNs-Client. Erstes send() initialisiert tatsächlich.
apns = APNsClient()