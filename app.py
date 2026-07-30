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


def like_prefix(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


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
            ON CONFLICT(username) DO NOTHING
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
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --surface: #ffffff;
      --surface-soft: #f8fafc;
      --text: #111827;
      --muted: #667085;
      --border: #d7dce2;
      --border-strong: #c5ccd6;
      --blue: #1f6feb;
      --blue-dark: #1557bd;
      --green: #16794c;
      --shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
    }}
    body, header, main, section, div, form, table, th, td, input, select, button {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 1rem/1.5 system-ui, -apple-system, Segoe UI, sans-serif; color: var(--text); background: var(--bg); }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 24px; background: rgba(255, 255, 255, 0.92); border-bottom: 1px solid var(--border); }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 20px; }}
    h1 {{ font-size: 1.25rem; margin: 0; }}
    h2 {{ font-size: 1.05rem; margin: 22px 0 10px; }}
    h3 {{ font-size: 1rem; margin: 0 0 12px; }}
    p {{ text-wrap: pretty; }}
    nav {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; justify-content: flex-end; }}
    form {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 0 0 16px; }}
    label {{ display: block; font-size: 0.84rem; font-weight: 650; color: #344054; margin-bottom: 6px; }}
    input, select, button {{ font: inherit; padding: 9px 11px; border: 1px solid var(--border); border-radius: 7px; background: #fff; min-height: 40px; }}
    input:focus-visible, select:focus-visible, button:focus-visible, a:focus-visible {{ outline: 3px solid rgba(31, 111, 235, 0.3); outline-offset: 2px; }}
    button {{ cursor: pointer; background: var(--blue); border-color: var(--blue); color: #fff; font-weight: 650; }}
    button:hover {{ background: var(--blue-dark); border-color: var(--blue-dark); }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--border); }}
    th, td {{ padding: 7px 9px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
    th {{ background: #f0f2f5; font-weight: 600; position: sticky; top: 0; }}
    .muted {{ color: var(--muted); }}
    .panel {{ background: #fff; border: 1px solid var(--border); padding: 14px; border-radius: 8px; margin-bottom: 16px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 16px; }}
    .metric {{ display: block; background: #fff; border: 1px solid var(--border); padding: 12px; border-radius: 8px; color: var(--text); min-height: 100px; }}
    .metric b {{ display: block; font-size: 18px; }}
    .metric small {{ display: block; margin-top: 8px; color: var(--muted); }}
    a.metric:hover {{ border-color: var(--blue); text-decoration: none; box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08); }}
    .pill {{ display: inline-block; padding: 2px 7px; border-radius: 999px; background: #eef1f4; }}
    .chance-details {{ margin-top: 6px; color: var(--muted); }}
    .chance-details summary {{ cursor: pointer; color: var(--blue); font-size: 0.9rem; }}
    .chance-details dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 4px 10px; margin: 8px 0 0; }}
    .chance-details dt {{ color: #344054; font-weight: 650; }}
    .chance-details dd {{ margin: 0; }}
    .chance-formula {{ display: block; margin-top: 8px; color: #344054; }}
    .danger {{ color: #b42318; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background: #eef1f4; padding: 2px 4px; border-radius: 4px; }}
    .home-shell {{ max-width: 1120px; }}
    .home-hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(360px, 0.95fr);
      gap: 28px;
      align-items: center;
      min-height: min(640px, calc(100dvh - 112px));
      padding-block: 34px 22px;
    }}
    .hero-copy h2 {{ font-size: clamp(2rem, 1.45rem + 2vw, 3.35rem); line-height: 1.05; letter-spacing: 0; margin: 0 0 18px; text-wrap: balance; }}
    .hero-copy p {{ max-width: 680px; margin: 0 0 20px; color: #475467; font-size: 1.08rem; }}
    .hero-points {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 24px; }}
    .hero-points span {{ border: 1px solid var(--border); background: rgba(255, 255, 255, 0.72); border-radius: 999px; padding: 7px 11px; color: #344054; font-size: 0.92rem; }}
    .start-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; box-shadow: var(--shadow); padding: 18px; }}
    .start-card form {{ display: grid; grid-template-columns: 1fr; gap: 12px; margin: 0; }}
    .start-card input, .start-card button {{ width: 100%; min-height: 46px; }}
    .start-card .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .start-card button.secondary {{ background: var(--green); border-color: var(--green); }}
    .form-section {{ padding: 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-soft); }}
    .form-section + .form-section {{ margin-top: 12px; }}
    .form-section p {{ margin: -4px 0 12px; color: var(--muted); font-size: 0.92rem; }}
    .landing-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 8px 0 44px; }}
    .landing-item {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
    .landing-item b {{ display: block; margin-bottom: 6px; }}
    .landing-item p {{ margin: 0; color: var(--muted); }}
    .dashboard-hero {{ display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr); gap: 18px; align-items: stretch; margin-bottom: 18px; }}
    .dashboard-intro {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }}
    .dashboard-intro h2 {{ font-size: 1.65rem; line-height: 1.15; margin: 0 0 12px; text-wrap: balance; }}
    .dashboard-intro p {{ margin: 0; color: #475467; max-width: 780px; }}
    .dashboard-side {{ background: #111827; color: #fff; border-radius: 8px; padding: 18px; }}
    .dashboard-side b {{ display: block; font-size: 1.25rem; margin-bottom: 8px; }}
    .dashboard-side p {{ margin: 0; color: #d0d5dd; }}
    .section-title {{ display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 10px; margin-top: 22px; }}
    .section-title h2 {{ margin: 0; }}
    .section-title p {{ margin: 0; color: var(--muted); }}
    .explain-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .explain-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
    .explain-card b {{ display: block; margin-bottom: 8px; }}
    .explain-card p {{ margin: 0; color: var(--muted); }}
    .note {{ background: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px; padding: 14px 16px; color: #7c2d12; margin-bottom: 18px; }}
    .search-panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 22px; }}
    .search-panel form {{ margin-bottom: 0; }}
    @media (max-width: 860px) {{
      header {{ padding-inline: 16px; }}
      main {{ padding-inline: 16px; }}
      .home-hero {{ grid-template-columns: 1fr; min-height: auto; padding-block-start: 24px; }}
      .start-card {{ order: -1; }}
      .hero-copy h2 {{ font-size: 2.15rem; }}
      .landing-grid {{ grid-template-columns: 1fr; }}
      .dashboard-hero {{ grid-template-columns: 1fr; }}
      .explain-grid {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 520px) {{
      nav {{ display: none; }}
      .start-card .form-row {{ grid-template-columns: 1fr; }}
      .hero-copy h2 {{ font-size: 1.85rem; }}
      .explain-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>{body}</body>
</html>"""


def nav_html(user: sqlite3.Row | None = None) -> str:
    if not user:
        return '<nav><a href="#start">старт</a><a href="#about">о проекте</a></nav>'
    links = ['<a href="/">статус</a>']
    if user["role"] in {"admin", "manager"}:
        links += [
            '<a href="/universities">вузы</a>',
            '<a href="/competitions">конкурсы</a>',
            '<a href="/applicants">абитуриенты</a>',
        ]
    if user["role"] == "admin":
        links.append('<a href="/admin/users">пользователи</a>')
    links.append(f'<span class="muted">{esc(user["username"])} · {role_title(user["role"])}</span>')
    links.append('<a href="/logout">выйти</a>')
    return "<nav>" + "".join(links) + "</nav>"


def header(title: str, user: sqlite3.Row | None = None) -> str:
    return f"<header><h1>{esc(title)}</h1>{nav_html(user)}</header>"


def metric(label: str, value, href: str | None = None, desc: str | None = None) -> str:
    body = f'<span class="muted">{esc(label)}</span><b>{esc(value)}</b>'
    if desc:
        body += f'<small>{esc(desc)}</small>'
    if href:
        return f'<a class="metric" href="{esc(href)}">{body}</a>'
    return f'<div class="metric">{body}</div>'


def chance_html(result: ChanceResult, seats=None) -> str:
    percent = "н/д" if result.chance_percent is None else f"{result.chance_percent}%"
    seats_text = "н/д" if not seats else str(seats)
    percent_formula = (
        "н/д: количество мест не указано или равно нулю"
        if result.chance_percent is None or not seats
        else f"({seats} - {result.effective_blockers}) / {seats} * 100 = {percent}"
    )
    formula = (
        f"1.0 * {result.above_consent_here} + "
        f"0.15 * {result.above_consent_elsewhere} + "
        f"0.55 * {result.above_no_consent} = {result.effective_blockers}"
    )
    return (
        f'<span class="pill">{esc(result.chance_label)} · {percent}</span>'
        f'<br><span class="muted">эффективно выше: {result.effective_blockers}, прогнозное место: {result.projected_rank}</span>'
        '<details class="chance-details">'
        '<summary>почему такой шанс</summary>'
        '<dl>'
        f'<dt>Согласие здесь</dt><dd>{result.above_consent_here} человек выше, вес 1.0</dd>'
        f'<dt>Согласие в другом конкурсе</dt><dd>{result.above_consent_elsewhere} человек выше, вес 0.15</dd>'
        f'<dt>Без найденного согласия</dt><dd>{result.above_no_consent} человек выше, вес 0.55</dd>'
        f'<dt>Мест</dt><dd>{esc(seats_text)}</dd>'
        f'<dt>Эффективно выше</dt><dd>{result.effective_blockers}</dd>'
        f'<dt>Прогнозное место</dt><dd>{result.projected_rank}</dd>'
        f'<dt>Итог</dt><dd>{percent}</dd>'
        '</dl>'
        f'<code class="chance-formula">эффективно выше: {esc(formula)}</code>'
        f'<code class="chance-formula">процент: {esc(percent_formula)}</code>'
        f'<p class="muted">{esc(result.explanation)}</p>'
        '</details>'
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

        if parsed.path == "/":
            user = self.current_user()
            if not user:
                return self.home_page()
            if user["role"] == "applicant" and user["applicant_id"]:
                return self.redirect("/applicant?" + urlencode({"id": user["applicant_id"]}))
            return self.search_page(parsed, user)

        user = self.require_user()
        if not user:
            return
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
            return self.redirect("/")
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

    def public_stats(self) -> sqlite3.Row:
        with db_connect() as conn:
            return conn.execute(
                """
                SELECT
                  (SELECT count(*) FROM competitions) AS competitions,
                  (SELECT count(*) FROM entries) AS entries,
                  (SELECT count(DISTINCT applicant_id) FROM entries) AS applicants
                """
            ).fetchone()

    def home_page(self, error=""):
        stats = self.public_stats()
        error_html = f"<p class='danger' role='alert'>{esc(error)}</p>" if error else ""
        self.send_html(
            HTTPStatus.OK,
            page(
                "Vvuzz Online",
                f"""{header("Vvuzz Online")}
<main class="home-shell">
<section class="home-hero" id="start">
  <div class="hero-copy">
    <h2>Проверьте свое место в конкурсных списках без лишних переходов</h2>
    <p>Vvuzz Online собирает публичные конкурсные списки, помогает найти участника по ID и показывает понятную оценку положения в выбранных конкурсах.</p>
    <div class="grid">
      {metric("Конкурсов", f"{stats['competitions']:,}")}
      {metric("Строк списков", f"{stats['entries']:,}")}
      {metric("Участников", f"{stats['applicants']:,}")}
    </div>
    <div class="hero-points" aria-label="Возможности">
      <span>публичный поиск по ID</span>
      <span>оценка шанса</span>
      <span>кабинет для команды</span>
    </div>
  </div>
  <div class="start-card" aria-label="Старт">
    <section class="form-section">
      <h3>Найти себя</h3>
      <p>Введите публичный ID участника, чтобы открыть свои конкурсы.</p>
      <form method="get" action="/public/applicant">
        <div>
          <label for="public-applicant-id">ID участника</label>
          <input id="public-applicant-id" name="id" placeholder="Например, 123456789" inputmode="numeric" enterkeyhint="search" required>
        </div>
        <button class="secondary" type="submit">Показать мои конкурсы</button>
      </form>
    </section>
    <section class="form-section">
      <h3>Войти в кабинет</h3>
      <p>Для администраторов и менеджеров проекта.</p>
      {error_html}
      <form method="post" action="/login">
        <div class="form-row">
          <div>
            <label for="username">Логин</label>
            <input id="username" name="username" autocomplete="username" required>
          </div>
          <div>
            <label for="current-password">Пароль</label>
            <input id="current-password" name="password" type="password" autocomplete="current-password" required>
          </div>
        </div>
        <button type="submit">Войти</button>
      </form>
    </section>
  </div>
</section>
<section class="landing-grid" id="about" aria-label="О проекте">
  <div class="landing-item">
    <b>Одна стартовая точка</b>
    <p>Главная страница работает и как вход, и как быстрый публичный поиск по себе.</p>
  </div>
  <div class="landing-item">
    <b>Без лишней навигации</b>
    <p>Публичному пользователю достаточно знать свой ID: остальные страницы открываются из результата.</p>
  </div>
  <div class="landing-item">
    <b>Прозрачная эвристика</b>
    <p>Оценка учитывает людей выше, согласия и найденные альтернативные конкурсы, но не гарантирует зачисление.</p>
  </div>
</section>
</main>""",
            ),
        )

    def login_page(self, error=""):
        return self.home_page(error)

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
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", "sid=; Max-Age=0; Path=/")
        self.end_headers()

    def public_index(self):
        return self.home_page()

    def status_stats(self) -> sqlite3.Row:
        with db_connect() as conn:
            return conn.execute(
                """
                SELECT
                  (SELECT count(*) FROM applicants) AS applicants,
                  (SELECT count(*) FROM universities) AS universities,
                  (SELECT count(*) FROM programs) AS programs,
                  (SELECT count(*) FROM competitions_core) AS competitions,
                  (SELECT count(*) FROM applications) AS applications,
                  (SELECT count(*) FROM ingest_batches) AS ingest_batches,
                  (SELECT max(received_at) FROM ingest_batches) AS last_ingest_received_at,
                  (SELECT max(fetched_at) FROM ingest_batches) AS last_ingest_fetched_at
                """
            ).fetchone()

    def status_metrics_html(self, stats: sqlite3.Row) -> str:
        return (
            metric("Абитуриентов", f"{stats['applicants']:,}", "/applicants", "Постраничный список публичных ID.")
            + metric("Вузов", stats["universities"], "/universities", "Сводка по вузам, программам и строкам.")
            + metric("Программ", stats["programs"], None, "Направления подготовки внутри вузов.")
            + metric("Конкурсов", f"{stats['competitions']:,}", "/competitions", "Фильтр по вузу, программе, квоте и форме.")
            + metric("Заявлений", f"{stats['applications']:,}", None, "Строки участников в конкурсных списках.")
            + metric("Завершенных циклов обновления данных", stats["ingest_batches"], None, "Примененные обновления текущего снимка.")
        )

    def search_page(self, parsed, user):
        params = parse_qs(parsed.query)
        applicant_id = params.get("applicant_id", [""])[0].strip()
        stats = self.status_stats()
        last_ingest = row_get(stats, "last_ingest_received_at", "") or "нет завершенных циклов"
        results = ""
        if applicant_id:
            return self.redirect("/applicant?" + urlencode({"id": applicant_id}))
        self.send_html(
            HTTPStatus.OK,
            page(
                "Поиск",
                f"""{header("Vvuzz Online", user)}
<main>
<section class="dashboard-hero">
  <div class="dashboard-intro">
    <h2>Рабочий снимок конкурсных списков для анализа поступления</h2>
    <p>Здесь собран снимок публичных конкурсных списков Госуслуг / «Поступление в вуз онлайн» за 2025 год. По одному ID видно все найденные конкурсы участника, его места, приоритеты, согласия и расчетный шанс по каждому направлению.</p>
  </div>
  <aside class="dashboard-side">
    <b>Актуальность снимка</b>
    <p>Завершенных циклов обновления данных: {esc(stats['ingest_batches'])}. Последнее обновление: {esc(last_ingest)}.</p>
  </aside>
</section>

<section class="section-title">
  <h2>Статус данных</h2>
  <p>Счетчики текущего снимка конкурсных списков.</p>
</section>
<div class="grid">
  {self.status_metrics_html(stats)}
</div>

<section class="search-panel">
  <h2>Поиск по ID</h2>
  <form method="get" action="/">
    <label for="root-applicant-id">ID участника</label>
    <input id="root-applicant-id" name="applicant_id" value="{esc(applicant_id)}" placeholder="Например, 4150096" inputmode="numeric" required>
    <button type="submit">Найти</button>
  </form>
</section>

<section class="section-title">
  <h2>Как это устроено</h2>
  <p>Коротко о данных и расчете, чтобы метрики не были черным ящиком.</p>
</section>
<section class="explain-grid">
  <article class="explain-card">
    <b>Источник</b>
    <p>Публичные конкурсные списки: вуз, программа, конкурс, количество мест, позиция участника, приоритет, согласие и текущий статус заявления.</p>
  </article>
  <article class="explain-card">
    <b>Поиск по ID</b>
    <p>Один ID сопоставляется со всеми конкурсами, где участник найден, поэтому в карточке видно несколько вузов, программ и квот сразу.</p>
  </article>
  <article class="explain-card">
    <b>Кто выше</b>
    <p>Для каждого конкурса считаются люди выше участника: с согласием здесь, с согласием в другом конкурсе и без найденного согласия.</p>
  </article>
  <article class="explain-card">
    <b>Цикл обновления</b>
    <p>Это завершенная загрузка нового снимка списков в сервис. Число показывает, сколько таких обновлений уже применено.</p>
  </article>
</section>

<section class="explain-grid">
  <article class="explain-card">
    <b>Формула шанса</b>
    <p>Эффективно выше = 1.0 × согласие здесь + 0.15 × согласие в другом конкурсе + 0.55 × без найденного согласия.</p>
  </article>
  <article class="explain-card">
    <b>Процент</b>
    <p>Шанс = (места − эффективно выше) / места × 100%. Итог ограничивается диапазоном от 0 до 100%.</p>
  </article>
  <article class="explain-card">
    <b>Как читать результат</b>
    <p>Высокий шанс означает, что расчетных блокирующих меньше, чем мест. Низкий шанс — что впереди слишком много участников с учетом согласий и весов.</p>
  </article>
  <article class="explain-card">
    <b>Ограничение</b>
    <p>Это эвристика по текущему снимку публичных данных, а не гарантия зачисления и не официальный прогноз вуза.</p>
  </article>
</section>

<p class="note">Главная продуктовая ценность появляется в короткое окно после публикации конкурсных списков: важно не просто место в списке, а понимание, сколько людей выше уже выбрали другие конкурсы или реально удерживают место здесь.</p>
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
            items = []
            for row in found:
                _, chance, metrics = competition_metrics(conn, row["competition_id"], applicant_id)
                if public:
                    q = urlencode({"id": row["competition_id"], "focus_applicant_id": applicant_id})
                    link = f"<a href='/public/competition?{q}'>детали</a>"
                else:
                    q = urlencode({"id": row["competition_id"], "focus_applicant_id": applicant_id})
                    link = f"<a href='/competition?{q}'>конкурс</a>"
                items.append((row, chance, link))
            items.sort(
                key=lambda item: (
                    item[1].chance_percent is not None if item[1] else False,
                    item[1].chance_percent if item[1] and item[1].chance_percent is not None else -1,
                    -(row_get(item[0], "ordinal", 0) or 0),
                ),
                reverse=True,
            )
            rendered_rows = []
            for row, chance, link in items:
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
                    f"<td>{chance_html(chance, row['seats']) if chance else 'н/д'}</td>"
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
<p class="muted">Конкурсы отсортированы по расчетному шансу: сначала самый высокий процент, строки с «н/д» ниже. Раскройте «почему такой шанс», чтобы увидеть веса и числа по конкретному конкурсу.</p>
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
        params = parse_qs(parsed.query)
        q = params.get("q", [""])[0].strip()
        after = params.get("after", [""])[0].strip()
        partial = params.get("partial", [""])[0] == "1"
        page_size = 100
        with db_connect() as conn:
            selected = conn.execute(
                """
                SELECT id, applicant_id
                FROM applicants
                WHERE (? = '' OR applicant_id LIKE ? ESCAPE '\\')
                  AND (? = '' OR applicant_id > ?)
                ORDER BY applicant_id
                LIMIT ?
                """,
                (q, like_prefix(q), after, after, page_size + 1),
            ).fetchall()
            has_more = len(selected) > page_size
            selected = selected[:page_size]
            rows = []
            if selected:
                placeholders = ",".join("?" for _ in selected)
                rows = conn.execute(
                    f"""
                    SELECT ap.applicant_id, count(a.id) AS applications,
                           COALESCE(sum(CASE WHEN a.has_consent = 1 THEN 1 ELSE 0 END), 0) AS consents
                    FROM applicants ap
                    LEFT JOIN applications a ON a.applicant_id = ap.id
                    WHERE ap.id IN ({placeholders})
                    GROUP BY ap.id, ap.applicant_id
                    ORDER BY ap.applicant_id
                    """,
                    [row["id"] for row in selected],
                ).fetchall()
        next_after = rows[-1]["applicant_id"] if rows else after
        body_rows = self.applicants_rows_html(rows)
        if partial:
            return self.send_json(
                HTTPStatus.OK,
                {
                    "html": body_rows,
                    "has_more": has_more,
                    "next_after": next_after,
                    "count": len(rows),
                },
            )
        next_qs = urlencode({"q": q, "after": next_after}) if has_more else ""
        next_link = (
            f'<p id="applicants-fallback" class="muted"><a href="/applicants?{next_qs}">Показать еще</a></p>'
            if has_more
            else '<p id="applicants-fallback" class="muted">Больше строк нет.</p>'
        )
        empty_row = "<tr><td colspan='3'>Ничего не найдено</td></tr>" if not rows else ""
        script_q = json.dumps(q, ensure_ascii=False)
        script_after = json.dumps(next_after, ensure_ascii=False)
        script_has_more = "true" if has_more else "false"
        self.send_html(HTTPStatus.OK, page("Абитуриенты", f"""{header("Абитуриенты", user)}<main>
<form method="get" action="/applicants">
  <label for="applicant-search">ID участника</label>
  <input id="applicant-search" name="q" value="{esc(q)}" placeholder="начните вводить ID" inputmode="numeric">
  <button>Найти</button>
</form>
<p class="muted">Загружается по 100 участников. Поиск работает по началу ID, чтобы не сканировать всю базу.</p>
<table><thead><tr><th>ID</th><th>Конкурсов</th><th>Согласий</th></tr></thead><tbody id="applicants-body">{body_rows or empty_row}</tbody></table>
<div id="applicants-sentinel" aria-hidden="true"></div>
<p id="applicants-status" class="muted" aria-live="polite"></p>
{next_link}
<script>
(() => {{
  const tbody = document.getElementById("applicants-body");
  const sentinel = document.getElementById("applicants-sentinel");
  const status = document.getElementById("applicants-status");
  const fallback = document.getElementById("applicants-fallback");
  let q = {script_q};
  let after = {script_after};
  let hasMore = {script_has_more};
  let loading = false;

  async function loadNext() {{
    if (!hasMore || loading) return;
    loading = true;
    status.textContent = "Загружаю следующую страницу...";
    const params = new URLSearchParams({{ partial: "1", q, after }});
    try {{
      const response = await fetch(`/applicants?${{params}}`, {{ headers: {{ "Accept": "application/json" }} }});
      if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
      const data = await response.json();
      if (data.html) tbody.insertAdjacentHTML("beforeend", data.html);
      after = data.next_after || after;
      hasMore = Boolean(data.has_more);
      status.textContent = hasMore ? "" : "Больше строк нет.";
      if (fallback) fallback.hidden = !hasMore;
    }} catch (error) {{
      status.textContent = "Не удалось загрузить следующую страницу. Используйте кнопку ниже.";
      if (fallback) fallback.hidden = false;
    }} finally {{
      loading = false;
    }}
  }}

  if ("IntersectionObserver" in window && sentinel) {{
    if (fallback) fallback.hidden = true;
    const observer = new IntersectionObserver((entries) => {{
      if (entries.some((entry) => entry.isIntersecting)) loadNext();
    }}, {{ rootMargin: "600px 0px" }});
    observer.observe(sentinel);
  }}
}})();
</script>
</main>"""))

    def applicants_rows_html(self, rows):
        return "".join(
            f"<tr><td><a href='/applicant?{urlencode({'id': row['applicant_id']})}'>{esc(row['applicant_id'])}</a></td><td>{row['applications']}</td><td>{row['consents']}</td></tr>"
            for row in rows
        )

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
{metric("Конкурсов", f"{stats['competitions']:,}")}{metric("Заявлений", f"{stats['applications']:,}")}{metric("Завершенных циклов обновления данных", stats['ingest_batches'])}
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
        competition_ids = []
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
                    applications = comp.get("applications") or []
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
                        len(applications),
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
                    competition_ids.append(competition_id)
                    if comp.get("replace_applications"):
                        conn.execute("DELETE FROM applications WHERE competition_id = ?", (competition_id,))
                    for app in applications:
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
            if not errors:
                from vvuzz_public.ingest import refresh_legacy_campaigns

                refresh_legacy_campaigns(
                    conn,
                    competition_ids=competition_ids,
                    payload=payload,
                )
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
