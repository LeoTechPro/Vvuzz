from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type


PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, type=Type.ID)
MAX_FAILURES = 5
LOCKOUT_MINUTES = 15


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def iso(value: dt.datetime) -> str:
    return value.isoformat()


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def digest(value: str, key: bytes | None = None) -> str:
    payload = value.encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest() if key else hashlib.sha256(payload).hexdigest()


def hash_password(password: str) -> str:
    return PASSWORDS.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORDS.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


@dataclass(frozen=True)
class Session:
    token: str
    csrf_token: str
    expires_at: str


def audit(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    outcome: str,
    user_id: int | None = None,
    target_type: str | None = None,
    target_key: str | None = None,
    metadata: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO admin_audit(
          user_id, event_type, target_type, target_key, outcome, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            event_type,
            target_type,
            target_key,
            outcome,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            iso(utcnow()),
        ),
    )


def authenticate(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
) -> sqlite3.Row | None:
    now = utcnow()
    row = conn.execute(
        "SELECT * FROM auth_users WHERE username = ? AND is_active = 1",
        (username,),
    ).fetchone()
    if row is None:
        audit(conn, event_type="login", outcome="denied", target_type="username")
        return None
    if row["locked_until"] and parse_iso(row["locked_until"]) > now:
        audit(conn, event_type="login", outcome="locked", user_id=row["id"])
        return None
    if not verify_password(row["password_hash"], password):
        failures = row["failed_attempts"] + 1
        locked_until = (
            iso(now + dt.timedelta(minutes=LOCKOUT_MINUTES))
            if failures >= MAX_FAILURES
            else None
        )
        conn.execute(
            "UPDATE auth_users SET failed_attempts=?, locked_until=?, updated_at=? WHERE id=?",
            (failures, locked_until, iso(now), row["id"]),
        )
        audit(conn, event_type="login", outcome="denied", user_id=row["id"])
        return None
    conn.execute(
        """
        UPDATE auth_users
        SET failed_attempts=0, locked_until=NULL, last_login_at=?, updated_at=?
        WHERE id=?
        """,
        (iso(now), iso(now), row["id"]),
    )
    audit(conn, event_type="login", outcome="success", user_id=row["id"])
    return row


def create_session(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    hmac_key: bytes,
    ttl_seconds: int,
    user_agent: str,
    ip_prefix: str,
) -> Session:
    now = utcnow()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = now + dt.timedelta(seconds=ttl_seconds)
    conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    conn.execute(
        """
        INSERT INTO auth_sessions(
          id_hash, user_id, csrf_hash, created_at, expires_at, last_seen_at,
          user_agent_hash, ip_prefix_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            digest(token, hmac_key),
            user_id,
            digest(csrf, hmac_key),
            iso(now),
            iso(expires),
            iso(now),
            digest(user_agent, hmac_key),
            digest(ip_prefix, hmac_key),
        ),
    )
    return Session(token=token, csrf_token=csrf, expires_at=iso(expires))


def get_session(
    conn: sqlite3.Connection,
    *,
    token: str,
    hmac_key: bytes,
    idle_seconds: int,
) -> sqlite3.Row | None:
    if not token:
        return None
    row = conn.execute(
        """
        SELECT s.*, u.username, u.role, u.is_active
        FROM auth_sessions s
        JOIN auth_users u ON u.id = s.user_id
        WHERE s.id_hash = ?
        """,
        (digest(token, hmac_key),),
    ).fetchone()
    if row is None or not row["is_active"]:
        return None
    now = utcnow()
    if parse_iso(row["expires_at"]) <= now:
        conn.execute("DELETE FROM auth_sessions WHERE id_hash = ?", (row["id_hash"],))
        return None
    if parse_iso(row["last_seen_at"]) + dt.timedelta(seconds=idle_seconds) <= now:
        conn.execute("DELETE FROM auth_sessions WHERE id_hash = ?", (row["id_hash"],))
        return None
    conn.execute(
        "UPDATE auth_sessions SET last_seen_at = ? WHERE id_hash = ?",
        (iso(now), row["id_hash"]),
    )
    return row


def valid_csrf(row: sqlite3.Row, token: str, hmac_key: bytes) -> bool:
    return bool(token) and secrets.compare_digest(row["csrf_hash"], digest(token, hmac_key))
