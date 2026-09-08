"""Authentication and server-side authorization for the dashboard.

The local SQLite registry is intended as a deployable prototype. In production,
the authentication functions can be replaced by an OIDC provider while the
role and geography policy remains unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROLES = {
    "Administrator": {"levels": {"National", "County", "Sub-county", "Ward"}, "admin": True},
    "President": {"levels": {"National", "County", "Sub-county", "Ward"}, "admin": False},
    "Governor": {"levels": {"County", "Sub-county", "Ward"}, "admin": False},
    "Senator": {"levels": {"County", "Sub-county", "Ward"}, "admin": False},
    "Women Representative": {"levels": {"County", "Sub-county", "Ward"}, "admin": False},
    "MP": {"levels": {"Sub-county", "Ward"}, "admin": False},
    "MCA": {"levels": {"Ward"}, "admin": False},
}

PASSWORD_ITERATIONS = 310_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_path(root: Path) -> Path:
    path = root / "data" / "auth.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(root: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path(root))
    connection.row_factory = sqlite3.Row
    return connection


def initialize(root: Path) -> None:
    with connect(root) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                auth_subject TEXT,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login_at TEXT,
                CHECK (role IN ('Administrator', 'President', 'Governor', 'Senator', 'Women Representative', 'MP', 'MCA'))
            );
            CREATE TABLE IF NOT EXISTS user_scopes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                dataset TEXT,
                county TEXT,
                sub_county TEXT,
                constituency TEXT,
                ward TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_username TEXT NOT NULL,
                action TEXT NOT NULL,
                target_username TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        if "auth_subject" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN auth_subject TEXT")


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        calculated = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(calculated.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def user_count(root: Path) -> int:
    initialize(root)
    with connect(root) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def create_user(root: Path, username: str, display_name: str, role: str, password: str = "", scopes: list[dict[str, str | None]] | None = None, actor: str = "system", auth_subject: str | None = None) -> None:
    username = username.strip().lower()
    if not username or not display_name.strip() or role not in ROLES:
        raise ValueError("Username, display name, and a valid role are required.")
    if password and len(password) < 12:
        raise ValueError("A local password must contain at least 12 characters.")
    initialize(root)
    with connect(root) as connection:
        cursor = connection.execute(
            "INSERT INTO users (username, auth_subject, display_name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, auth_subject or username, display_name.strip(), role, _password_hash(password) if password else "oidc_only", utc_now()),
        )
        user_id = cursor.lastrowid
        for scope in scopes or []:
            connection.execute(
                "INSERT INTO user_scopes (user_id, dataset, county, sub_county, constituency, ward) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, scope.get("dataset"), scope.get("county"), scope.get("sub_county"), scope.get("constituency"), scope.get("ward")),
            )
        connection.execute("INSERT INTO audit_log (actor_username, action, target_username, detail, created_at) VALUES (?, ?, ?, ?, ?)", (actor, "create_user", username, role, utc_now()))


def authenticate(root: Path, username: str, password: str) -> dict[str, Any] | None:
    initialize(root)
    with connect(root) as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username.strip().lower(),)).fetchone()
        if row is None or not _password_matches(password, row["password_hash"]):
            return None
        connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utc_now(), row["id"]))
        connection.execute("INSERT INTO audit_log (actor_username, action, target_username, detail, created_at) VALUES (?, ?, ?, ?, ?)", (row["username"], "login", row["username"], "", utc_now()))
        return dict(row)


def authenticate_oidc(root: Path, subject: str, email: str | None = None) -> dict[str, Any] | None:
    initialize(root)
    identities = [value.strip().lower() for value in [subject, email] if value]
    if not identities:
        return None
    placeholders = ",".join("?" for _ in identities)
    with connect(root) as connection:
        row = connection.execute(f"SELECT * FROM users WHERE active = 1 AND (lower(auth_subject) IN ({placeholders}) OR lower(username) IN ({placeholders}))", identities + identities).fetchone()
        if row is None:
            return None
        connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utc_now(), row["id"]))
        connection.execute("INSERT INTO audit_log (actor_username, action, target_username, detail, created_at) VALUES (?, ?, ?, ?, ?)", (row["username"], "oidc_login", row["username"], subject, utc_now()))
        return dict(row)


def scopes_for(root: Path, user_id: int) -> list[dict[str, str | None]]:
    initialize(root)
    with connect(root) as connection:
        rows = connection.execute("SELECT dataset, county, sub_county, constituency, ward FROM user_scopes WHERE user_id = ?", (user_id,)).fetchall()
        return [dict(row) for row in rows]


def list_users(root: Path) -> pd.DataFrame:
    initialize(root)
    with connect(root) as connection:
        rows = connection.execute("SELECT id, username, auth_subject, display_name, role, active, created_at, last_login_at FROM users ORDER BY username").fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def update_user(root: Path, user_id: int, role: str, active: bool, scopes: list[dict[str, str | None]], actor: str) -> None:
    if role not in ROLES:
        raise ValueError("Invalid role.")
    initialize(root)
    with connect(root) as connection:
        row = connection.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("User not found.")
        connection.execute("UPDATE users SET role = ?, active = ? WHERE id = ?", (role, int(active), user_id))
        connection.execute("DELETE FROM user_scopes WHERE user_id = ?", (user_id,))
        for scope in scopes:
            connection.execute(
                "INSERT INTO user_scopes (user_id, dataset, county, sub_county, constituency, ward) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, scope.get("dataset"), scope.get("county"), scope.get("sub_county"), scope.get("constituency"), scope.get("ward")),
            )
        connection.execute("INSERT INTO audit_log (actor_username, action, target_username, detail, created_at) VALUES (?, ?, ?, ?, ?)", (actor, "update_user", row["username"], f"role={role}; active={active}", utc_now()))


def is_admin(user: dict[str, Any]) -> bool:
    return bool(ROLES.get(user.get("role"), {}).get("admin"))


def allowed_levels(user: dict[str, Any]) -> set[str]:
    return set(ROLES.get(user.get("role"), {}).get("levels", set()))


def _matches_scope(row: pd.Series, scope: dict[str, str | None], dataset: str) -> bool:
    if scope.get("dataset") and scope["dataset"] != dataset:
        return False
    for column in ("county", "sub_county", "constituency", "ward"):
        value = scope.get(column)
        if value and (column not in row.index or str(row.get(column, "")) != value):
            return False
    return any(scope.get(column) for column in ("county", "sub_county", "constituency", "ward"))


def filter_frame(frame: pd.DataFrame, user: dict[str, Any], dataset: str, root: Path) -> pd.DataFrame:
    """Apply geography scopes before the frame is used by the dashboard."""
    if is_admin(user) or user.get("role") == "President":
        return frame.copy()
    scopes = scopes_for(root, int(user["id"]))
    if not scopes:
        return frame.iloc[0:0].copy()
    mask = frame.apply(lambda row: any(_matches_scope(row, scope, dataset) for scope in scopes), axis=1)
    return frame.loc[mask].copy()


def filter_bundle(bundle: dict[str, Any], user: dict[str, Any], dataset: str, root: Path) -> dict[str, Any]:
    return {**bundle, **{name: filter_frame(bundle[name], user, dataset, root) for name in ("history", "snapshot", "master", "summary")}}
