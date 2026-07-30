from __future__ import annotations

import argparse
import datetime as dt
import getpass

from .config import Settings
from .db import connection
from .security import audit, hash_password


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Vvuzz Public management CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="create or replace a management user")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=("admin", "manager"), default="admin")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "create-admin":
        password = getpass.getpass("Новый пароль: ")
        confirmation = getpass.getpass("Повторите пароль: ")
        if password != confirmation:
            raise SystemExit("Пароли не совпадают")
        if len(password) < 12:
            raise SystemExit("Пароль должен содержать не менее 12 символов")
        timestamp = now_iso()
        with connection(settings.db_path) as conn:
            conn.execute(
                """
                INSERT INTO auth_users(
                  username, password_hash, role, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                  password_hash=excluded.password_hash,
                  role=excluded.role,
                  is_active=1,
                  failed_attempts=0,
                  locked_until=NULL,
                  updated_at=excluded.updated_at
                """,
                (args.username.strip(), hash_password(password), args.role, timestamp, timestamp),
            )
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = ?",
                (args.username.strip(),),
            ).fetchone()
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user["id"],))
            audit(
                conn,
                event_type="credentials_updated",
                outcome="success",
                user_id=user["id"],
                target_type="auth_user",
                target_key=args.username.strip(),
            )
            conn.commit()
        print("Управляющая учётная запись сохранена")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
