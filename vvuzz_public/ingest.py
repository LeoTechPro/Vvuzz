from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from migrations.migrate import refresh_stats


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def payload_digest(payload: dict) -> str:
    content = {
        key: value
        for key, value in payload.items()
        if key != "payload_sha256"
    }
    canonical = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def named_id(conn: sqlite3.Connection, table: str, value: str | None) -> int | None:
    if not value:
        return None
    conn.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES (?)", (value,))
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (value,)).fetchone()
    return row["id"] if row else None


def _chunks(values: list, size: int = 500):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def upsert_applications(
    conn: sqlite3.Connection,
    competition_id: int,
    applications: list[dict],
    *,
    campaign_year: int,
    replace: bool,
) -> tuple[int, int]:
    by_external_id: dict[str, dict] = {}
    legacy_external_ids: list[str] = []
    for application in applications:
        external_id = str(application.get("applicant_id") or "").strip()
        if not external_id:
            raise ValueError("application.applicant_id is required")
        storage_id = (
            external_id
            if external_id.startswith(f"{campaign_year}:")
            else f"{campaign_year}:{external_id}"
        )
        if storage_id != external_id:
            legacy_external_ids.append(external_id)
        by_external_id[storage_id] = application
    external_ids = list(by_external_id)
    conn.executemany(
        "INSERT OR IGNORE INTO applicants(applicant_id) VALUES (?)",
        ((external_id,) for external_id in external_ids),
    )
    applicant_ids: dict[str, int] = {}
    for batch in _chunks(external_ids):
        placeholders = ",".join("?" for _ in batch)
        for row in conn.execute(
            f"SELECT id, applicant_id FROM applicants WHERE applicant_id IN ({placeholders})",
            batch,
        ):
            applicant_ids[row["applicant_id"]] = row["id"]

    if replace:
        conn.execute("DELETE FROM applications WHERE competition_id = ?", (competition_id,))
        existing_ids: dict[int, int] = {}
    else:
        for legacy_external_id in legacy_external_ids:
            conn.execute(
                """
                DELETE FROM applications
                WHERE competition_id = ?
                  AND applicant_id = (
                    SELECT id FROM applicants WHERE applicant_id = ?
                  )
                """,
                (competition_id, legacy_external_id),
            )
        internal_ids = list(applicant_ids.values())
        existing_ids = {}
        for batch in _chunks(internal_ids):
            placeholders = ",".join("?" for _ in batch)
            for row in conn.execute(
                f"""
                SELECT id, applicant_id
                FROM applications
                WHERE competition_id = ? AND applicant_id IN ({placeholders})
                """,
                (competition_id, *batch),
            ):
                existing_ids[row["applicant_id"]] = row["id"]

    inserts = []
    updates = []
    for external_id, application in by_external_id.items():
        applicant_id = applicant_ids[external_id]
        values = (
            application.get("ordinal"),
            application.get("competition_priority"),
            application.get("consent_text"),
            1 if application.get("has_consent") else 0,
            application.get("score_total"),
            application.get("exam_scores_text"),
            application.get("individual_score"),
            application.get("status"),
            application.get("selected_at_moscow"),
        )
        if applicant_id in existing_ids:
            updates.append((*values, existing_ids[applicant_id]))
        else:
            inserts.append((competition_id, applicant_id, *values))
    conn.executemany(
        """
        UPDATE applications
        SET ordinal=?, competition_priority=?, consent_text=?, has_consent=?,
            score_total=?, exam_scores_text=?, individual_score=?, status=?,
            selected_at_moscow=?
        WHERE id=?
        """,
        updates,
    )
    conn.executemany(
        """
        INSERT INTO applications(
          competition_id, applicant_id, ordinal, competition_priority, consent_text,
          has_consent, score_total, exam_scores_text, individual_score, status,
          selected_at_moscow
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        inserts,
    )
    return len(inserts), len(updates)


def validate_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    required = ("campaign_year", "run_id", "source_key", "snapshot_at")
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    year = payload["campaign_year"]
    if not isinstance(year, int) or not 2020 <= year <= 2100:
        raise ValueError("campaign_year must be an integer between 2020 and 2100")
    if len(str(payload["run_id"])) > 160 or len(str(payload["source_key"])) > 500:
        raise ValueError("run_id or source_key is too long")
    if not isinstance(payload.get("competitions", []), list):
        raise ValueError("competitions must be a list")
    try:
        snapshot_at = dt.datetime.fromisoformat(str(payload["snapshot_at"]))
    except ValueError as exc:
        raise ValueError("snapshot_at must be an ISO 8601 timestamp") from exc
    if snapshot_at.tzinfo is None:
        raise ValueError("snapshot_at must include a timezone")
    for flag in ("refresh_stats", "finalize_campaign"):
        if flag in payload and not isinstance(payload[flag], bool):
            raise ValueError(f"{flag} must be a boolean")
    for competition in payload.get("competitions", []):
        if not isinstance(competition, dict):
            raise ValueError("each competition must be an object")
        source_path = str(competition.get("source_path") or "")
        if len(source_path) > 1000:
            raise ValueError("competition.source_path is too long")
        if not isinstance(competition.get("applications", []), list):
            raise ValueError("competition.applications must be a list")


def refresh_and_finalize(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    snapshot_id: int,
    payload: dict,
    timestamp: str,
) -> str:
    if payload.get("refresh_stats") or payload.get("finalize_campaign"):
        refresh_stats(conn, campaign_id, timestamp)
        conn.execute(
            "UPDATE campaign_stats SET snapshot_id=? WHERE campaign_id=?",
            (snapshot_id, campaign_id),
        )
    if payload.get("finalize_campaign"):
        conn.execute(
            "UPDATE snapshots SET status='final', updated_at=? WHERE id=?",
            (timestamp, snapshot_id),
        )
        conn.execute(
            """
            UPDATE campaigns
            SET status='final', final_snapshot_id=?, updated_at=?
            WHERE id=?
            """,
            (snapshot_id, timestamp, campaign_id),
        )
    conn.commit()
    return "final" if payload.get("finalize_campaign") else "ready"


def refresh_legacy_campaigns(
    conn: sqlite3.Connection,
    *,
    competition_ids: list[int],
    payload: dict,
) -> list[int]:
    if not competition_ids:
        return []
    has_campaigns = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaigns'"
    ).fetchone()
    if not has_campaigns:
        return []
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in competition_ids)
    campaigns = conn.execute(
        f"""
        SELECT DISTINCT c.id, c.status
        FROM campaigns c
        JOIN competitions_core core ON core.campaign_id = c.id
        WHERE core.id IN ({placeholders})
        """,
        competition_ids,
    ).fetchall()
    timestamp = now_iso()
    snapshot_at = str(payload.get("fetched_at") or timestamp)
    source_key = f"legacy:{payload.get('source_key') or 'batch'}"
    payload_sha256 = payload_digest(payload)
    run_id = (
        f"legacy:{payload.get('run_id') or 'batch'}:"
        f"{payload_sha256[:16]}"
    )
    snapshot_ids = []
    for campaign in campaigns:
        if campaign["status"] == "final":
            continue
        totals = conn.execute(
            f"""
            SELECT count(DISTINCT c.id), count(a.id), count(DISTINCT a.applicant_id)
            FROM competitions_core c
            LEFT JOIN applications a ON a.competition_id = c.id
            WHERE c.campaign_id=? AND c.id IN ({placeholders})
            """,
            (campaign["id"], *competition_ids),
        ).fetchone()
        snapshot = conn.execute(
            """
            INSERT INTO snapshots(
              campaign_id, run_id, source_key, snapshot_at, status,
              competition_count, application_count, applicant_count,
              payload_sha256, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, run_id, source_key) DO UPDATE SET
              snapshot_at=excluded.snapshot_at,
              status='ready',
              competition_count=excluded.competition_count,
              application_count=excluded.application_count,
              applicant_count=excluded.applicant_count,
              payload_sha256=excluded.payload_sha256,
              updated_at=excluded.updated_at
            RETURNING id
            """,
            (
                campaign["id"],
                run_id,
                source_key,
                snapshot_at,
                totals[0],
                totals[1],
                totals[2],
                payload_sha256,
                timestamp,
                timestamp,
            ),
        ).fetchone()
        snapshot_ids.append(snapshot["id"])
        conn.commit()
        refresh_stats(conn, campaign["id"], timestamp)
        conn.execute(
            "UPDATE campaign_stats SET snapshot_id=? WHERE campaign_id=?",
            (snapshot["id"], campaign["id"]),
        )
        conn.commit()
    return snapshot_ids


def ingest_v2(conn: sqlite3.Connection, payload: dict) -> tuple[int, dict]:
    validate_payload(payload)
    expected_digest = payload_digest(payload)
    timestamp = now_iso()
    year = payload["campaign_year"]
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO campaigns(year, name, status, created_at, updated_at)
            VALUES (?, ?, 'active', ?, ?)
            ON CONFLICT(year) DO UPDATE SET updated_at=excluded.updated_at
            """,
            (year, f"Приёмная кампания {year}", timestamp, timestamp),
        )
        campaign = conn.execute(
            "SELECT id, status FROM campaigns WHERE year = ?", (year,)
        ).fetchone()
        duplicate = conn.execute(
            """
            SELECT id, status, competition_count, application_count, error_count,
                   payload_sha256
            FROM snapshots
            WHERE campaign_id=? AND run_id=? AND source_key=?
            """,
            (campaign["id"], str(payload["run_id"]), str(payload["source_key"])),
        ).fetchone()
        if duplicate:
            conn.rollback()
            if duplicate["payload_sha256"] not in {None, expected_digest}:
                return 409, {
                    "accepted": False,
                    "duplicate": True,
                    "snapshot_id": duplicate["id"],
                    "error": "idempotency key was already used with another payload",
                }
            return 200, {
                "accepted": duplicate["status"] in {"ready", "final"},
                "duplicate": True,
                "snapshot_id": duplicate["id"],
                "competitions": duplicate["competition_count"],
                "applications": duplicate["application_count"],
                "errors": duplicate["error_count"],
            }
        if campaign["status"] == "final":
            raise ValueError("campaign is final and cannot accept new ingest")

        cursor = conn.execute(
            """
            INSERT INTO snapshots(
              campaign_id, run_id, source_key, snapshot_at, status, payload_sha256,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'receiving', ?, ?, ?)
            """,
            (
                campaign["id"],
                str(payload["run_id"]),
                str(payload["source_key"]),
                str(payload["snapshot_at"]),
                expected_digest,
                timestamp,
                timestamp,
            ),
        )
        snapshot_id = cursor.lastrowid
        if snapshot_id is None:
            raise RuntimeError("failed to create snapshot")
        inserted = updated = 0
        batch_applicant_ids: set[str] = set()
        competitions = payload.get("competitions") or []
        for item in competitions:
            source_path = str(item.get("source_path") or "").strip()
            if not source_path:
                raise ValueError("competition.source_path is required")
            applications = item.get("applications") or []
            program_id = named_id(conn, "programs", item.get("program"))
            university_id = named_id(conn, "universities", item.get("university"))
            existing = conn.execute(
                "SELECT id, campaign_id FROM competitions_core WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            values = (
                item.get("file_name") or Path(source_path).name,
                program_id,
                item.get("education_level"),
                item.get("city"),
                university_id,
                item.get("study_form"),
                item.get("quota_type"),
                item.get("seats"),
                json.dumps(item.get("path") or [], ensure_ascii=False),
                len(applications),
                str(payload["snapshot_at"]),
                campaign["id"],
            )
            if existing:
                if existing["campaign_id"] not in {None, campaign["id"]}:
                    raise ValueError("source_path already belongs to another campaign")
                competition_id = existing["id"]
                conn.execute(
                    """
                    UPDATE competitions_core
                    SET file_name=?, program_id=?, education_level=?, city=?,
                        university_id=?, study_form=?, quota_type=?, seats=?,
                        path_json=?, row_count=?, imported_at=?, campaign_id=?
                    WHERE id=?
                    """,
                    (*values, competition_id),
                )
            else:
                inserted_row = conn.execute(
                    """
                    INSERT INTO competitions_core(
                      source_path, file_name, program_id, education_level, city,
                      university_id, study_form, quota_type, seats, path_json,
                      row_count, imported_at, campaign_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source_path, *values),
                )
                competition_id = inserted_row.lastrowid
            for application in applications:
                external_id = str(application.get("applicant_id") or "").strip()
                if external_id:
                    batch_applicant_ids.add(external_id)
            inserted_count, updated_count = upsert_applications(
                conn,
                competition_id,
                applications,
                campaign_year=year,
                replace=bool(item.get("replace_applications")),
            )
            inserted += inserted_count
            updated += updated_count
            consent_count = conn.execute(
                "SELECT count(*) FROM applications WHERE competition_id=? AND has_consent=1",
                (competition_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO competition_snapshot_metrics(
                  snapshot_id, competition_id, seats, row_count, consent_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (snapshot_id, competition_id, item.get("seats"), len(applications), consent_count),
            )

        conn.execute(
            """
            UPDATE snapshots
            SET status=?, competition_count=?, application_count=?,
                applicant_count=?, updated_at=?
            WHERE id=?
            """,
            (
                "ready",
                len(competitions),
                inserted + updated,
                len(batch_applicant_ids),
                timestamp,
                snapshot_id,
            ),
        )
        conn.commit()
        refresh_and_finalize(
            conn,
            campaign_id=campaign["id"],
            snapshot_id=snapshot_id,
            payload=payload,
            timestamp=timestamp,
        )
        return 202, {
            "accepted": True,
            "duplicate": False,
            "snapshot_id": snapshot_id,
            "competitions": len(competitions),
            "inserted": inserted,
            "updated": updated,
            "errors": 0,
        }
    except Exception:
        conn.rollback()
        raise
