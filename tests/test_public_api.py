from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from vvuzz_public.config import Settings
from vvuzz_public.main import create_app
from vvuzz_public.ingest import refresh_legacy_campaigns


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


def test_lookup_is_post_only_and_privacy_bounded(migrated_db):
    client = client_for(migrated_db)
    response = client.post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "4150096"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    payload = response.json()
    assert payload["found"] is True
    assert payload["campaign_year"] == 2026
    assert payload["results"][0]["competition_id"] == 1
    assert "consent_here" not in payload["results"][0]
    details = client.post(
        "/api/public/competition",
        json={
            "year": 2026,
            "applicant_id": "4150096",
            "competition_ids": [payload["results"][0]["competition_id"]],
        },
    )
    assert details.status_code == 200
    detail = details.json()["competitions"][0]
    assert detail["consent_here"] == 1
    assert detail["consent_elsewhere"] == 1
    assert detail["no_consent"] == 1
    assert "4150096" not in response.text
    assert client.get("/api/public/lookup?id=4150096").status_code == 405


def test_missing_lookup_keeps_response_shape(migrated_db):
    client = client_for(migrated_db)
    found = client.post("/api/public/lookup", json={"year": 2026, "applicant_id": "4150096"}).json()
    missing = client.post("/api/public/lookup", json={"year": 2026, "applicant_id": "7777777"}).json()
    assert set(found) == set(missing)
    assert missing["found"] is False
    assert missing["results"] == []


def test_extreme_and_invalid_ids(migrated_db):
    client = client_for(migrated_db)
    extreme = client.post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "99999999999999999999"},
    )
    assert extreme.status_code == 200
    assert extreme.json()["found"] is True
    assert client.post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "../4150096"},
    ).status_code == 422


def test_campaigns_archive_and_html_fallback(migrated_db):
    client = client_for(migrated_db)
    campaigns = client.get("/api/public/campaigns")
    assert campaigns.status_code == 200
    assert [item["year"] for item in campaigns.json()["campaigns"]] == [2026, 2025]
    archive = client.get("/api/public/archive/2025/overview")
    assert archive.status_code == 200
    assert archive.json()["competition_count"] == 1
    html = client.post("/lookup", data={"year": "2026", "applicant_id": "4150096"})
    assert html.status_code == 200
    assert "Ваши конкурсы" in html.text
    assert "4150096" not in html.text


def test_consent_in_another_campaign_is_not_counted(migrated_db):
    conn = sqlite3.connect(migrated_db)
    conn.execute(
        """
        INSERT INTO applications(
          competition_id, applicant_id, ordinal, competition_priority,
          consent_text, has_consent, score_total, status
        ) VALUES (3, 4, 2, 1, 'Да', 1, 280, 'зачислен')
        """
    )
    conn.commit()
    conn.close()
    response = client_for(migrated_db).post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "4150096"},
    )
    competition_id = response.json()["results"][0]["competition_id"]
    result = client_for(migrated_db).post(
        "/api/public/competition",
        json={
            "year": 2026,
            "applicant_id": "4150096",
            "competition_ids": [competition_id],
        },
    ).json()["competitions"][0]
    assert result["consent_elsewhere"] == 1
    assert result["no_consent"] == 1


def test_25_parallel_public_lookups_complete_without_errors(migrated_db):
    client = client_for(migrated_db)

    def lookup_once(_):
        return client.post(
            "/api/public/lookup",
            json={"year": 2026, "applicant_id": "4150096"},
        ).status_code

    with ThreadPoolExecutor(max_workers=25) as pool:
        statuses = list(pool.map(lookup_once, range(25)))
    assert statuses == [200] * 25


def test_lookup_paginates_more_than_50_competitions(migrated_db):
    conn = sqlite3.connect(migrated_db)
    campaign_id = conn.execute("SELECT id FROM campaigns WHERE year=2026").fetchone()[0]
    applicant_id = conn.execute(
        "SELECT id FROM applicants WHERE applicant_id='4150096'"
    ).fetchone()[0]
    for index in range(10, 65):
        conn.execute(
            """
            INSERT INTO competitions_core(
              id, source_path, file_name, program_id, education_level, city,
              university_id, study_form, quota_type, seats, path_json,
              row_count, imported_at, campaign_id
            ) VALUES (?, ?, ?, 1, 'Бакалавриат', 'Москва', 1, 'Очная',
                      'Общий конкурс', 10, '[]', 1, ?, ?)
            """,
            (
                index,
                f"gosuslugi2026/competition/{index}",
                f"{index}.json",
                "2026-07-30T12:00:00+00:00",
                campaign_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO applications(
              competition_id, applicant_id, ordinal, competition_priority,
              consent_text, has_consent
            ) VALUES (?, ?, 1, 1, 'Нет', 0)
            """,
            (index, applicant_id),
        )
    conn.commit()
    conn.close()

    client = client_for(migrated_db)
    first = client.post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "4150096", "offset": 0},
    ).json()
    second = client.post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "4150096", "offset": first["next_offset"]},
    ).json()
    assert len(first["results"]) == 50
    assert first["truncated"] is True
    assert len(second["results"]) == 6
    assert second["truncated"] is False
    combined = first["results"] + second["results"]
    competition_ids = [item["competition_id"] for item in combined]
    assert len(competition_ids) == len(set(competition_ids))
    ordering = [(item["position"], item["competition_id"]) for item in combined]
    assert ordering == sorted(ordering)


def test_legacy_bridge_invalidates_cached_lookup(migrated_db):
    client = client_for(migrated_db)
    first = client.post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "4150096"},
    ).json()
    conn = sqlite3.connect(migrated_db)
    conn.row_factory = sqlite3.Row
    campaign_id = conn.execute(
        "SELECT id FROM campaigns WHERE year=2026"
    ).fetchone()[0]
    applicant_id = conn.execute(
        "SELECT id FROM applicants WHERE applicant_id='4150096'"
    ).fetchone()[0]
    cursor = conn.execute(
        """
        INSERT INTO competitions_core(
          source_path, file_name, program_id, university_id, seats, path_json,
          row_count, imported_at, campaign_id
        ) VALUES ('gosuslugi2026/competition/legacy-bridge', 'legacy.json',
                  1, 1, 3, '[]', 1, '2026-07-30T15:30:00+00:00', ?)
        """,
        (campaign_id,),
    )
    competition_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO applications(
          competition_id, applicant_id, ordinal, competition_priority,
          consent_text, has_consent
        ) VALUES (?, ?, 2, 1, 'Нет', 0)
        """,
        (competition_id, applicant_id),
    )
    conn.commit()
    refresh_legacy_campaigns(
        conn,
        competition_ids=[competition_id],
        payload={
            "run_id": "legacy-run",
            "source_key": "legacy-source",
            "fetched_at": "2026-07-30T15:30:00+00:00",
            "competitions": [],
        },
    )
    conn.close()
    second = client.post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "4150096"},
    ).json()
    assert len(second["results"]) == len(first["results"]) + 1

    conn = sqlite3.connect(migrated_db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "UPDATE applications SET ordinal=9 WHERE competition_id=? AND applicant_id=?",
        (competition_id, applicant_id),
    )
    conn.commit()
    refresh_legacy_campaigns(
        conn,
        competition_ids=[competition_id],
        payload={
            "run_id": "legacy-run",
            "source_key": "legacy-source",
            "fetched_at": "2026-07-30T15:31:00+00:00",
            "competitions": [],
        },
    )
    conn.close()
    repeated = client.post(
        "/api/public/lookup",
        json={"year": 2026, "applicant_id": "4150096"},
    ).json()
    changed = next(
        item
        for item in repeated["results"]
        if item["competition_id"] == competition_id
    )
    assert changed["position"] == 9
