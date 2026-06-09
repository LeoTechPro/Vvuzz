#!/usr/bin/env python
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from import_zip_to_sqlite import DEFAULT_ZIP, init_db, import_workbook


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = PROJECT_DIR / "vuzonline_2025_full.sqlite"
DEFAULT_SANDBOX_DB = PROJECT_DIR / "parser_sandbox.sqlite"
RAW_DIR = PROJECT_DIR / "raw" / "gosuslugi"
DEFAULT_RPM = 55
DEFAULT_BACKOFF_SECONDS = 3 * 60 * 60


PARSER_SCHEMA = """
CREATE TABLE IF NOT EXISTS parser_runs (
    id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    config_json TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS parser_queue (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    url TEXT,
    source_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 100,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parser_fetch_log (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES parser_runs(id),
    source_key TEXT,
    url TEXT,
    source_path TEXT,
    status_code INTEGER,
    content_type TEXT,
    bytes INTEGER,
    sha256 TEXT,
    raw_file TEXT,
    error TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parser_raw_responses (
    id INTEGER PRIMARY KEY,
    fetch_log_id INTEGER NOT NULL REFERENCES parser_fetch_log(id) ON DELETE CASCADE,
    payload BLOB NOT NULL
);
"""


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_parser_db(conn: sqlite3.Connection) -> None:
    init_db(conn)
    conn.executescript(PARSER_SCHEMA)
    conn.commit()


def start_run(conn: sqlite3.Connection, mode: str, source: str, config: dict) -> int:
    cur = conn.execute(
        """
        INSERT INTO parser_runs(mode, source, status, started_at, config_json)
        VALUES (?, ?, 'running', ?, ?)
        """,
        (mode, source, now_iso(), json.dumps(config, ensure_ascii=False, indent=2)),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, notes: str = "") -> None:
    conn.execute(
        "UPDATE parser_runs SET status = ?, finished_at = ?, notes = ? WHERE id = ?",
        (status, now_iso(), notes, run_id),
    )
    conn.commit()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def is_block_payload(payload: bytes, content_type: str | None = None) -> bool:
    text = payload[:20000].decode("utf-8", "ignore").lower()
    return any(marker in text for marker in ("captcha", "капча", "too many requests", "доступ ограничен", "blocked"))


class RateLimiter:
    def __init__(self, rpm: int):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.interval = 60.0 / rpm
        self.last_call = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delay = self.interval - (now - self.last_call)
        if delay > 0:
            time.sleep(delay)
        self.last_call = time.monotonic()


def seed_fixture_queue(conn: sqlite3.Connection, zip_path: Path, limit: int = 0) -> int:
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".xlsx")]
    if limit:
        names = names[:limit]
    now = now_iso()
    rows = [
        (f"fixture:{name}", "fixture_xlsx", name, "pending", 100, now)
        for name in names
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO parser_queue(
            source_key, source_type, source_path, status, priority, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes


def fixture_import(conn: sqlite3.Connection, zip_path: Path, limit: int, run_id: int) -> tuple[int, int, int]:
    with zipfile.ZipFile(zip_path) as archive:
        rows = conn.execute(
            """
            SELECT id, source_key, source_path
            FROM parser_queue
            WHERE source_type = 'fixture_xlsx'
              AND status IN ('pending', 'error')
            ORDER BY priority, id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        imported = 0
        skipped = 0
        errors = 0
        for row in rows:
            try:
                if conn.execute(
                    "SELECT 1 FROM competitions WHERE source_path = ? LIMIT 1",
                    (row["source_path"],),
                ).fetchone():
                    skipped += 1
                else:
                    import_workbook(conn, archive, row["source_path"])
                    imported += 1
                conn.execute(
                    """
                    UPDATE parser_queue
                    SET status = 'done', attempts = attempts + 1, last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso(), row["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO parser_fetch_log(run_id, source_key, source_path, status_code, fetched_at)
                    VALUES (?, ?, ?, 200, ?)
                    """,
                    (run_id, row["source_key"], row["source_path"], now_iso()),
                )
            except Exception as exc:
                errors += 1
                conn.execute(
                    """
                    UPDATE parser_queue
                    SET status = 'error', attempts = attempts + 1, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (str(exc), now_iso(), row["id"]),
                )
            if (imported + skipped + errors) % 100 == 0:
                conn.commit()
                print(f"fixture processed={imported + skipped + errors} imported={imported} skipped={skipped} errors={errors}", flush=True)
        conn.commit()
        return imported, skipped, errors


def http_probe(
    conn: sqlite3.Connection,
    run_id: int,
    url: str,
    rpm: int,
    timeout: int,
    dry_run: bool,
    save_raw_file: bool,
) -> None:
    limiter = RateLimiter(rpm)
    source_key = f"http:{url}"
    if dry_run:
        print(f"dry-run: would GET {url} with rpm={rpm}")
        conn.execute(
            """
            INSERT INTO parser_fetch_log(run_id, source_key, url, error, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, source_key, url, "dry-run only", now_iso()),
        )
        conn.commit()
        return

    limiter.wait()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VuzOnlineLocalParser/0.1 (+local research; rate-limited)",
            "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    fetched_at = now_iso()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            digest = sha256_bytes(payload)
            raw_file = None
            if save_raw_file:
                RAW_DIR.mkdir(parents=True, exist_ok=True)
                raw_file = RAW_DIR / f"{digest[:16]}.bin"
                raw_file.write_bytes(payload)
            cur = conn.execute(
                """
                INSERT INTO parser_fetch_log(
                    run_id, source_key, url, status_code, content_type, bytes,
                    sha256, raw_file, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source_key,
                    url,
                    response.status,
                    response.headers.get("Content-Type"),
                    len(payload),
                    digest,
                    str(raw_file) if raw_file else None,
                    fetched_at,
                ),
            )
            conn.execute(
                "INSERT INTO parser_raw_responses(fetch_log_id, payload) VALUES (?, ?)",
                (cur.lastrowid, payload),
            )
            conn.commit()
            location = f", raw_file={raw_file}" if raw_file else ", raw=sqlite"
            print(f"GET {url} -> {response.status}, bytes={len(payload)}{location}")
    except urllib.error.HTTPError as exc:
        conn.execute(
            """
            INSERT INTO parser_fetch_log(run_id, source_key, url, status_code, error, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, source_key, url, exc.code, str(exc), fetched_at),
        )
        conn.commit()
        print(f"GET {url} -> HTTP {exc.code}: {exc}", file=sys.stderr)
        if exc.code in {403, 429}:
            print("received block/rate-limit signal; stop this run and back off", file=sys.stderr)
    except Exception as exc:
        conn.execute(
            """
            INSERT INTO parser_fetch_log(run_id, source_key, url, error, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, source_key, url, str(exc), fetched_at),
        )
        conn.commit()
        raise


def command_init(args) -> int:
    with connect(args.db) as conn:
        init_parser_db(conn)
    print(f"initialized parser schema in {args.db}")
    return 0


def command_seed_fixtures(args) -> int:
    with connect(args.db) as conn:
        init_parser_db(conn)
        count = seed_fixture_queue(conn, args.zip, args.limit)
    print(f"seeded fixture queue changes={count} db={args.db}")
    return 0


def command_fixture_run(args) -> int:
    with connect(args.db) as conn:
        init_parser_db(conn)
        run_id = start_run(
            conn,
            "fixture-run",
            str(args.zip),
            {"limit": args.limit, "db": str(args.db), "zip": str(args.zip)},
        )
        try:
            imported, skipped, errors = fixture_import(conn, args.zip, args.limit, run_id)
            status = "ok" if errors == 0 else "partial"
            finish_run(conn, run_id, status, f"imported={imported} skipped={skipped} errors={errors}")
            print(f"fixture-run run_id={run_id} imported={imported} skipped={skipped} errors={errors}")
            return 0 if errors == 0 else 1
        except Exception as exc:
            finish_run(conn, run_id, "error", str(exc))
            raise


def command_http_probe(args) -> int:
    with connect(args.db) as conn:
        init_parser_db(conn)
        run_id = start_run(
            conn,
            "http-probe",
            args.url,
            {
                "url": args.url,
                "rpm": args.rpm,
                "timeout": args.timeout,
                "dry_run": args.dry_run,
                "save_raw_file": args.save_raw_file,
            },
        )
        try:
            http_probe(
                conn,
                run_id,
                args.url,
                args.rpm,
                args.timeout,
                args.dry_run,
                args.save_raw_file,
            )
            finish_run(conn, run_id, "ok")
            return 0
        except Exception as exc:
            finish_run(conn, run_id, "error", str(exc))
            raise


class RawArchive:
    def __init__(self, root: Path, run_id: str):
        self.root = root / run_id
        self.payloads = self.root / "payloads"
        self.root.mkdir(parents=True, exist_ok=True)
        self.payloads.mkdir(parents=True, exist_ok=True)
        self.manifest = self.root / "manifest.jsonl"
        self.errors = self.root / "errors.jsonl"

    def write_payload(self, record: dict, payload: bytes) -> Path:
        digest = record["sha256"]
        target_dir = self.payloads / digest[:2]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest}.bin.gz"
        if not target.exists():
            with gzip.open(target, "wb") as handle:
                handle.write(payload)
        record = dict(record)
        record["payload_path"] = str(target)
        with self.manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return target

    def write_error(self, record: dict) -> None:
        with self.errors.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_urls(args) -> list[str]:
    urls = list(args.url or [])
    if args.url_file:
        urls.extend(
            line.strip()
            for line in args.url_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    if not urls:
        raise SystemExit("live-crawl needs at least one --url or --url-file")
    return urls


def target_kind(target: str) -> tuple[str, str]:
    if target.startswith("sqlite://"):
        return "sqlite", target.removeprefix("sqlite://")
    if target.startswith("ingest+"):
        return "ingest", target.removeprefix("ingest+")
    raise SystemExit(f"unsupported target: {target}")


def extract_competitions(payload: bytes, content_type: str | None) -> list[dict]:
    if content_type and "json" not in content_type.lower():
        return []
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return []
    if isinstance(data, dict) and isinstance(data.get("competitions"), list):
        return data["competitions"]
    if isinstance(data, list):
        return data
    return []


def ensure_norm_target(conn: sqlite3.Connection) -> None:
    from normalize_sqlite import SCHEMA

    conn.executescript(SCHEMA)
    conn.commit()


def norm_name_id(conn: sqlite3.Connection, table: str, value: str | None) -> int | None:
    if not value:
        return None
    conn.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES (?)", (value,))
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (value,)).fetchone()
    return row[0] if row else None


def write_norm_sqlite(db_path: Path, competitions: list[dict], fetched_at: str) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_norm_target(conn)
    comp_count = 0
    app_count = 0
    for comp in competitions:
        source_path = comp.get("source_path")
        if not source_path:
            continue
        program_id = norm_name_id(conn, "programs", comp.get("program"))
        university_id = norm_name_id(conn, "universities", comp.get("university"))
        existing = conn.execute("SELECT id FROM competitions_core WHERE source_path = ?", (source_path,)).fetchone()
        path_json = json.dumps(comp.get("path") or [], ensure_ascii=False)
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
            path_json,
            len(comp.get("applications") or []),
            fetched_at,
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
            competition_id = conn.execute(
                """
                INSERT INTO competitions_core(
                    source_path, file_name, program_id, education_level, city, university_id,
                    study_form, quota_type, seats, path_json, row_count, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            ).lastrowid
            comp_count += 1
        for app in comp.get("applications") or []:
            external_id = str(app.get("applicant_id") or "").strip()
            if not external_id:
                continue
            conn.execute("INSERT OR IGNORE INTO applicants(applicant_id) VALUES (?)", (external_id,))
            applicant_id = conn.execute("SELECT id FROM applicants WHERE applicant_id = ?", (external_id,)).fetchone()["id"]
            existing_app = conn.execute(
                "SELECT id FROM applications WHERE competition_id = ? AND applicant_id = ? LIMIT 1",
                (competition_id, applicant_id),
            ).fetchone()
            values_app = (
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
            if existing_app:
                conn.execute(
                    """
                    UPDATE applications
                    SET ordinal=?, competition_priority=?, consent_text=?, has_consent=?,
                        score_total=?, exam_scores_text=?, individual_score=?, status=?, selected_at_moscow=?
                    WHERE id=?
                    """,
                    values_app + (existing_app["id"],),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO applications(
                        competition_id, applicant_id, ordinal, competition_priority, consent_text,
                        has_consent, score_total, exam_scores_text, individual_score, status, selected_at_moscow
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (competition_id, applicant_id) + values_app,
                )
                app_count += 1
    conn.commit()
    conn.close()
    return comp_count, app_count


def post_ingest(target_url: str, token: str, batch: dict, timeout: int) -> dict:
    payload = json.dumps(batch, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        target_url,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Vvuzz-Ingest-Token": token,
            "User-Agent": "VvuzzLiveParser/0.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def command_live_crawl(args) -> int:
    urls = read_urls(args)
    run_id = args.run_id or now_iso().replace(":", "-")
    raw_archive = RawArchive(args.raw_dir, run_id) if args.mode in {"raw-only", "both"} else None
    targets = [target_kind(target) for target in (args.target or [])]
    if args.mode in {"norm-only", "both"} and not targets:
        raise SystemExit(f"{args.mode} needs at least one --target")
    if args.mode == "raw-only" and targets:
        raise SystemExit("raw-only must not receive --target")
    limiter = RateLimiter(args.rpm)
    stopped = False
    processed = 0
    for url in urls:
        limiter.wait()
        fetched_at = now_iso()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "VvuzzLiveParser/0.1 (+local research; rate-limited)",
                "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        try:
            if args.dry_run:
                print(f"dry-run: would GET {url} mode={args.mode} rpm={args.rpm}")
                continue
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                payload = response.read()
                digest = sha256_bytes(payload)
                record = {
                    "run_id": run_id,
                    "url": url,
                    "source_key": f"http:{url}",
                    "status_code": response.status,
                    "content_type": response.headers.get("Content-Type"),
                    "bytes": len(payload),
                    "sha256": digest,
                    "fetched_at": fetched_at,
                }
                if response.status in {403, 429} or is_block_payload(payload, record["content_type"]):
                    if raw_archive:
                        raw_archive.write_error({**record, "error": "block/rate-limit/captcha detected"})
                    print(f"stop: block signal from {url}; backoff={args.backoff_seconds}s", file=sys.stderr)
                    stopped = True
                    break
                if raw_archive:
                    raw_archive.write_payload(record, payload)
                competitions = extract_competitions(payload, record["content_type"])
                if args.mode in {"norm-only", "both"}:
                    batch = {
                        "run_id": run_id,
                        "source": "gosuslugi",
                        "source_key": record["source_key"],
                        "payload_sha256": digest,
                        "fetched_at": fetched_at,
                        "competitions": competitions,
                    }
                    for kind, target in targets:
                        if kind == "sqlite":
                            comp_count, app_count = write_norm_sqlite(Path(target), competitions, fetched_at)
                            print(f"sqlite target={target} competitions={comp_count} applications={app_count}")
                        else:
                            if not args.ingest_token:
                                raise SystemExit("ingest target needs --ingest-token or VVUZZ_INGEST_TOKEN")
                            result = post_ingest(target, args.ingest_token, batch, args.timeout)
                            print(f"ingest target={target} result={result}")
                processed += 1
                print(f"GET {url} -> {response.status} bytes={len(payload)} sha256={digest[:12]}")
        except urllib.error.HTTPError as exc:
            record = {"run_id": run_id, "url": url, "status_code": exc.code, "error": str(exc), "fetched_at": fetched_at}
            if raw_archive:
                raw_archive.write_error(record)
            print(f"GET {url} -> HTTP {exc.code}: {exc}", file=sys.stderr)
            if exc.code in {403, 429}:
                stopped = True
                break
        except Exception as exc:
            record = {"run_id": run_id, "url": url, "error": str(exc), "fetched_at": fetched_at}
            if raw_archive:
                raw_archive.write_error(record)
            raise
    if stopped:
        print(f"stopped processed={processed} suggested_backoff_seconds={args.backoff_seconds}")
        return 2
    print(f"live-crawl done processed={processed} run_id={run_id}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Safe parser scaffold for Gosuslugi VUZ online data.")
    parser.set_defaults(func=None)

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="Create parser metadata tables.")
    p_init.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_init.set_defaults(func=command_init)

    p_seed = sub.add_parser("seed-fixtures", help="Seed parser queue from local 2025 zip.")
    p_seed.add_argument("--db", type=Path, default=DEFAULT_SANDBOX_DB)
    p_seed.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    p_seed.add_argument("--limit", type=int, default=100)
    p_seed.set_defaults(func=command_seed_fixtures)

    p_fixture = sub.add_parser("fixture-run", help="Import queued fixture XLSX files into SQLite.")
    p_fixture.add_argument("--db", type=Path, default=DEFAULT_SANDBOX_DB)
    p_fixture.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    p_fixture.add_argument("--limit", type=int, default=20)
    p_fixture.set_defaults(func=command_fixture_run)

    p_probe = sub.add_parser("http-probe", help="One safe rate-limited HTTP GET probe.")
    p_probe.add_argument("url")
    p_probe.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_probe.add_argument("--rpm", type=int, default=DEFAULT_RPM)
    p_probe.add_argument("--timeout", type=int, default=20)
    p_probe.add_argument("--dry-run", action="store_true")
    p_probe.add_argument(
        "--save-raw-file",
        action="store_true",
        help="Debug only: also write raw HTTP response to raw/gosuslugi. Default stores raw payload only in SQLite.",
    )
    p_probe.set_defaults(func=command_http_probe)

    p_live = sub.add_parser("live-crawl", help="Rate-limited live crawl with raw/norm/both output modes.")
    p_live.add_argument("--mode", choices=["raw-only", "norm-only", "both"], required=True)
    p_live.add_argument("--url", action="append", help="URL to fetch. Can be repeated.")
    p_live.add_argument("--url-file", type=Path, help="Text file with one URL per line.")
    p_live.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    p_live.add_argument("--target", action="append", help="sqlite://path or ingest+https://host/api/ingest/batch. Can be repeated.")
    p_live.add_argument("--ingest-token", default=os.getenv("VVUZZ_INGEST_TOKEN", ""))
    p_live.add_argument("--run-id")
    p_live.add_argument("--rpm", type=int, default=DEFAULT_RPM)
    p_live.add_argument("--timeout", type=int, default=20)
    p_live.add_argument("--backoff-seconds", type=int, default=DEFAULT_BACKOFF_SECONDS)
    p_live.add_argument("--dry-run", action="store_true")
    p_live.set_defaults(func=command_live_crawl)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
