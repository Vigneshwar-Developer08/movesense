"""Local account and cookie-session tests.

The store is redirected to a temporary directory; nothing touches
``server/data/local`` and no external auth service is involved.
"""
import json

import pytest
from fastapi.testclient import TestClient

import api
import auth
import local_store

COOKIE = auth.SESSION_COOKIE


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "DATA_DIR", tmp_path / "local")
    yield tmp_path / "local"


@pytest.fixture
def client():
    return TestClient(api.app)


def _signup(client: TestClient, email: str = "ada@example.com", password: str = "correct horse"):
    return client.post(
        "/api/auth/signup",
        json={"full_name": "Ada Lovelace", "email": email, "password": password},
    )


def test_signup_creates_user_and_session_cookie(client, isolated_store):
    response = _signup(client)

    assert response.status_code == 201
    payload = response.json()["user"]
    assert payload["email"] == "ada@example.com"
    assert payload["full_name"] == "Ada Lovelace"
    assert "password" not in json.dumps(payload)
    assert COOKIE in response.cookies
    assert "httponly" in response.headers["set-cookie"].lower()

    users = json.loads((isolated_store / "users.json").read_text())
    assert len(users) == 1
    assert users[0]["email_lower"] == "ada@example.com"


def test_signup_rejects_duplicate_email_case_insensitively(client):
    assert _signup(client).status_code == 201
    response = _signup(client, email="ADA@EXAMPLE.COM")

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


def test_signup_rejects_short_password(client):
    response = client.post(
        "/api/auth/signup",
        json={"full_name": "Ada", "email": "ada@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_signin_rejects_wrong_password(client):
    _signup(client)
    client.cookies.clear()

    response = client.post(
        "/api/auth/signin",
        json={"email": "ada@example.com", "password": "wrong password"},
    )

    assert response.status_code == 401
    assert COOKIE not in response.cookies


def test_signin_then_me_returns_the_same_user(client):
    _signup(client)
    user_id = client.get("/api/auth/me").json()["user"]["id"]
    client.cookies.clear()

    response = client.post(
        "/api/auth/signin",
        json={"email": "ada@example.com", "password": "correct horse", "remember": True},
    )

    assert response.status_code == 200
    assert response.json()["user"]["id"] == user_id
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "ada@example.com"


def test_me_without_cookie_is_unauthorized(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert COOKIE not in response.cookies


def test_signout_clears_cookie_and_server_session(client):
    _signup(client)
    assert client.get("/api/auth/me").status_code == 200

    response = client.post("/api/auth/signout")

    assert response.status_code == 200
    assert response.json() == {"signed_out": True}
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected_and_cookie_cleared(client, isolated_store):
    _signup(client)
    sessions_path = isolated_store / "sessions.json"
    sessions = json.loads(sessions_path.read_text())
    sessions[0]["expires_at"] = "2000-01-01T00:00:00+00:00"
    sessions_path.write_text(json.dumps(sessions))

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert COOKIE not in response.cookies
    assert json.loads(sessions_path.read_text()) == []


def test_secrets_are_hashed_at_rest(client, isolated_store):
    password = "correct horse"
    response = _signup(client, password=password)
    raw_token = response.cookies[COOKIE]

    users_text = (isolated_store / "users.json").read_text()
    sessions_text = (isolated_store / "sessions.json").read_text()

    assert password not in users_text
    assert raw_token not in sessions_text

    user = json.loads(users_text)[0]
    assert user["password_hash"] != password
    assert user["password_salt"]
    assert user["password_iterations"] >= 200_000
    # The digest, not the token, is what persists.
    assert auth._token_digest(raw_token) in sessions_text


def test_remember_me_chooses_cookie_lifetime(client):
    _signup(client)
    client.cookies.clear()

    remembered = client.post(
        "/api/auth/signin",
        json={"email": "ada@example.com", "password": "correct horse", "remember": True},
    )
    transient = client.post(
        "/api/auth/signin",
        json={"email": "ada@example.com", "password": "correct horse", "remember": False},
    )

    assert f"max-age={auth.REMEMBER_TTL_S}" in remembered.headers["set-cookie"].lower()
    assert "max-age" not in transient.headers["set-cookie"].lower()


def test_live_tracking_records_the_signed_in_user(client, monkeypatch):
    _signup(client)
    address = "AA:BB:CC:DD:EE:FF"

    async def _noop(session, seconds):
        return

    monkeypatch.setattr(api, "_run_tracking", _noop)
    api.manager._devices[address] = api.ConnectedDevice(
        address=address, name="Movesense", status="connected"
    )
    try:
        response = client.post("/api/tracking/start", json={"address": address})
        assert response.status_code == 200
        session = api.tracking_sessions[response.json()["session_id"]]
        assert session.user_name == "Ada Lovelace"
        assert session.user_email == "ada@example.com"
        assert session.user_id
    finally:
        api.manager._devices.clear()
        api.tracking_sessions.clear()
