#!/usr/bin/env python
import argparse
import html
import json
import secrets
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


DB_PATH = Path(__file__).resolve().parent / "vuzonline_2025_full.sqlite"
SESSIONS = {}


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def page(title, body):
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; --border: #d7dce2; --muted: #667085; }}
    body {{ margin: 0; font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; color: #111827; background: #f6f7f9; }}
    header {{ display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; background: #fff; border-bottom: 1px solid var(--border); }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 20px; }}
    h1 {{ font-size: 20px; margin: 0; }}
    h2 {{ font-size: 16px; margin: 22px 0 10px; }}
    form {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 0 0 16px; }}
    input, button {{ font: inherit; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; background: #fff; }}
    button {{ cursor: pointer; background: #1f6feb; border-color: #1f6feb; color: #fff; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--border); }}
    th, td {{ padding: 7px 9px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
    th {{ background: #f0f2f5; font-weight: 600; position: sticky; top: 0; }}
    .muted {{ color: var(--muted); }}
    .panel {{ background: #fff; border: 1px solid var(--border); padding: 14px; border-radius: 8px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 16px; }}
    .metric {{ background: #fff; border: 1px solid var(--border); padding: 12px; border-radius: 8px; }}
    .metric b {{ display: block; font-size: 18px; }}
    a {{ color: #1f6feb; }}
    code {{ background: #eef1f4; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>{body}</body>
</html>"""


def row_get(row, key, default=""):
    value = row[key]
    return "" if value is None else value


class Handler(BaseHTTPRequestHandler):
    server_version = "VuzOnlineLocal/0.1"

    def send_html(self, status, content):
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def current_user(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("sid="):
                return SESSIONS.get(part[4:])
        return None

    def require_user(self):
        user = self.current_user()
        if not user:
            self.redirect("/login")
            return None
        return user

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            return self.login_page()
        if parsed.path == "/logout":
            return self.logout()
        user = self.require_user()
        if not user:
            return
        if parsed.path == "/":
            return self.index(parsed)
        if parsed.path == "/competition":
            return self.competition(parsed)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            return self.login_submit()
        self.send_error(HTTPStatus.NOT_FOUND)

    def login_page(self, error=""):
        error_html = f"<p style='color:#b42318'>{html.escape(error)}</p>" if error else ""
        self.send_html(
            HTTPStatus.OK,
            page(
                "Вход",
                f"""<main style="max-width:420px">
<h1>Vvuzz Online</h1>
<div class="panel">
{error_html}
<form method="post" action="/login">
  <input name="username" placeholder="Логин" autocomplete="username" required>
  <input name="password" placeholder="Пароль" autocomplete="current-password" required>
  <button type="submit">Войти</button>
</form>
<p class="muted">По умолчанию: <code>admin</code> / <code>admin</code>. Логины лежат в таблице <code>users</code>.</p>
</div>
</main>""",
            ),
        )

    def login_submit(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = parse_qs(self.rfile.read(length).decode("utf-8"))
        username = data.get("username", [""])[0]
        password = data.get("password", [""])[0]
        with db_connect() as conn:
            row = conn.execute(
                "SELECT username FROM users WHERE username = ? AND password_text = ?",
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

    def index(self, parsed):
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
            found = []
            if applicant_id:
                found = conn.execute(
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
                    LIMIT 500
                    """,
                    (applicant_id,),
                ).fetchall()

        result_html = ""
        if applicant_id:
            rows = []
            for row in found:
                q = urlencode({"competition_id": row["competition_id"], "applicant_id": applicant_id})
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(row_get(row, 'university')))}</td>"
                    f"<td>{html.escape(str(row_get(row, 'program')))}</td>"
                    f"<td>{html.escape(str(row_get(row, 'quota_type')))}</td>"
                    f"<td>{html.escape(str(row_get(row, 'seats')))}</td>"
                    f"<td>{html.escape(str(row_get(row, 'ordinal')))}</td>"
                    f"<td>{html.escape(str(row_get(row, 'competition_priority')))}</td>"
                    f"<td>{html.escape(str(row_get(row, 'consent_text')))}</td>"
                    f"<td>{html.escape(str(row_get(row, 'status')))}</td>"
                    f"<td><a href='/competition?{q}'>люди выше</a></td>"
                    "</tr>"
                )
            body_rows = "\n".join(rows) or "<tr><td colspan='9'>Ничего не найдено</td></tr>"
            result_html = f"""
<h2>Результаты по ID {html.escape(applicant_id)}</h2>
<table>
<thead><tr><th>Вуз</th><th>Программа</th><th>Конкурс</th><th>Мест</th><th>Позиция</th><th>Приоритет</th><th>Согласие</th><th>Статус</th><th></th></tr></thead>
<tbody>{body_rows}</tbody>
</table>"""

        self.send_html(
            HTTPStatus.OK,
            page(
                "Поиск",
                f"""<header><h1>Vvuzz Online</h1><nav><span class="muted">{html.escape(self.current_user() or "")}</span> · <a href="/logout">выйти</a></nav></header>
<main>
<div class="grid">
  <div class="metric"><span class="muted">Конкурсов</span><b>{stats['competitions']:,}</b></div>
  <div class="metric"><span class="muted">Строк абитуриентов</span><b>{stats['entries']:,}</b></div>
  <div class="metric"><span class="muted">Пользователей</span><b>{stats['users']:,}</b></div>
</div>
<form method="get" action="/">
  <input name="applicant_id" value="{html.escape(applicant_id)}" placeholder="ID участника" inputmode="numeric" required>
  <button type="submit">Найти</button>
</form>
{result_html}
</main>""",
            ),
        )

    def competition(self, parsed):
        params = parse_qs(parsed.query)
        competition_id = int(params.get("competition_id", ["0"])[0] or "0")
        applicant_id = params.get("applicant_id", [""])[0].strip()
        with db_connect() as conn:
            target = conn.execute(
                """
                SELECT e.ordinal, c.* FROM entries e
                JOIN competitions c ON c.id = e.competition_id
                WHERE c.id = ? AND e.applicant_id = ?
                """,
                (competition_id, applicant_id),
            ).fetchone()
            if not target:
                return self.send_error(HTTPStatus.NOT_FOUND)
            before_rows = conn.execute(
                """
                SELECT e1.ordinal, e1.applicant_id, e1.competition_priority,
                       e1.consent_text, e1.has_consent, e1.status
                FROM entries e1
                WHERE e1.competition_id = ? AND e1.ordinal < ?
                ORDER BY e1.ordinal
                """,
                (competition_id, target["ordinal"]),
            ).fetchall()

            before_ids = [row["applicant_id"] for row in before_rows]
            elsewhere_ids = set()
            for start in range(0, len(before_ids), 800):
                batch = before_ids[start : start + 800]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                elsewhere_ids.update(
                    row["applicant_id"]
                    for row in conn.execute(
                        f"""
                        SELECT DISTINCT applicant_id
                        FROM entries
                        WHERE has_consent = 1
                          AND competition_id <> ?
                          AND applicant_id IN ({placeholders})
                        """,
                        [competition_id, *batch],
                    )
                )

        total_before = len(before_rows)
        consent_here = sum(1 for row in before_rows if row["has_consent"])
        consent_elsewhere = len(elsewhere_ids)
        undecided_est = max(total_before - consent_here - consent_elsewhere, 0)
        sample = before_rows[:200]
        rows = "\n".join(
            "<tr>"
            f"<td>{row['ordinal']}</td><td>{html.escape(row['applicant_id'])}</td>"
            f"<td>{html.escape(str(row_get(row, 'competition_priority')))}</td>"
            f"<td>{html.escape(str(row_get(row, 'consent_text')))}</td>"
            f"<td>{html.escape(str(row_get(row, 'status')))}</td>"
            "</tr>"
            for row in sample
        )
        self.send_html(
            HTTPStatus.OK,
            page(
                "Конкурс",
                f"""<header><h1>Vvuzz Online</h1><nav><a href="/?applicant_id={html.escape(applicant_id)}">назад</a> · <a href="/logout">выйти</a></nav></header>
<main>
<h2>{html.escape(target['university'] or '')}</h2>
<p>{html.escape(target['program'] or '')}<br>{html.escape(target['quota_type'] or '')}, мест: {html.escape(str(row_get(target, 'seats')))}, позиция ID {html.escape(applicant_id)}: {target['ordinal']}</p>
<div class="grid">
  <div class="metric"><span class="muted">Людей выше</span><b>{total_before}</b></div>
  <div class="metric"><span class="muted">Согласие здесь</span><b>{consent_here}</b></div>
  <div class="metric"><span class="muted">Согласие в других конкурсах</span><b>{consent_elsewhere or 0}</b></div>
  <div class="metric"><span class="muted">Без найденного согласия</span><b>{undecided_est}</b></div>
</div>
<h2>Первые 200 людей выше</h2>
<table>
<thead><tr><th>Позиция</th><th>ID</th><th>Приоритет</th><th>Согласие</th><th>Статус</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</main>""",
            ),
        )


def main():
    parser = argparse.ArgumentParser(description="Local web UI for VUZ online SQLite archive.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}. Run import_zip_to_sqlite.py first.")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
