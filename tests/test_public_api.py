from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from vvuzz_public.config import Settings
from vvuzz_public.main import create_app


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
    assert payload["results"][0]["consent_here"] == 1
    assert payload["results"][0]["consent_elsewhere"] == 1
    assert payload["results"][0]["no_consent"] == 1
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
    result = response.json()["results"][0]
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
