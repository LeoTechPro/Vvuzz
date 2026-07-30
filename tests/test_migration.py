from __future__ import annotations

import sqlite3

from migrations.migrate import apply, preflight


def test_additive_migration_preserves_counts_and_campaigns(migrated_db):
    report = preflight(migrated_db)
    assert report["integrity_check"] == "ok"
    assert report["counts"] == {
        "competitions": 3,
        "applications": 7,
        "applicants": 5,
    }
    second = apply(migrated_db, with_stats=True)
    assert second["after_counts"] == report["counts"]
    assert second["after_counts_by_year"] == report["counts_by_year"]
    assert set(report["counts_by_year"]) == {"2025", "2026"}

    conn = sqlite3.connect(migrated_db)
    assert conn.execute("SELECT username FROM users").fetchall() == [("legacy",)]
    assert conn.execute("SELECT year, status FROM campaigns ORDER BY year").fetchall() == [
        (2025, "final"),
        (2026, "active"),
    ]
    assert conn.execute(
        "SELECT count(*) FROM competitions_core WHERE campaign_id IS NULL"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT competition_count FROM campaign_stats s JOIN campaigns c ON c.id=s.campaign_id WHERE c.year=2026"
    ).fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
    assert conn.execute(
        "SELECT final_snapshot_id IS NOT NULL FROM campaigns WHERE year=2025"
    ).fetchone()[0] == 1
    conn.execute(
        """
        INSERT INTO competitions_core(
          source_path, file_name, path_json, row_count, imported_at
        ) VALUES ('gosuslugi2026/competition/legacy-v1', 'legacy.json', '[]', 0, '2026-07-30T12:00:00+00:00')
        """
    )
    assert conn.execute(
        """
        SELECT c.year
        FROM competitions_core core
        JOIN campaigns c ON c.id = core.campaign_id
        WHERE core.source_path = 'gosuslugi2026/competition/legacy-v1'
        """
    ).fetchone()[0] == 2026
    conn.close()
