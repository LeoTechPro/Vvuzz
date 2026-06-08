#!/usr/bin/env python
import argparse
import sqlite3
import time
from pathlib import Path


DEFAULT_SOURCE = Path(__file__).resolve().parent / "vuzonline_2025_full.sqlite"
DEFAULT_TARGET = Path(__file__).resolve().parent / "vuzonline_2025_norm.sqlite"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE applicants (
    id INTEGER PRIMARY KEY,
    applicant_id TEXT NOT NULL UNIQUE
);

CREATE TABLE universities (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE
);

CREATE TABLE programs (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE
);

CREATE TABLE competitions_core (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    program_id INTEGER REFERENCES programs(id),
    education_level TEXT,
    city TEXT,
    university_id INTEGER REFERENCES universities(id),
    study_form TEXT,
    quota_type TEXT,
    seats INTEGER,
    path_json TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE TABLE applications (
    id INTEGER PRIMARY KEY,
    competition_id INTEGER NOT NULL REFERENCES competitions_core(id) ON DELETE CASCADE,
    applicant_id INTEGER NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
    ordinal INTEGER,
    competition_priority INTEGER,
    consent_text TEXT,
    has_consent INTEGER NOT NULL DEFAULT 0,
    score_total REAL,
    exam_scores_text TEXT,
    individual_score REAL,
    status TEXT,
    selected_at_moscow TEXT
);

CREATE TABLE users (
    username TEXT PRIMARY KEY,
    password_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE import_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIEW competitions AS
SELECT
    c.id,
    c.source_path,
    c.file_name,
    p.name AS program,
    c.education_level,
    c.city,
    u.name AS university,
    c.study_form,
    c.quota_type,
    c.seats,
    c.path_json,
    c.row_count,
    c.imported_at
FROM competitions_core c
LEFT JOIN programs p ON p.id = c.program_id
LEFT JOIN universities u ON u.id = c.university_id;

CREATE VIEW entries AS
SELECT
    a.id,
    a.competition_id,
    a.ordinal,
    ap.applicant_id,
    a.competition_priority,
    a.consent_text,
    a.has_consent,
    a.score_total,
    a.exam_scores_text,
    a.individual_score,
    a.status,
    a.selected_at_moscow,
    json_object(
        'Порядковый номер', a.ordinal,
        'ID участника', ap.applicant_id,
        'Приоритет конкурса', a.competition_priority,
        'Подано согласие', a.consent_text,
        'Сумма баллов', a.score_total,
        'Баллы за ВИ', a.exam_scores_text,
        'Баллы за ИД', a.individual_score,
        'Статус', a.status,
        'Дата выбора конкурсной группы по Москве', a.selected_at_moscow
    ) AS raw_json
FROM applications a
JOIN applicants ap ON ap.id = a.applicant_id;

CREATE INDEX idx_applicants_applicant_id ON applicants(applicant_id);
CREATE INDEX idx_applications_applicant_id ON applications(applicant_id);
CREATE INDEX idx_applications_competition_ordinal ON applications(competition_id, ordinal);
CREATE INDEX idx_applications_has_consent_applicant ON applications(has_consent, applicant_id);
CREATE INDEX idx_competitions_core_university ON competitions_core(university_id);
CREATE INDEX idx_competitions_core_program ON competitions_core(program_id);
CREATE INDEX idx_competitions_core_quota ON competitions_core(quota_type);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build normalized Vvuzz SQLite database.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"source not found: {args.source}")
    if args.target.exists() and not args.force:
        raise SystemExit(f"target exists, pass --force to overwrite: {args.target}")
    if args.target.exists():
        args.target.unlink()

    started = time.time()
    target = connect(args.target)
    target.execute("PRAGMA journal_mode = WAL")
    target.execute("PRAGMA synchronous = NORMAL")
    target.execute("PRAGMA temp_store = MEMORY")
    target.executescript(SCHEMA)
    target.execute("ATTACH DATABASE ? AS src", (str(args.source),))

    log("copy users/import_meta")
    target.execute("INSERT INTO users SELECT username, password_text, created_at FROM src.users")
    target.execute("INSERT INTO import_meta SELECT key, value FROM src.import_meta")
    target.commit()

    log("build applicants")
    target.execute(
        """
        INSERT INTO applicants(applicant_id)
        SELECT DISTINCT applicant_id
        FROM src.entries
        WHERE applicant_id IS NOT NULL AND applicant_id <> ''
        """
    )
    target.commit()

    log("build universities")
    target.execute(
        """
        INSERT INTO universities(name)
        SELECT DISTINCT university
        FROM src.competitions
        WHERE university IS NOT NULL AND university <> ''
        """
    )
    target.commit()

    log("build programs")
    target.execute(
        """
        INSERT INTO programs(name)
        SELECT DISTINCT program
        FROM src.competitions
        WHERE program IS NOT NULL AND program <> ''
        """
    )
    target.commit()

    log("build competitions_core")
    target.execute(
        """
        INSERT INTO competitions_core(
            id, source_path, file_name, program_id, education_level, city,
            university_id, study_form, quota_type, seats, path_json, row_count, imported_at
        )
        SELECT
            c.id, c.source_path, c.file_name, p.id, c.education_level, c.city,
            u.id, c.study_form, c.quota_type, c.seats, c.path_json, c.row_count, c.imported_at
        FROM src.competitions c
        LEFT JOIN programs p ON p.name IS c.program
        LEFT JOIN universities u ON u.name IS c.university
        """
    )
    target.commit()

    log("build applications")
    target.execute(
        """
        INSERT INTO applications(
            id, competition_id, applicant_id, ordinal, competition_priority,
            consent_text, has_consent, score_total, exam_scores_text,
            individual_score, status, selected_at_moscow
        )
        SELECT
            e.id, e.competition_id, ap.id, e.ordinal, e.competition_priority,
            e.consent_text, e.has_consent, e.score_total, e.exam_scores_text,
            e.individual_score, e.status, e.selected_at_moscow
        FROM src.entries e
        JOIN applicants ap ON ap.applicant_id = e.applicant_id
        """
    )
    target.commit()

    stats = {
        "applicants": target.execute("SELECT count(*) FROM applicants").fetchone()[0],
        "universities": target.execute("SELECT count(*) FROM universities").fetchone()[0],
        "programs": target.execute("SELECT count(*) FROM programs").fetchone()[0],
        "competitions": target.execute("SELECT count(*) FROM competitions_core").fetchone()[0],
        "applications": target.execute("SELECT count(*) FROM applications").fetchone()[0],
    }
    log(f"stats {stats}")
    log(f"elapsed_sec {time.time() - started:.1f}")
    target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
