#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SQL_PATH = Path(__file__).with_name("001_public_archive.sql")


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    conn.row_factory = sqlite3.Row
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "competitions": conn.execute("SELECT count(*) FROM competitions_core").fetchone()[0],
        "applications": conn.execute("SELECT count(*) FROM applications").fetchone()[0],
        "applicants": conn.execute("SELECT count(*) FROM applicants").fetchone()[0],
    }


def preflight(db_path: Path) -> dict:
    usage = shutil.disk_usage(db_path.parent)
    db_size = db_path.stat().st_size
    wal_path = Path(f"{db_path}-wal")
    wal_size = wal_path.stat().st_size if wal_path.exists() else 0
    projected_peak = db_size + wal_size + max(512 * 1024 * 1024, db_size // 5)
    reserve = int(usage.total * 0.05)
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=5)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        integrity_check = conn.execute("PRAGMA integrity_check(1)").fetchone()[0]
        before = counts(conn)
    finally:
        conn.close()
    return {
        "db_path": str(db_path),
        "db_size": db_size,
        "wal_size": wal_size,
        "free_bytes": usage.free,
        "required_reserve_bytes": reserve,
        "projected_peak_bytes": projected_peak,
        "headroom_after_projection": usage.free - projected_peak,
        "reserve_ok": usage.free - projected_peak >= reserve,
        "journal_mode": journal_mode,
        "integrity_check": integrity_check,
        "counts": before,
    }


def refresh_stats(conn: sqlite3.Connection, campaign_id: int, timestamp: str) -> None:
    top_institutions = [
        dict(row)
        for row in conn.execute(
            """
            SELECT COALESCE(u.name, 'Не указан') AS name, count(*) AS competitions
            FROM competitions_core c
            LEFT JOIN universities u ON u.id = c.university_id
            WHERE c.campaign_id = ?
            GROUP BY u.name
            ORDER BY competitions DESC, name
            LIMIT 50
            """,
            (campaign_id,),
        )
    ]
    top_programs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT COALESCE(p.name, 'Не указана') AS name, count(*) AS competitions
            FROM competitions_core c
            LEFT JOIN programs p ON p.id = c.program_id
            WHERE c.campaign_id = ?
            GROUP BY p.name
            ORDER BY competitions DESC, name
            LIMIT 50
            """,
            (campaign_id,),
        )
    ]
    aggregate = conn.execute(
        """
        SELECT
          count(DISTINCT c.id) AS competitions,
          count(a.id) AS applications,
          count(DISTINCT a.applicant_id) AS applicants,
          count(DISTINCT c.university_id) AS institutions,
          count(DISTINCT c.program_id) AS programs,
          max(c.imported_at) AS freshness_at
        FROM competitions_core c
        LEFT JOIN applications a ON a.competition_id = c.id
        WHERE c.campaign_id = ?
        """,
        (campaign_id,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO campaign_stats(
          campaign_id, competition_count, application_count, applicant_count,
          institution_count, program_count, institutions_json, programs_json,
          freshness_at, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(campaign_id) DO UPDATE SET
          competition_count=excluded.competition_count,
          application_count=excluded.application_count,
          applicant_count=excluded.applicant_count,
          institution_count=excluded.institution_count,
          program_count=excluded.program_count,
          institutions_json=excluded.institutions_json,
          programs_json=excluded.programs_json,
          freshness_at=excluded.freshness_at,
          computed_at=excluded.computed_at
        """,
        (
            campaign_id,
            aggregate[0],
            aggregate[1],
            aggregate[2],
            aggregate[3],
            aggregate[4],
            json.dumps(top_institutions, ensure_ascii=False),
            json.dumps(top_programs, ensure_ascii=False),
            aggregate[5],
            timestamp,
        ),
    )


def apply(db_path: Path, *, with_stats: bool) -> dict:
    report = preflight(db_path)
    if report["integrity_check"] != "ok":
        raise RuntimeError(f"SQLite integrity_check failed: {report['integrity_check']}")
    if not report["reserve_ok"]:
        raise RuntimeError("Projected migration peak would violate the 5% disk reserve")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        for statement in SQL_PATH.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                conn.execute(statement)
        if not column_exists(conn, "competitions_core", "campaign_id"):
            conn.execute(
                "ALTER TABLE competitions_core ADD COLUMN campaign_id INTEGER REFERENCES campaigns(id)"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_competitions_core_campaign ON competitions_core(campaign_id, id)"
        )
        timestamp = now_iso()
        conn.execute(
            """
            INSERT INTO campaigns(year, name, status, created_at, updated_at)
            VALUES (2025, 'Приёмная кампания 2025', 'final', ?, ?)
            ON CONFLICT(year) DO NOTHING
            """,
            (timestamp, timestamp),
        )
        conn.execute(
            """
            INSERT INTO campaigns(year, name, status, created_at, updated_at)
            VALUES (2026, 'Приёмная кампания 2026', 'active', ?, ?)
            ON CONFLICT(year) DO NOTHING
            """,
            (timestamp, timestamp),
        )
        conn.execute(
            """
            UPDATE competitions_core
            SET campaign_id = (
              SELECT id FROM campaigns
              WHERE year = CASE
                WHEN lower(competitions_core.source_path) LIKE '%2026%' THEN 2026
                ELSE 2025
              END
            )
            WHERE campaign_id IS NULL
            """
        )
        if with_stats:
            for row in conn.execute("SELECT id FROM campaigns ORDER BY year"):
                refresh_stats(conn, row["id"], timestamp)
        after = counts(conn)
        if after != report["counts"]:
            raise RuntimeError(f"Count parity failed: before={report['counts']} after={after}")
        conn.commit()
        return {**report, "after_counts": after, "stats_refreshed": with_stats}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded additive Vvuzz Public migration")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="apply after preflight")
    parser.add_argument("--with-stats", action="store_true", help="compute expensive aggregates")
    args = parser.parse_args()
    report = apply(args.db, with_stats=args.with_stats) if args.apply else preflight(args.db)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
