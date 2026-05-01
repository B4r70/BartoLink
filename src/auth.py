# ===========================================================================================
#  # BartoAI / barto-link
# ===========================================================================================
#  Bereich . . . : Backend
#  Datei . . . . : auth.py
#  Autor . . . . : Bartosz Stryjewski
#  Erstellt am . : 30.04.2026
# ------------------------------------------------------------------------------------------
#  Beschreibung  : Bearer-Token-Authentifizierung für Backend-Endpoints.
#                  Vergleicht den Authorization-Header mit dem Master-Token
#                  aus den Settings. Pro V1: ein Token für alle Konsumenten.
# ------------------------------------------------------------------------------------------
#  (C) Copyright 2026 Bartosz Stryjewski
#  All rights reserved
# ===========================================================================================
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from src.config import settings


def verify_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI-Dependency, die den Bearer-Token gegen die Settings prüft.

    Wird in geschützten Endpoints als Dependency injiziert:
        @app.post("/push", dependencies=[Depends(verify_token)])

    Raises:
        HTTPException 401, wenn Header fehlt oder Token falsch.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization-Header fehlt",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization-Header muss 'Bearer <token>' sein",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided = authorization.removeprefix("Bearer ").strip()

    # secrets.compare_digest = timing-safe comparison
    # Wichtig in der Auth, damit Angreifer nicht über Antwortzeit den Token erraten können.
    if not secrets.compare_digest(provided, settings.barto_link_api_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token ungültig",
        )