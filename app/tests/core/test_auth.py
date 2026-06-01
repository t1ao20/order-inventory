import pytest
from fastapi import HTTPException
from jose import jwt

from app.core.auth import get_current_user
from app.core.config import settings


class DummyRequest:
    def __init__(self, headers: dict):
        self.headers = headers


def test_get_current_user_reads_headers_admin():
    result = get_current_user(request=DummyRequest({}), x_user_id="1", x_user_email="admin@example.com", x_user_role="admin")
    assert result == {"user_id": 1, "email": "admin@example.com", "role": "admin"}


def test_get_current_user_defaults_role_to_employee_and_allows_missing_email():
    result = get_current_user(request=DummyRequest({}), x_user_id="3", x_user_email=None, x_user_role="employee")
    assert result == {"user_id": 3, "email": None, "role": "employee"}


def test_get_current_user_fallbacks_to_jwt():
    payload = {"userId": 1, "email": "user@example.com", "role": "admin"}
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    req = DummyRequest({"authorization": f"Bearer {token}"})
    result = get_current_user(request=req, x_user_id=None)
    assert result == {"user_id": 1, "email": "user@example.com", "role": "admin"}


def test_get_current_user_rejects_missing_credentials():
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(request=DummyRequest({}), x_user_id=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing credentials"


def test_get_current_user_rejects_invalid_token():
    req = DummyRequest({"authorization": "Bearer not-a-jwt"})
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(request=req, x_user_id=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"


def test_get_current_user_rejects_invalid_user_id_header():
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(request=DummyRequest({}), x_user_id="not-an-int")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid user id header"
