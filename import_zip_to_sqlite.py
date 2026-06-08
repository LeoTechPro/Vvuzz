#!/usr/bin/env python
import argparse
import datetime as dt
import io
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

import openpyxl


DEFAULT_ZIP = Path(__file__).resolve().parent / ".." / "2025-07-24 13-40-58.zip"
DEFAULT_DB = Path(__file__).resolve().parent / "vuzonline_2025.sqlite"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competitions (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    program TEXT,
    education_level TEXT,
    city TEXT,
    university TEXT,
    study_form TEXT,
    quota_type TEXT,
    seats INTEGER,
    path_json TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
    ordinal INTEGER,
    applicant_id TEXT,
    competition_priority INTEGER,
    consent_text TEXT,
    has_consent INTEGER NOT NULL DEFAULT 0,
    score_total REAL,
    exam_scores_text TEXT,
    individual_score REAL,
    status TEXT,
    selected_at_moscow TEXT,
    raw_json TEXT NOT NULL
);

"""


INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_entries_applicant_id ON entries(applicant_id);
CREATE INDEX IF NOT EXISTS idx_entries_competition_ordinal ON entries(competition_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_entries_has_consent ON entries(has_consent, applicant_id);
CREATE INDEX IF NOT EXISTS idx_competitions_university ON competitions(university);
CREATE INDEX IF NOT EXISTS idx_competitions_program ON competitions(program);
CREATE INDEX IF NOT EXISTS idx_competitions_quota ON competitions(quota_type);
"""


HEADER_MAP = {
    "Порядковый номер": "ordinal",
    "ID участника": "applicant_id",
    "Приоритет конкурса": "competition_priority",
    "Подано согласие": "consent_text",
    "Сумма баллов": "score_total",
    "Баллы за ВИ": "exam_scores_text",
    "Баллы за ИД": "individual_score",
    "Статус": "status",
    "Дата выбора конкурсной группы по Москве": "selected_at_moscow",
}


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def as_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def as_int(value):
    text = as_text(value)
    if text is None or text == "-":
        return None
    try:
        return int(float(text.replace(",", ".")))
    except ValueError:
        return None


def as_float(value):
    text = as_text(value)
    if text is None or text == "-":
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def has_consent(value) -> int:
    text = as_text(value)
    if not text or text == "-":
        return 0
    return 1


def decode_zip_name(name: str) -> str:
    # Python usually decodes UTF-8 names correctly. Keep this hook isolated in
    # case an old archive marks names as CP437 while storing Cyrillic bytes.
    return name


def parse_competition_path(source_path: str) -> dict:
    path = decode_zip_name(source_path)
    parts = path.split("/")
    file_name = parts[-1]
    stem = file_name.removesuffix(".xlsx")
    quota_type = stem.split(" [", 1)[0]
    seats = None
    match = re.search(r"\[(\d+)\]", stem)
    if match:
        seats = int(match.group(1))

    payload = {
        "source_path": path,
        "file_name": file_name,
        "program": None,
        "education_level": None,
        "city": None,
        "university": None,
        "study_form": None,
        "quota_type": quota_type,
        "seats": seats,
        "path_json": json.dumps(parts, ensure_ascii=False),
    }
    if len(parts) >= 8:
        payload.update(
            {
                "program": parts[2],
                "education_level": parts[3],
                "city": parts[4],
                "university": parts[5],
                "study_form": parts[6],
            }
        )
    return payload


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO users(username, password_text, created_at) VALUES (?, ?, ?)",
        ("admin", "admin", now_iso()),
    )
    conn.commit()


def ensure_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(INDEX_SQL)
    conn.commit()


def already_imported(conn: sqlite3.Connection, source_path: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM competitions WHERE source_path = ? LIMIT 1", (source_path,)
    ).fetchone()
    return row is not None


def import_workbook(conn: sqlite3.Connection, archive: zipfile.ZipFile, source_path: str) -> int:
    meta = parse_competition_path(source_path)
    workbook = openpyxl.load_workbook(
        io.BytesIO(archive.read(source_path)),
        read_only=True,
        data_only=True,
    )
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        header_row = []

    headers = [as_text(cell) or f"column_{idx + 1}" for idx, cell in enumerate(header_row)]
    mapped = [HEADER_MAP.get(header, None) for header in headers]

    cur = conn.execute(
        """
        INSERT INTO competitions(
            source_path, file_name, program, education_level, city, university,
            study_form, quota_type, seats, path_json, row_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            meta["source_path"],
            meta["file_name"],
            meta["program"],
            meta["education_level"],
            meta["city"],
            meta["university"],
            meta["study_form"],
            meta["quota_type"],
            meta["seats"],
            meta["path_json"],
            now_iso(),
        ),
    )
    competition_id = cur.lastrowid

    batch = []
    row_count = 0
    for row in rows:
        raw = {
            headers[idx]: row[idx] if idx < len(row) else None
            for idx in range(len(headers))
        }
        values = {field: None for field in HEADER_MAP.values()}
        for idx, field in enumerate(mapped):
            if field and idx < len(row):
                values[field] = row[idx]

        applicant_id = as_text(values["applicant_id"])
        if not applicant_id:
            continue

        batch.append(
            (
                competition_id,
                as_int(values["ordinal"]),
                applicant_id,
                as_int(values["competition_priority"]),
                as_text(values["consent_text"]),
                has_consent(values["consent_text"]),
                as_float(values["score_total"]),
                as_text(values["exam_scores_text"]),
                as_float(values["individual_score"]),
                as_text(values["status"]),
                as_text(values["selected_at_moscow"]),
                json.dumps(raw, ensure_ascii=False, default=str),
            )
        )
        row_count += 1

        if len(batch) >= 1000:
            conn.executemany(
                """
                INSERT INTO entries(
                    competition_id, ordinal, applicant_id, competition_priority,
                    consent_text, has_consent, score_total, exam_scores_text,
                    individual_score, status, selected_at_moscow, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            batch.clear()

    if batch:
        conn.executemany(
            """
            INSERT INTO entries(
                competition_id, ordinal, applicant_id, competition_priority,
                consent_text, has_consent, score_total, exam_scores_text,
                individual_score, status, selected_at_moscow, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )

    conn.execute(
        "UPDATE competitions SET row_count = ? WHERE id = ?",
        (row_count, competition_id),
    )
    workbook.close()
    return row_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Import VUZ online XLSX zip into SQLite.")
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=0, help="Debug limit for number of xlsx files.")
    parser.add_argument("--commit-every", type=int, default=200)
    args = parser.parse_args()

    if not args.zip.exists():
        print(f"Zip not found: {args.zip}", file=sys.stderr)
        return 2

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    init_db(conn)

    with zipfile.ZipFile(args.zip) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".xlsx")]
        if args.limit:
            names = names[: args.limit]

        total_rows = 0
        imported_files = 0
        skipped_files = 0
        started = now_iso()
        conn.execute(
            "INSERT OR REPLACE INTO import_meta(key, value) VALUES (?, ?)",
            ("last_import_started_at", started),
        )

        for idx, name in enumerate(names, 1):
            if already_imported(conn, name):
                skipped_files += 1
                continue
            try:
                total_rows += import_workbook(conn, archive, name)
                imported_files += 1
            except Exception as exc:
                conn.rollback()
                print(f"ERROR importing {name}: {exc}", file=sys.stderr)
                raise

            if imported_files % args.commit_every == 0:
                conn.commit()
                print(
                    f"imported_files={imported_files} skipped={skipped_files} "
                    f"seen={idx}/{len(names)} rows={total_rows}",
                    flush=True,
                )

        conn.commit()
        conn.execute(
            "INSERT OR REPLACE INTO import_meta(key, value) VALUES (?, ?)",
            ("last_import_finished_at", now_iso()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO import_meta(key, value) VALUES (?, ?)",
            ("source_zip", str(args.zip)),
        )
        conn.commit()

    print("building indexes...", flush=True)
    ensure_indexes(conn)

    stats = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM competitions),
            (SELECT count(*) FROM entries),
            (SELECT count(*) FROM users)
        """
    ).fetchone()
    print(f"done competitions={stats[0]} entries={stats[1]} users={stats[2]}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
