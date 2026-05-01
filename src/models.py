# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : models.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Pydantic-Schemas für Request/Response der HTTP-API.
#                  Trennung von SQLite-DataClasses (in tokens.py): hier nur API,
#                  dort die Persistenz. Frontend-Backend-Vertrag.
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------------------------
#  /tokens/register
# ------------------------------------------------------------------------------

class TokenRegisterRequest(BaseModel):
    """Was die iOS-App sendet, wenn sie ihren APNs-Token meldet."""
    token: str = Field(..., min_length=64, max_length=200, description="APNs-Hex-Token")
    bundle_id: str = Field(..., description="Bundle-ID der App, z.B. 'com.barto.bartolink'")
    environment: Literal["sandbox", "production"]
    device_label: Optional[str] = Field(default=None, max_length=80)


class TokenRegisterResponse(BaseModel):
    id: int
    token_preview: str         # nur erste 16 Zeichen — voller Token nicht zurückspielen
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------------------
#  /push
# ------------------------------------------------------------------------------

class PushRequest(BaseModel):
    """Was Tools (dbticker, mailcontrol, …) schicken, wenn sie einen Push wollen."""
    title: str = Field(..., max_length=200)
    body: str = Field(..., max_length=2000)
    source: str = Field(default="system", max_length=50, description="z.B. 'dbticker'")
    priority: int = Field(default=5, ge=1, le=10, description="APNs-Priority")
    sound: str = Field(default="default")
    badge: Optional[int] = Field(default=None, ge=0)


class PushResponse(BaseModel):
    sent_to: int
    failed: int
    details: list[dict]


# ------------------------------------------------------------------------------
#  /tokens/list (Admin)
# ------------------------------------------------------------------------------

class TokenInfo(BaseModel):
    id: int
    token_preview: str
    bundle_id: str
    environment: str
    device_label: Optional[str]
    created_at: datetime
    updated_at: datetime
    is_active: bool