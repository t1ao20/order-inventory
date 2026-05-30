from typing import Optional

from fastapi import Header, HTTPException, Request, status
from jose import JWTError, jwt

from app.core.config import settings


def get_current_user(
    request: Request,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_user_email: Optional[str] = Header(None, alias="X-User-Email"),
    x_user_role: str = Header("employee", alias="X-User-Role"),
) -> dict:
    """Mixed-mode auth: prefer Kong headers, else fall back to JWT.

    Security notes:
    - Only trust headers when Kong is guaranteed to overwrite client headers.
    - If headers are absent, try to read a Bearer token from `Authorization` and decode it.
    """
    # 1) Header-first (Kong)
    if x_user_id is not None:
        try:
            user_id = int(x_user_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user id header")
        return {"user_id": user_id, "email": x_user_email, "role": x_user_role}

    # 2) Fallback: JWT from Authorization header
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
    token = auth_header.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("userId") or payload.get("user_id")
        email = payload.get("email")
        role = payload.get("role", "employee")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return {"user_id": int(user_id), "email": email, "role": role}
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
