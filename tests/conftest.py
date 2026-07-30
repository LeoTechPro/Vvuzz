from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migrations.migrate import apply


LEGACY_SCHEMA = """
CREATE TABLE universities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE programs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE users (
    username TEXT PRIMARY KEY,
    password_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE applicants (
    id INTEGER PRIMARY KEY,
    applicant_id TEXT NOT NULL UNIQUE
);
CREATE INDEX idx_applicants_applicant_id ON applicants(applicant_id);
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
    competition_id INTEGER NOT NULL REFERENCES competitions_core(id),
    applicant_id INTEGER NOT NULL REFERENCES applicants(id),
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
CREATE INDEX idx_applications_applicant_id ON applications(applicant_id);
CREATE INDEX idx_applications_competition_ordinal ON applications(competition_id, ordinal);
CREATE INDEX idx_applications_has_consent_applicant ON applications(has_consent, applicant_id);
CREATE VIEW competitions AS
SELECT c.id, c.source_path, c.file_name, p.name AS program, c.education_level,
       c.city, u.name AS university, c.study_form, c.quota_type, c.seats,
       c.path_json, c.row_count, c.imported_at
FROM competitions_core c
LEFT JOIN programs p ON p.id = c.program_id
LEFT JOIN universities u ON u.id = c.university_id;
CREATE VIEW entries AS
SELECT a.id, a.competition_id, a.ordinal, ap.applicant_id,
       a.competition_priority, a.consent_text, a.has_consent, a.score_total,
       a.exam_scores_text, a.individual_score, a.status, a.selected_at_moscow
FROM applications a
JOIN applicants ap ON ap.id = a.applicant_id;
"""


def seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users(username, password_text, created_at) VALUES ('legacy', 'legacy-only', '2025-01-01T00:00:00+00:00')"
    )
    conn.execute("INSERT INTO universities(id, name) VALUES (1, 'Тестовый университет')")
    conn.execute("INSERT INTO programs(id, name) VALUES (1, 'Прикладная математика'), (2, 'Физика')")
    conn.executemany(
        "INSERT INTO applicants(id, applicant_id) VALUES (?, ?)",
        [
            (1, "4150096"),
            (2, "1000001"),
            (3, "1000002"),
            (4, "1000003"),
            (5, "99999999999999999999"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO competitions_core(
          id, source_path, file_name, program_id, education_level, city,
          university_id, study_form, quota_type, seats, path_json, row_count,
          imported_at
        ) VALUES (?, ?, ?, ?, 'Бакалавриат', 'Москва', 1, 'Очная',
                  'Общий конкурс', ?, '[]', ?, ?)
        """,
        [
            (1, "gosuslugi2026/competition/1", "1.json", 1, 3, 5, "2026-07-30T12:00:00+00:00"),
            (2, "gosuslugi2026/competition/2", "2.json", 2, 2, 2, "2026-07-30T12:00:00+00:00"),
            (3, "2025-final/competition/3", "3.json", 1, 1, 1, "2025-08-10T12:00:00+00:00"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO applications(
          id, competition_id, applicant_id, ordinal, competition_priority,
          consent_text, has_consent, score_total, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, 2, 1, 1, "Да", 1, 300, "участвует"),
            (2, 1, 3, 2, 1, "Нет", 0, 290, "участвует"),
            (3, 1, 4, 3, 1, "Нет", 0, 280, "участвует"),
            (4, 1, 1, 4, 2, "Нет", 0, 270, "участвует"),
            (5, 1, 5, 5, 2, "Нет", 0, 260, "участвует"),
            (6, 2, 3, 1, 1, "Да", 1, 290, "участвует"),
            (7, 3, 1, 1, 1, "Да", 1, 270, "зачислен"),
        ],
    )
    conn.commit()


@pytest.fixture()
def migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "vvuzz.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(LEGACY_SCHEMA)
    seed(conn)
    conn.close()
    apply(db_path, with_stats=True)
    return db_path
