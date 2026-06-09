#!/usr/bin/env python
import argparse
import datetime as dt
import html
import json
import os
import secrets
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from chance import ChanceResult, calculate_chance


DB_PATH = Path(os.getenv("VVUZZ_DB_PATH", Path(__file__).resolve().parent / "vuzonline_2025_norm.sqlite"))
INGEST_TOKEN = os.getenv("VVUZZ_INGEST_TOKEN", "")
SESSIONS: dict[str, str] = {}
ROLES = ("admin", "manager", "applicant")


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_get(row, key, default=""):
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def role_title(role: str) -> str:
    return {"admin": "admin", "manager": "manager", "applicant": "поступающий"}.get(role, role)


def ensure_schema() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'manager'")
        if "applicant_id" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN applicant_id TEXT")
        if "is_active" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN updated_at TEXT")
        conn.execute("UPDATE users SET role = 'manager' WHERE role IS NULL OR role = ''")
        conn.execute("UPDATE users SET is_active = 1 WHERE is_active IS NULL")
        conn.execute("UPDATE users SET updated_at = COALESCE(updated_at, created_at, ?) WHERE updated_at IS NULL", (now_iso(),))
        conn.execute(
            """
            INSERT INTO users(username, password_text, role, applicant_id, is_active, created_at, updated_at)
            VALUES (?, ?, 'admin', NULL, 1, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_text = excluded.password_text,
                role = 'admin',
                is_active = 1,
                updated_at = excluded.updated_at
            """,
            ("prointdata@ya.ru", "admin", now_iso(), now_iso()),
        )
        conn.execute(
            "UPDATE users SET is_active = 0, updated_at = ? WHERE username = 'admin'",
            (now_iso(),),
        )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingest_runs (
                run_id TEXT PRIMARY KEY,
                source TEXT,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                config_json TEXT
            );
            CREATE TABLE IF NOT EXISTS ingest_batches (
                id INTEGER PRIMARY KEY,
                run_id TEXT,
                source_key TEXT,
                payload_sha256 TEXT,
                fetched_at TEXT,
                received_at TEXT NOT NULL,
                competitions INTEGER NOT NULL DEFAULT 0,
                applications INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS source_snapshots (
                source_key TEXT PRIMARY KEY,
                source_path TEXT,
                payload_sha256 TEXT,
                fetched_at TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    :root {{ color-scheme: light; --border: #d7dce2; --muted: #667085; --blue: #1f6feb; --bg: #f6f7f9; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; color: #111827; background: var(--bg); }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 20px; background: #fff; border-bottom: 1px solid var(--border); }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 20px; }}
    h1 {{ font-size: 20px; margin: 0; }}
    h2 {{ font-size: 16px; margin: 22px 0 10px; }}
    nav {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; justify-content: flex-end; }}
    form {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 0 0 16px; }}
    input, select, button {{ font: inherit; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; background: #fff; min-height: 36px; }}
    button {{ cursor: pointer; background: var(--blue); border-color: var(--blue); color: #fff; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--border); }}
    th, td {{ padding: 7px 9px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
    th {{ background: #f0f2f5; font-weight: 600; position: sticky; top: 0; }}
    .muted {{ color: var(--muted); }}
    .panel {{ background: #fff; border: 1px solid var(--border); padding: 14px; border-radius: 8px; margin-bottom: 16px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 16px; }}
    .metric {{ background: #fff; border: 1px solid var(--border); padding: 12px; border-radius: 8px; }}
    .metric b {{ display: block; font-size: 18px; }}
    .pill {{ display: inline-block; padding: 2px 7px; border-radius: 999px; background: #eef1f4; }}
    .danger {{ color: #b42318; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background: #eef1f4; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>{body}</body>
</html>"""


def nav_html(user: sqlite3.Row | None = None) -> str:
    if not user:
        return '<nav><a href="/public">публичный поиск</a><a href="/login">вход</a></nav>'
    links = ['<a href="/">поиск ID</a>']
    if user["role"] in {"admin", "manager"}:
        links += [
            '<a href="/universities">вузы</a>',
            '<a href="/competitions">конкурсы</a>',
            '<a href="/applicants">абитуриенты</a>',
            '<a href="/status">статус данных</a>',
        ]
    if user["role"] == "admin":
        links.append('<a href="/admin/users">пользователи</a>')
    links.append(f'<span class="muted">{esc(user["username"])} · {role_title(user["role"])}</span>')
    links.append('<a href="/logout">выйти</a>')
    return "<nav>" + "".join(links) + "</nav>"


def header(title: str, user: sqlite3.Row | None = None) -> str:
    return f"<header><h1>{esc(title)}</h1>{nav_html(user)}</header>"


def metric(label: str, value) -> str:
    return f'<div class="metric"><span class="muted">{esc(label)}</span><b>{esc(value)}</b></div>'


def chance_html(result: ChanceResult) -> str:
    percent = "н/д" if result.chance_percent is None else f"{result.chance_percent}%"
    return (
        f'<span class="pill">{esc(result.chance_label)} · {percent}</span>'
        f'<br><span class="muted">эффективно выше: {result.effective_blockers}, прогнозное место: {result.projected_rank}</span>'
    )


def user_by_session(cookie: str) -> sqlite3.Row | None:
    sid = ""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("sid="):
            sid = part[4:]
            break
    username = SESSIONS.get(sid)
    if not username:
        return None
    with db_connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username,),
        ).fetchone()


def get_or_create_name(conn: sqlite3.Connection, table: str, value: str | None) -> int | None:
    if not value:
        return None
    conn.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES (?)", (value,))
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (value,)).fetchone()
    return row[0] if row else None


def upsert_application(conn: sqlite3.Connection, competition_id: int, app: dict) -> bool:
    applicant_external = str(app.get("applicant_id") or "").strip()
    if not applicant_external:
        return False
    conn.execute("INSERT OR IGNORE INTO applicants(applicant_id) VALUES (?)", (applicant_external,))
    applicant_row = conn.execute("SELECT id FROM applicants WHERE applicant_id = ?", (applicant_external,)).fetchone()
    existing = conn.execute(
        "SELECT id FROM applications WHERE competition_id = ? AND applicant_id = ? LIMIT 1",
        (competition_id, applicant_row["id"]),
    ).fetchone()
    values = (
        competition_id,
        applicant_row["id"],
        app.get("ordinal"),
        app.get("competition_priority"),
        app.get("consent_text"),
        1 if app.get("has_consent") else 0,
        app.get("score_total"),
        app.get("exam_scores_text"),
        app.get("individual_score"),
        app.get("status"),
        app.get("selected_at_moscow"),
    )
    if existing:
        conn.execute(
            """
            UPDATE applications
            SET ordinal=?, competition_priority=?, consent_text=?, has_consent=?,
                score_total=?, exam_scores_text=?, individual_score=?, status=?, selected_at_moscow=?
            WHERE id=?
            """,
            values[2:] + (existing["id"],),
        )
        return False
    conn.execute(
        """
        INSERT INTO applications(
            competition_id, applicant_id, ordinal, competition_priority, consent_text,
            has_consent, score_total, exam_scores_text, individual_score, status, selected_at_moscow
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    return True


def competition_metrics(conn: sqlite3.Connection, competition_id: int, applicant_id: str) -> tuple[sqlite3.Row | None, ChanceResult | None, dict]:
    target = conn.execute(
        """
        SELECT e.ordinal, e.applicant_id, e.competition_priority, e.consent_text, e.has_consent,
               e.score_total, e.status, c.*
        FROM entries e
        JOIN competitions c ON c.id = e.competition_id
        WHERE c.id = ? AND e.applicant_id = ?
        """,
        (competition_id, applicant_id),
    ).fetchone()
    if not target:
        return None, None, {}
    total_before = conn.execute(
        "SELECT count(*) FROM entries WHERE competition_id = ? AND ordinal < ?",
        (competition_id, target["ordinal"]),
    ).fetchone()[0]
    consent_here = conn.execute(
        """
        SELECT count(*) FROM entries
        WHERE competition_id = ? AND ordinal < ? AND has_consent = 1
        """,
        (competition_id, target["ordinal"]),
    ).fetchone()[0]
    consent_elsewhere = conn.execute(
        """
        SELECT count(DISTINCT e1.applicant_id)
        FROM entries e1
        WHERE e1.competition_id = ?
          AND e1.ordinal < ?
          AND e1.has_consent = 0
          AND EXISTS (
              SELECT 1 FROM entries e2
              WHERE e2.applicant_id = e1.applicant_id
                AND e2.competition_id <> ?
                AND e2.has_consent = 1
          )
        """,
        (competition_id, target["ordinal"], competition_id),
    ).fetchone()[0]
    no_consent = max(total_before - consent_here - consent_elsewhere, 0)
    result = calculate_chance(row_get(target, "seats", None), consent_here, consent_elsewhere, no_consent)
    metrics = {
        "total_before": total_before,
        "consent_here": consent_here,
        "consent_elsewhere": consent_elsewhere,
        "no_consent": no_consent,
    }
    return target, result, metrics


class Handler(BaseHTTPRequestHandler):
    server_version = "VuzOnlineLocal/0.2"

    def send_html(self, status, content, head_only: bool = False):
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def send_json(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def current_user(self):
        return user_by_session(self.headers.get("Cookie", ""))

    def require_user(self):
        user = self.current_user()
        if not user:
            self.redirect("/login")
            return None
        return user

    def require_role(self, allowed: set[str]):
        user = self.require_user()
        if not user:
            return None
        if user["role"] not in allowed:
            self.send_error(HTTPStatus.FORBIDDEN)
            return None
        return user

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/login", "/public"}:
            return self.send_html(HTTPStatus.OK, "", head_only=True)
        self.send_response(HTTPStatus.OK)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/public":
            return self.public_index()
        if parsed.path == "/public/applicant":
            return self.applicant_page(parsed, public=True)
        if parsed.path == "/public/competition":
            return self.competition_page(parsed, public=True)
        if parsed.path == "/login":
            return self.login_page()
        if parsed.path == "/logout":
            return self.logout()

        user = self.require_user()
        if not user:
            return
        if parsed.path == "/":
            if user["role"] == "applicant" and user["applicant_id"]:
                return self.redirect("/applicant?" + urlencode({"id": user["applicant_id"]}))
            return self.search_page(parsed, user)
        if parsed.path == "/universities":
            return self.universities_page(parsed, user)
        if parsed.path == "/university":
            return self.university_page(parsed, user)
        if parsed.path == "/competitions":
            return self.competitions_page(parsed, user)
        if parsed.path == "/competition":
            return self.competition_page(parsed, public=False, user=user)
        if parsed.path == "/applicants":
            return self.applicants_page(parsed, user)
        if parsed.path == "/applicant":
            return self.applicant_page(parsed, public=False, user=user)
        if parsed.path == "/status":
            return self.status_page(user)
        if parsed.path == "/admin/users":
            return self.admin_users_page(user)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            return self.login_submit()
        if parsed.path == "/api/ingest/batch":
            return self.ingest_batch()
        user = self.require_user()
        if not user:
            return
        if parsed.path == "/admin/users":
            if user["role"] != "admin":
                return self.send_error(HTTPStatus.FORBIDDEN)
            return self.admin_users_submit()
        self.send_error(HTTPStatus.NOT_FOUND)

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        return parse_qs(self.rfile.read(length).decode("utf-8"))

    def login_page(self, error=""):
        error_html = f"<p class='danger'>{esc(error)}</p>" if error else ""
        self.send_html(
            HTTPStatus.OK,
            page(
                "Вход",
                f"""{header("Vvuzz Online")}
<main style="max-width:440px">
<div class="panel">
{error_html}
<form method="post" action="/login">
  <input name="username" placeholder="Логин" autocomplete="username" required>
  <input name="password" placeholder="Пароль" autocomplete="current-password" required>
  <button type="submit">Войти</button>
</form>
<p class="muted"><a href="/public">Публичный поиск по ID участника</a></p>
</div>
</main>""",
            ),
        )

    def login_submit(self):
        data = self.read_form()
        username = data.get("username", [""])[0].strip()
        password = data.get("password", [""])[0]
        with db_connect() as conn:
            row = conn.execute(
                "SELECT username FROM users WHERE username = ? AND password_text = ? AND is_active = 1",
                (username, password),
            ).fetchone()
        if not row:
            return self.login_page("Неверный логин или пароль")
        sid = secrets.token_urlsafe(24)
        SESSIONS[sid] = username
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", f"sid={sid}; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def logout(self):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", "sid=; Max-Age=0; Path=/")
        self.end_headers()

    def public_index(self):
        self.send_html(
            HTTPStatus.OK,
            page(
                "Публичный поиск",
                f"""{header("Vvuzz Online")}
<main style="max-width:720px">
<div class="panel">
<h2>Публичный поиск по ID участника</h2>
<form method="get" action="/public/applicant">
  <input name="id" placeholder="ID участника" inputmode="numeric" required>
  <button type="submit">Показать мои конкурсы</button>
</form>
<p class="muted">Сервис агрегирует публичные данные Госуслуг. Оценка шанса является эвристикой и не гарантирует зачисление.</p>
</div>
</main>""",
            ),
        )

    def search_page(self, parsed, user):
        params = parse_qs(parsed.query)
        applicant_id = params.get("applicant_id", [""])[0].strip()
        with db_connect() as conn:
            stats = conn.execute(
                """
                SELECT
                  (SELECT count(*) FROM competitions) AS competitions,
                  (SELECT count(*) FROM entries) AS entries,
                  (SELECT count(*) FROM users) AS users
                """
            ).fetchone()
        results = ""
        if applicant_id:
            return self.redirect("/applicant?" + urlencode({"id": applicant_id}))
        self.send_html(
            HTTPStatus.OK,
            page(
                "Поиск",
                f"""{header("Vvuzz Online", user)}
<main>
<div class="grid">
  {metric("Конкурсов", f"{stats['competitions']:,}")}
  {metric("Строк абитуриентов", f"{stats['entries']:,}")}
  {metric("Пользователей", f"{stats['users']:,}")}
</div>
<form method="get" action="/">
  <input name="applicant_id" value="{esc(applicant_id)}" placeholder="ID участника" inputmode="numeric" required>
  <button type="submit">Найти</button>
</form>
{results}
</main>""",
            ),
        )

    def applicant_rows(self, conn, applicant_id: str):
        return conn.execute(
            """
            SELECT
              e.applicant_id, e.ordinal, e.competition_priority, e.consent_text,
              e.has_consent, e.score_total, e.status,
              c.id AS competition_id, c.program, c.education_level, c.city,
              c.university, c.study_form, c.quota_type, c.seats, c.row_count
            FROM entries e
            JOIN competitions c ON c.id = e.competition_id
            WHERE e.applicant_id = ?
            ORDER BY c.university, c.program, c.quota_type
            LIMIT 1000
            """,
            (applicant_id,),
        ).fetchall()

    def applicant_page(self, parsed, public: bool, user=None):
        params = parse_qs(parsed.query)
        applicant_id = params.get("id", params.get("applicant_id", [""]))[0].strip()
        if not applicant_id:
            return self.redirect("/public" if public else "/")
        if user and user["role"] == "applicant" and user["applicant_id"] and user["applicant_id"] != applicant_id:
            return self.send_error(HTTPStatus.FORBIDDEN)
        with db_connect() as conn:
            found = self.applicant_rows(conn, applicant_id)
            rendered_rows = []
            for row in found:
                _, chance, metrics = competition_metrics(conn, row["competition_id"], applicant_id)
                if public:
                    q = urlencode({"id": row["competition_id"], "focus_applicant_id": applicant_id})
                    link = f"<a href='/public/competition?{q}'>детали</a>"
                else:
                    q = urlencode({"id": row["competition_id"], "focus_applicant_id": applicant_id})
                    link = f"<a href='/competition?{q}'>конкурс</a>"
                rendered_rows.append(
                    "<tr>"
                    f"<td>{esc(row['university'])}</td>"
                    f"<td>{esc(row['program'])}</td>"
                    f"<td>{esc(row['quota_type'])}</td>"
                    f"<td>{esc(row['seats'])}</td>"
                    f"<td>{esc(row['ordinal'])}</td>"
                    f"<td>{esc(row['competition_priority'])}</td>"
                    f"<td>{esc(row['consent_text'])}</td>"
                    f"<td>{esc(row['status'])}</td>"
                    f"<td>{chance_html(chance) if chance else 'н/д'}</td>"
                    f"<td>{link}</td>"
                    "</tr>"
                )
        rows_html = "\n".join(rendered_rows) or "<tr><td colspan='10'>Ничего не найдено</td></tr>"
        back_header = header("Публичный поиск", None) if public else header("Карточка абитуриента", user)
        public_note = "<p class='muted'>Публичный режим не дает переходить на страницы соперников.</p>" if public else ""
        self.send_html(
            HTTPStatus.OK,
            page(
                "Абитуриент",
                f"""{back_header}
<main>
<h2>ID участника {esc(applicant_id)}</h2>
{public_note}
<table>
<thead><tr><th>Вуз</th><th>Программа</th><th>Конкурс</th><th>Мест</th><th>Позиция</th><th>Приоритет</th><th>Согласие</th><th>Статус</th><th>Шанс</th><th></th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
</main>""",
            ),
        )

    def competition_page(self, parsed, public: bool, user=None):
        params = parse_qs(parsed.query)
        competition_id = int(params.get("id", params.get("competition_id", ["0"]))[0] or "0")
        applicant_id = params.get("focus_applicant_id", params.get("applicant_id", [""]))[0].strip()
        if user and user["role"] == "applicant":
            applicant_id = user["applicant_id"] or applicant_id
        with db_connect() as conn:
            comp = conn.execute("SELECT * FROM competitions WHERE id = ?", (competition_id,)).fetchone()
            if not comp:
                return self.send_error(HTTPStatus.NOT_FOUND)
            target = chance = metrics = None
            if applicant_id:
                target, chance, metrics = competition_metrics(conn, competition_id, applicant_id)
            limit = 200
            if target:
                participants = conn.execute(
                    """
                    SELECT ordinal, applicant_id, competition_priority, consent_text, has_consent, status
                    FROM entries
                    WHERE competition_id = ? AND ordinal < ?
                    ORDER BY ordinal
                    LIMIT ?
                    """,
                    (competition_id, target["ordinal"], limit),
                ).fetchall()
                section_title = "Первые 200 людей выше"
            else:
                participants = conn.execute(
                    """
                    SELECT ordinal, applicant_id, competition_priority, consent_text, has_consent, status
                    FROM entries
                    WHERE competition_id = ?
                    ORDER BY ordinal
                    LIMIT ?
                    """,
                    (competition_id, limit),
                ).fetchall()
                section_title = "Первые 200 участников"
        participant_rows = []
        for row in participants:
            if public:
                applicant_cell = "скрыто в публичном режиме" if row["applicant_id"] != applicant_id else esc(row["applicant_id"])
            else:
                applicant_cell = f"<a href='/applicant?{urlencode({'id': row['applicant_id']})}'>{esc(row['applicant_id'])}</a>"
            participant_rows.append(
                "<tr>"
                f"<td>{esc(row['ordinal'])}</td><td>{applicant_cell}</td>"
                f"<td>{esc(row['competition_priority'])}</td><td>{esc(row['consent_text'])}</td><td>{esc(row['status'])}</td>"
                "</tr>"
            )
        metrics_html = ""
        if target and chance:
            metrics_html = f"""
<div class="grid">
  {metric("Людей выше", metrics['total_before'])}
  {metric("Согласие здесь", metrics['consent_here'])}
  {metric("Согласие в других конкурсах", metrics['consent_elsewhere'])}
  {metric("Без найденного согласия", metrics['no_consent'])}
  {metric("Оценка", f"{chance.chance_label} · {'н/д' if chance.chance_percent is None else str(chance.chance_percent) + '%'}")}
</div>
<p class="muted">{esc(chance.explanation)}</p>"""
        title = "Публичный конкурс" if public else "Конкурс"
        self.send_html(
            HTTPStatus.OK,
            page(
                title,
                f"""{header(title, None if public else user)}
<main>
<h2>{esc(comp['university'])}</h2>
<p>{esc(comp['program'])}<br>{esc(comp['quota_type'])}, мест: {esc(comp['seats'])}, строк: {esc(comp['row_count'])}</p>
{metrics_html}
<h2>{section_title}</h2>
<table>
<thead><tr><th>Позиция</th><th>ID</th><th>Приоритет</th><th>Согласие</th><th>Статус</th></tr></thead>
<tbody>{''.join(participant_rows) or '<tr><td colspan="5">Нет строк</td></tr>'}</tbody>
</table>
</main>""",
            ),
        )

    def universities_page(self, parsed, user):
        if user["role"] not in {"admin", "manager"}:
            return self.send_error(HTTPStatus.FORBIDDEN)
        q = parse_qs(parsed.query).get("q", [""])[0].strip()
        like = f"%{q}%"
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.name, count(DISTINCT c.id) AS competitions,
                       count(DISTINCT c.program_id) AS programs, COALESCE(sum(c.row_count), 0) AS rows_count
                FROM universities u
                LEFT JOIN competitions_core c ON c.university_id = u.id
                WHERE ? = '' OR u.name LIKE ?
                GROUP BY u.id, u.name
                ORDER BY u.name
                LIMIT 300
                """,
                (q, like),
            ).fetchall()
        body_rows = "".join(
            f"<tr><td><a href='/university?id={row['id']}'>{esc(row['name'])}</a></td><td>{row['competitions']}</td><td>{row['programs']}</td><td>{row['rows_count']}</td></tr>"
            for row in rows
        )
        self.send_html(HTTPStatus.OK, page("Вузы", f"""{header("Вузы", user)}<main>
<form><input name="q" value="{esc(q)}" placeholder="Название вуза"><button>Найти</button></form>
<table><thead><tr><th>Вуз</th><th>Конкурсов</th><th>Программ</th><th>Строк</th></tr></thead><tbody>{body_rows}</tbody></table>
</main>"""))

    def university_page(self, parsed, user):
        if user["role"] not in {"admin", "manager"}:
            return self.send_error(HTTPStatus.FORBIDDEN)
        uid = int(parse_qs(parsed.query).get("id", ["0"])[0] or "0")
        with db_connect() as conn:
            uni = conn.execute("SELECT * FROM universities WHERE id = ?", (uid,)).fetchone()
            if not uni:
                return self.send_error(HTTPStatus.NOT_FOUND)
            rows = conn.execute(
                """
                SELECT c.id, p.name AS program, c.education_level, c.city, c.study_form,
                       c.quota_type, c.seats, c.row_count
                FROM competitions_core c
                LEFT JOIN programs p ON p.id = c.program_id
                WHERE c.university_id = ?
                ORDER BY p.name, c.quota_type
                LIMIT 500
                """,
                (uid,),
            ).fetchall()
        body_rows = "".join(
            f"<tr><td><a href='/competition?id={row['id']}'>{esc(row['program'])}</a></td><td>{esc(row['quota_type'])}</td><td>{esc(row['study_form'])}</td><td>{esc(row['seats'])}</td><td>{esc(row['row_count'])}</td></tr>"
            for row in rows
        )
        self.send_html(HTTPStatus.OK, page("Вуз", f"""{header("Вуз", user)}<main>
<h2>{esc(uni['name'])}</h2>
<table><thead><tr><th>Программа</th><th>Конкурс</th><th>Форма</th><th>Мест</th><th>Строк</th></tr></thead><tbody>{body_rows}</tbody></table>
</main>"""))

    def competitions_page(self, parsed, user):
        if user["role"] not in {"admin", "manager"}:
            return self.send_error(HTTPStatus.FORBIDDEN)
        params = parse_qs(parsed.query)
        q = params.get("q", [""])[0].strip()
        like = f"%{q}%"
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT id, university, program, city, study_form, quota_type, seats, row_count, imported_at
                FROM competitions
                WHERE ? = '' OR university LIKE ? OR program LIKE ? OR quota_type LIKE ?
                ORDER BY university, program, quota_type
                LIMIT 500
                """,
                (q, like, like, like),
            ).fetchall()
        body_rows = "".join(
            f"<tr><td>{esc(row['university'])}</td><td><a href='/competition?id={row['id']}'>{esc(row['program'])}</a></td><td>{esc(row['quota_type'])}</td><td>{esc(row['study_form'])}</td><td>{esc(row['seats'])}</td><td>{esc(row['row_count'])}</td><td>{esc(row['imported_at'])}</td></tr>"
            for row in rows
        )
        self.send_html(HTTPStatus.OK, page("Конкурсы", f"""{header("Конкурсы", user)}<main>
<form><input name="q" value="{esc(q)}" placeholder="Вуз, программа, конкурс"><button>Найти</button></form>
<table><thead><tr><th>Вуз</th><th>Программа</th><th>Конкурс</th><th>Форма</th><th>Мест</th><th>Строк</th><th>Импорт</th></tr></thead><tbody>{body_rows}</tbody></table>
</main>"""))

    def applicants_page(self, parsed, user):
        if user["role"] not in {"admin", "manager"}:
            return self.send_error(HTTPStatus.FORBIDDEN)
        q = parse_qs(parsed.query).get("q", [""])[0].strip()
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT ap.applicant_id, count(a.id) AS applications,
                       sum(CASE WHEN a.has_consent = 1 THEN 1 ELSE 0 END) AS consents
                FROM applicants ap
                JOIN applications a ON a.applicant_id = ap.id
                WHERE ? = '' OR ap.applicant_id LIKE ?
                GROUP BY ap.applicant_id
                ORDER BY applications DESC, ap.applicant_id
                LIMIT 500
                """,
                (q, f"%{q}%"),
            ).fetchall()
        body_rows = "".join(
            f"<tr><td><a href='/applicant?id={esc(row['applicant_id'])}'>{esc(row['applicant_id'])}</a></td><td>{row['applications']}</td><td>{row['consents']}</td></tr>"
            for row in rows
        )
        self.send_html(HTTPStatus.OK, page("Абитуриенты", f"""{header("Абитуриенты", user)}<main>
<form><input name="q" value="{esc(q)}" placeholder="ID участника"><button>Найти</button></form>
<table><thead><tr><th>ID</th><th>Конкурсов</th><th>Согласий</th></tr></thead><tbody>{body_rows}</tbody></table>
</main>"""))

    def status_page(self, user):
        if user["role"] not in {"admin", "manager"}:
            return self.send_error(HTTPStatus.FORBIDDEN)
        with db_connect() as conn:
            stats = conn.execute(
                """
                SELECT
                  (SELECT count(*) FROM applicants) AS applicants,
                  (SELECT count(*) FROM universities) AS universities,
                  (SELECT count(*) FROM programs) AS programs,
                  (SELECT count(*) FROM competitions_core) AS competitions,
                  (SELECT count(*) FROM applications) AS applications,
                  (SELECT count(*) FROM ingest_batches) AS ingest_batches
                """
            ).fetchone()
        self.send_html(HTTPStatus.OK, page("Статус данных", f"""{header("Статус данных", user)}<main>
<div class="grid">
{metric("Абитуриентов", f"{stats['applicants']:,}")}{metric("Вузов", stats['universities'])}{metric("Программ", stats['programs'])}
{metric("Конкурсов", f"{stats['competitions']:,}")}{metric("Заявлений", f"{stats['applications']:,}")}{metric("Ingest batch", stats['ingest_batches'])}
</div>
</main>"""))

    def admin_users_page(self, user):
        if user["role"] != "admin":
            return self.send_error(HTTPStatus.FORBIDDEN)
        with db_connect() as conn:
            users = conn.execute("SELECT * FROM users ORDER BY is_active DESC, username").fetchall()
        rows = []
        for row in users:
            role_options = "".join(
                f"<option value='{role}' {'selected' if row['role'] == role else ''}>{role_title(role)}</option>"
                for role in ROLES
            )
            rows.append(
                f"""<tr><form method="post" action="/admin/users">
<td><input type="hidden" name="original_username" value="{esc(row['username'])}"><input name="username" value="{esc(row['username'])}" required></td>
<td><input name="password_text" value="{esc(row['password_text'])}" required></td>
<td><select name="role">{role_options}</select></td>
<td><input name="applicant_id" value="{esc(row['applicant_id'])}"></td>
<td><select name="is_active"><option value="1" {'selected' if row['is_active'] else ''}>активен</option><option value="0" {'selected' if not row['is_active'] else ''}>выключен</option></select></td>
<td><button name="action" value="save">Сохранить</button></td>
</form></tr>"""
            )
        new_role_options = "".join(f"<option value='{role}'>{role_title(role)}</option>" for role in ROLES)
        rows.append(
            f"""<tr><form method="post" action="/admin/users">
<td><input name="username" placeholder="логин" required></td>
<td><input name="password_text" placeholder="пароль" required></td>
<td><select name="role">{new_role_options}</select></td>
<td><input name="applicant_id" placeholder="ID участника"></td>
<td><select name="is_active"><option value="1">активен</option><option value="0">выключен</option></select></td>
<td><button name="action" value="save">Добавить</button></td>
</form></tr>"""
        )
        self.send_html(HTTPStatus.OK, page("Пользователи", f"""{header("Пользователи", user)}<main>
<table><thead><tr><th>Логин</th><th>Пароль</th><th>Роль</th><th>ID участника</th><th>Статус</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</main>"""))

    def admin_users_submit(self):
        data = self.read_form()
        original = data.get("original_username", [""])[0].strip()
        username = data.get("username", [""])[0].strip()
        password = data.get("password_text", [""])[0]
        role = data.get("role", ["manager"])[0]
        applicant_id = data.get("applicant_id", [""])[0].strip() or None
        is_active = 1 if data.get("is_active", ["1"])[0] == "1" else 0
        if role not in ROLES or not username or not password:
            return self.send_error(HTTPStatus.BAD_REQUEST)
        with db_connect() as conn:
            if original and original != username:
                conn.execute("DELETE FROM users WHERE username = ?", (original,))
            conn.execute(
                """
                INSERT INTO users(username, password_text, role, applicant_id, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_text=excluded.password_text,
                    role=excluded.role,
                    applicant_id=excluded.applicant_id,
                    is_active=excluded.is_active,
                    updated_at=excluded.updated_at
                """,
                (username, password, role, applicant_id, is_active, now_iso(), now_iso()),
            )
            conn.commit()
        self.redirect("/admin/users")

    def ingest_batch(self):
        token = self.headers.get("X-Vvuzz-Ingest-Token", "")
        if not INGEST_TOKEN:
            return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "ingest token is not configured"})
        if not secrets.compare_digest(token, INGEST_TOKEN):
            return self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "bad ingest token"})
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        run_id = str(payload.get("run_id") or "")
        competitions = payload.get("competitions") or []
        inserted_apps = 0
        updated_apps = 0
        errors = []
        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO ingest_runs(run_id, source, started_at, updated_at, status, config_json)
                VALUES (?, ?, ?, ?, 'running', ?)
                ON CONFLICT(run_id) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (run_id, payload.get("source"), payload.get("started_at"), now_iso(), json.dumps(payload.get("config") or {}, ensure_ascii=False)),
            )
            for comp in competitions:
                try:
                    source_path = comp["source_path"]
                    program_id = get_or_create_name(conn, "programs", comp.get("program"))
                    university_id = get_or_create_name(conn, "universities", comp.get("university"))
                    existing = conn.execute("SELECT id FROM competitions_core WHERE source_path = ?", (source_path,)).fetchone()
                    values = (
                        source_path,
                        comp.get("file_name") or Path(source_path).name,
                        program_id,
                        comp.get("education_level"),
                        comp.get("city"),
                        university_id,
                        comp.get("study_form"),
                        comp.get("quota_type"),
                        comp.get("seats"),
                        json.dumps(comp.get("path") or [], ensure_ascii=False),
                        len(comp.get("applications") or []),
                        payload.get("fetched_at") or now_iso(),
                    )
                    if existing:
                        competition_id = existing["id"]
                        conn.execute(
                            """
                            UPDATE competitions_core
                            SET file_name=?, program_id=?, education_level=?, city=?, university_id=?,
                                study_form=?, quota_type=?, seats=?, path_json=?, row_count=?, imported_at=?
                            WHERE id=?
                            """,
                            values[1:] + (competition_id,),
                        )
                    else:
                        cur = conn.execute(
                            """
                            INSERT INTO competitions_core(
                                source_path, file_name, program_id, education_level, city, university_id,
                                study_form, quota_type, seats, path_json, row_count, imported_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            values,
                        )
                        competition_id = cur.lastrowid
                    for app in comp.get("applications") or []:
                        if upsert_application(conn, competition_id, app):
                            inserted_apps += 1
                        else:
                            updated_apps += 1
                    conn.execute(
                        """
                        INSERT INTO source_snapshots(source_key, source_path, payload_sha256, fetched_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(source_key) DO UPDATE SET
                            source_path=excluded.source_path,
                            payload_sha256=excluded.payload_sha256,
                            fetched_at=excluded.fetched_at,
                            updated_at=excluded.updated_at
                        """,
                        (payload.get("source_key") or source_path, source_path, payload.get("payload_sha256"), payload.get("fetched_at"), now_iso()),
                    )
                except Exception as exc:
                    errors.append(str(exc))
            conn.execute(
                """
                INSERT INTO ingest_batches(run_id, source_key, payload_sha256, fetched_at, received_at, competitions, applications, errors)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, payload.get("source_key"), payload.get("payload_sha256"), payload.get("fetched_at"), now_iso(), len(competitions), inserted_apps + updated_apps, len(errors)),
            )
            conn.commit()
        status = HTTPStatus.ACCEPTED if not errors else HTTPStatus.BAD_REQUEST
        self.send_json(status, {"accepted": not errors, "inserted": inserted_apps, "updated": updated_apps, "skipped": 0, "errors": errors})


def main() -> int:
    parser = argparse.ArgumentParser(description="Vvuzz Online web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}. Run import_zip_to_norm_sqlite.py first.")
    ensure_schema()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
