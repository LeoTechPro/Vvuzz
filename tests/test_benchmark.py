import sqlite3
import threading
from pathlib import Path

import pytest

from scripts.benchmark_beta import (
    ingest_lock_probe,
    percentile,
    sanitized_base_url,
    validated_ids,
)


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([], 0.95) is None
    assert percentile([0.4, 0.1, 0.3, 0.2], 0.5) == 0.2
    assert percentile([0.4, 0.1, 0.3, 0.2], 0.95) == 0.4


def test_validated_ids_accepts_comments_without_echoing_invalid_value(
    tmp_path: Path,
) -> None:
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("# acceptance set\n4150096\n9999\n", encoding="utf-8")
    assert validated_ids(ids_file) == ["4150096", "9999"]

    ids_file.write_text("private-value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid value") as exc_info:
        validated_ids(ids_file)
    assert "private-value" not in str(exc_info.value)


def test_sanitized_base_url_removes_credentials_query_and_fragment() -> None:
    assert (
        sanitized_base_url("https://secret:user@example.test:8443/beta/?q=1#x")
        == "https://example.test:8443/beta"
    )


def test_ingest_lock_probe_rolls_back_without_mutating_db(tmp_path: Path) -> None:
    db_path = tmp_path / "probe.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO campaigns DEFAULT VALUES")
        conn.commit()

    duration, ok = ingest_lock_probe(
        threading.Barrier(1),
        db_path,
        timeout=1.0,
        hold_seconds=0,
    )

    assert ok is True
    assert duration >= 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM campaigns").fetchone()[0] == 1
