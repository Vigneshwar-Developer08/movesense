"""Local user accounts and cookie sessions.

Accounts and sessions live in ``data/local/users.json`` and
``data/local/sessions.json`` so a lab workstation can recognise a returning
user without a database or an external identity provider.

* Passwords are hashed with PBKDF2-HMAC-SHA256 (per-user random salt).
* Session tokens are random, returned to the browser once and stored here only
  as SHA-256 digests.
* The browser keeps the raw token in an HttpOnly cookie (see ``api.py``).

This is deliberately a single-workstation user store, not a replacement for a
real auth service. It never stores a plaintext password.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import local_store

log = logging.getLogger(__name__)

SESSION_COOKIE = "movesense_session"
USERS_FILE = "users.json"
SESSIONS_FILE = "sessions.json"

PASSWORD_ITERATIONS = 240_000
REMEMBER_TTL_S = 30 * 24 * 60 * 60  # 30 days ("Remember me")
SESSION_TTL_S = 12 * 60 * 60  # 12 hours (browser-session cookie)

_lock = threading.Lock()


class AuthError(ValueError):
    """A signup/signin request that cannot be fulfilled."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


# --------------------------------------------------------------------------- passwords
def _hash_password(password: str, salt_hex: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        iterations,
    ).hex()


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------ store
def _load_users() -> list[dict[str, Any]]:
    users = local_store.read_state(USERS_FILE, [])
    if not isinstance(users, list):
        return []
    return [u for u in users if isinstance(u, dict) and u.get("id")]


def _save_users(users: list[dict[str, Any]]) -> None:
    local_store.write_state(USERS_FILE, users)


def _load_sessions() -> list[dict[str, Any]]:
    sessions = local_store.read_state(SESSIONS_FILE, [])
    if not isinstance(sessions, list):
        return []
    return [s for s in sessions if isinstance(s, dict) and s.get("token_hash")]


def _save_sessions(sessions: list[dict[str, Any]]) -> None:
    local_store.write_state(SESSIONS_FILE, sessions)


# ------------------------------------------------------------------------------ users
def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """The subset of a user record that is safe to send to the browser."""
    return {
        "id": user.get("id"),
        "full_name": user.get("full_name"),
        "email": user.get("email"),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
    }


def find_user_by_email(email: str) -> dict[str, Any] | None:
    wanted = normalize_email(email)
    if not wanted:
        return None
    for user in _load_users():
        if user.get("email_lower") == wanted:
            return user
    return None


def active_user_count() -> int:
    return len(_load_users())


def create_user(full_name: str, email: str, password: str) -> dict[str, Any]:
    name = full_name.strip()
    address = email.strip()
    if not name:
        raise AuthError("Full name is required.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalize_email(address)):
        raise AuthError("Enter a valid email address.")
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")

    with _lock:
        if find_user_by_email(address) is not None:
            raise AuthError("An account with this email already exists. Sign in instead.")
        salt = secrets.token_hex(16)
        user = {
            "id": f"usr_{uuid4().hex[:12]}",
            "full_name": name,
            "email": address,
            "email_lower": normalize_email(address),
            "password_salt": salt,
            "password_hash": _hash_password(password, salt, PASSWORD_ITERATIONS),
            "password_iterations": PASSWORD_ITERATIONS,
            "created_at": _iso(_now()),
            "last_login_at": None,
        }
        users = _load_users()
        users.append(user)
        _save_users(users)

    log.info("created local user %s", user["email"])
    return user


def verify_password(user: dict[str, Any], password: str) -> bool:
    try:
        salt = str(user["password_salt"])
        iterations = int(user.get("password_iterations") or PASSWORD_ITERATIONS)
        expected = str(user["password_hash"])
    except (KeyError, TypeError, ValueError):
        return False
    candidate = _hash_password(password, salt, iterations)
    return hmac.compare_digest(candidate, expected)


def authenticate(email: str, password: str) -> dict[str, Any] | None:
    user = find_user_by_email(email)
    if user is None or not verify_password(user, password):
        return None
    with _lock:
        users = _load_users()
        for item in users:
            if item.get("id") == user.get("id"):
                item["last_login_at"] = _iso(_now())
                user = item
                break
        _save_users(users)
    return user


# --------------------------------------------------------------------------- sessions
def _prune_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _now()
    kept: list[dict[str, Any]] = []
    for session in sessions:
        expires = _parse_iso(session.get("expires_at"))
        if expires is None or expires > now:
            kept.append(session)
    return kept


def create_session(user_id: str, remember: bool = True) -> str:
    """Create a session and return the raw token (only the digest is stored)."""
    token = secrets.token_urlsafe(32)
    ttl = REMEMBER_TTL_S if remember else SESSION_TTL_S
    with _lock:
        sessions = _prune_sessions(_load_sessions())
        sessions.append(
            {
                "token_hash": _token_digest(token),
                "user_id": user_id,
                "remember": bool(remember),
                "created_at": _iso(_now()),
                "expires_at": _iso(_now() + timedelta(seconds=ttl)),
            }
        )
        _save_sessions(sessions)
    return token


def user_for_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    digest = _token_digest(token)
    with _lock:
        sessions = _load_sessions()
        session = next(
            (s for s in sessions if hmac.compare_digest(str(s.get("token_hash")), digest)),
            None,
        )
        if session is None:
            return None
        expires = _parse_iso(session.get("expires_at"))
        if expires is None or expires <= _now():
            _save_sessions([s for s in sessions if s is not session])
            return None
        user_id = session.get("user_id")
    return next((u for u in _load_users() if u.get("id") == user_id), None)


def delete_session(token: str | None) -> None:
    if not token:
        return
    digest = _token_digest(token)
    with _lock:
        sessions = _load_sessions()
        _save_sessions(
            [s for s in sessions if not hmac.compare_digest(str(s.get("token_hash")), digest)]
        )
