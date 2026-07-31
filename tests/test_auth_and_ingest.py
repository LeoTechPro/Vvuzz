from __future__ import annotations

import datetime as dt
import sqlite3

from fastapi.testclient import TestClient

from vvuzz_public.config import Settings
from vvuzz_public.main import create_app
from vvuzz_public.security import hash_password


def client_for(db_path):
    settings = Settings(
        db_path=db_path,
        environment="test",
        base_path="",
        cache_hmac_key=b"test-hmac-key-that-is-long-enough",
        ingest_token="test-ingest-token",
        lookup_timing_floor_ms=1,
        rate_limit_requests=100,
    )
    return TestClient(create_app(settings))


def create_admin(db_path):
    now = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO auth_users(
          username, password_hash, role, is_active, created_at, updated_at
        ) VALUES (?, ?, 'admin', 1, ?, ?)
        """,
        ("operator", hash_password("correct horse battery staple"), now, now),
    )
    conn.commit()
    conn.close()


def login_csrf(client: TestClient) -> str:
    response = client.get("/manage")
    assert response.status_code == 200
    token = client.cookies.get("vvuzz_login_csrf")
    assert token
    return token


def test_manage_session_rotation_csrf_and_logout(migrated_db):
    create_admin(migrated_db)
    client = client_for(migrated_db)
    csrf_before_login = login_csrf(client)
    login = client.post(
        "/manage/login",
        data={
            "username": "operator",
            "password": "correct horse battery staple",
            "login_csrf": csrf_before_login,
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert "HttpOnly" in login.headers["set-cookie"]
    session_token = client.cookies.get("vvuzz_manage")
    csrf = client.cookies.get("vvuzz_csrf")
    assert session_token and csrf
    assert client.get("/manage").status_code == 200
    assert client.post("/manage/logout", data={"csrf_token": "wrong"}).status_code == 403
    logout = client.post(
        "/manage/logout",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert logout.status_code == 303


def test_manage_lockout_after_repeated_failures(migrated_db):
    create_admin(migrated_db)
    client = client_for(migrated_db)
    csrf_before_login = login_csrf(client)
    for _ in range(5):
        response = client.post(
            "/manage/login",
            data={
                "username": "operator",
                "password": "wrong password",
                "login_csrf": csrf_before_login,
            },
        )
        assert response.status_code == 401
    response = client.post(
        "/manage/login",
        data={
            "username": "operator",
            "password": "correct horse battery staple",
            "login_csrf": csrf_before_login,
        },
    )
    assert response.status_code == 401


def test_manage_login_requires_csrf(migrated_db):
    create_admin(migrated_db)
    client = client_for(migrated_db)
    assert client.post(
        "/manage/login",
        data={"username": "operator", "password": "correct horse battery staple"},
    ).status_code == 422
    login_csrf(client)
    assert client.post(
        "/manage/login",
        data={
            "username": "operator",
            "password": "correct horse battery staple",
            "login_csrf": "forged",
        },
    ).status_code == 403


def test_ingest_v2_is_idempotent(migrated_db):
    client = client_for(migrated_db)
    payload = {
        "campaign_year": 2026,
        "run_id": "run-test-1",
        "source_key": "source-test-1",
        "snapshot_at": "2026-07-30T15:00:00+00:00",
        "competitions": [
            {
                "source_path": "gosuslugi2026/competition/new",
                "program": "Информатика",
                "university": "Тестовый университет",
                "study_form": "Очная",
                "quota_type": "Общий конкурс",
                "seats": 2,
                "replace_applications": True,
                "applications": [
                    {
                        "applicant_id": "4150096",
                        "ordinal": 1,
                        "competition_priority": 1,
                        "has_consent": True,
                    }
                ],
            }
        ],
    }
    headers = {"X-Vvuzz-Ingest-Token": "test-ingest-token"}
    first = client.post("/api/ingest/v2/batch", json=payload, headers=headers)
    second = client.post("/api/ingest/v2/batch", json=payload, headers=headers)
    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert first.json()["snapshot_id"] == second.json()["snapshot_id"]

    finalized_payload = {**payload, "finalize_campaign": True}
    conflict = client.post(
        "/api/ingest/v2/batch", json=finalized_payload, headers=headers
    )
    assert conflict.status_code == 409

    final_payload = {
        **payload,
        "run_id": "run-test-final",
        "source_key": "source-test-final",
        "competitions": [],
        "finalize_campaign": True,
    }
    finalized = client.post(
        "/api/ingest/v2/batch", json=final_payload, headers=headers
    )
    conn = sqlite3.connect(migrated_db)
    conn.execute(
        """
        CREATE TRIGGER reject_duplicate_stats_refresh
        BEFORE UPDATE ON campaign_stats
        BEGIN
          SELECT RAISE(ABORT, 'duplicate completion must not refresh stats');
        END
        """
    )
    conn.commit()
    conn.close()
    finalized_retry = client.post(
        "/api/ingest/v2/batch", json=final_payload, headers=headers
    )
    assert finalized.status_code == 202
    assert finalized_retry.status_code == 200
    assert finalized_retry.json()["duplicate"] is True

    conn = sqlite3.connect(migrated_db)
    conn.execute("DROP TRIGGER reject_duplicate_stats_refresh")
    assert conn.execute(
        "SELECT count(*) FROM snapshots WHERE run_id='run-test-1' AND source_key='source-test-1'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT status FROM campaigns WHERE year=2026"
    ).fetchone()[0] == "final"
    assert conn.execute(
        "SELECT count(*) FROM applicants WHERE applicant_id='2026:4150096'"
    ).fetchone()[0] == 1
    conn.close()


def test_ingest_v2_rejects_reused_key_with_changed_payload(migrated_db):
    client = client_for(migrated_db)
    payload = {
        "campaign_year": 2026,
        "run_id": "stable-run",
        "source_key": "stable-source",
        "snapshot_at": "2026-07-30T15:00:00+00:00",
        "competitions": [],
    }
    headers = {"X-Vvuzz-Ingest-Token": "test-ingest-token"}
    assert client.post(
        "/api/ingest/v2/batch", json=payload, headers=headers
    ).status_code == 202
    changed = {**payload, "snapshot_at": "2026-07-30T15:01:00+00:00"}
    response = client.post(
        "/api/ingest/v2/batch", json=changed, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["accepted"] is False


def test_ingest_v2_replaces_legacy_raw_identity_without_duplicate(migrated_db):
    conn = sqlite3.connect(migrated_db)
    source_path, competition_id = conn.execute(
        """
        SELECT c.source_path, c.id
        FROM competitions_core c
        JOIN campaigns campaign ON campaign.id=c.campaign_id
        WHERE campaign.year=2026
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    client = client_for(migrated_db)
    payload = {
        "campaign_year": 2026,
        "run_id": "mixed-identity-run",
        "source_key": "mixed-identity-source",
        "snapshot_at": "2026-07-30T15:00:00+00:00",
        "competitions": [
            {
                "source_path": source_path,
                "applications": [
                    {"applicant_id": "4150096", "ordinal": 7}
                ],
            }
        ],
    }
    response = client.post(
        "/api/ingest/v2/batch",
        json=payload,
        headers={"X-Vvuzz-Ingest-Token": "test-ingest-token"},
    )
    assert response.status_code == 202

    conn = sqlite3.connect(migrated_db)
    rows = conn.execute(
        """
        SELECT ap.applicant_id
        FROM applications a
        JOIN applicants ap ON ap.id=a.applicant_id
        WHERE a.competition_id=?
          AND ap.applicant_id IN ('4150096', '2026:4150096')
        """,
        (competition_id,),
    ).fetchall()
    conn.close()
    assert rows == [("2026:4150096",)]
    lookup = client.post(
        "/api/public/lookup", json={"year": 2026, "applicant_id": "4150096"}
    ).json()
    assert [row["competition_id"] for row in lookup["results"]].count(competition_id) == 1


def test_final_campaign_rejects_ingest(migrated_db):
    client = client_for(migrated_db)
    response = client.post(
        "/api/ingest/v2/batch",
        headers={"X-Vvuzz-Ingest-Token": "test-ingest-token"},
        json={
            "campaign_year": 2025,
            "run_id": "late-write",
            "source_key": "late-write",
            "snapshot_at": "2026-07-30T15:00:00+00:00",
            "competitions": [],
        },
    )
    assert response.status_code == 422
    assert "final" in response.json()["detail"]
