from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

_basic = HTTPBasic(realm="leave-bot admin")


def require_admin(
    credentials: HTTPBasicCredentials = Depends(_basic),
) -> str:
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin UI is disabled (ADMIN_PASSWORD not set)",
        )
    u_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    p_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (u_ok and p_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="leave-bot admin"'},
        )
    return credentials.username
