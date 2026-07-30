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
    conn.close()
