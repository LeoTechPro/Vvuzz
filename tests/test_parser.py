import argparse

from gosuslugi_parser import (
    build_ingest_batch,
    finish_v2_ingest,
    read_vuznav_sources,
    vuznav_competition,
)


def test_build_ingest_v2_batch_has_explicit_campaign_contract():
    batch = build_ingest_batch(
        api_version="v2",
        campaign_year=2026,
        run_id="run-1",
        source="gosuslugi",
        source_key="competition:42",
        snapshot_at="2026-07-31T18:00:00+00:00",
        competitions=[{"source_path": "competition:42", "applications": []}],
    )

    assert batch == {
        "campaign_year": 2026,
        "run_id": "run-1",
        "source_key": "competition:42",
        "snapshot_at": "2026-07-31T18:00:00+00:00",
        "refresh_stats": False,
        "finalize_campaign": False,
        "competitions": [{"source_path": "competition:42", "applications": []}],
    }


def test_finish_v2_ingest_refreshes_active_campaign_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "gosuslugi_parser.post_ingest",
        lambda target, token, batch, timeout: calls.append((target, token, batch, timeout)) or {"accepted": True},
    )
    args = argparse.Namespace(
        ingest_version="v2",
        campaign_year=2026,
        refresh_stats=True,
        finalize_campaign=False,
        ingest_token="secret",
        timeout=30,
    )

    finish_v2_ingest(
        args,
        [("sqlite", "local.sqlite"), ("ingest", "https://vvuzz.intdata.pro/api/ingest/v2/batch")],
        "run-1",
        "2026-07-31T18:05:00+00:00",
    )

    assert len(calls) == 1
    assert calls[0][2]["source_key"] == "run:run-1:complete"
    assert calls[0][2]["competitions"] == []
    assert calls[0][2]["refresh_stats"] is True
    assert calls[0][2]["finalize_campaign"] is False


def test_legacy_batch_remains_available_during_transition():
    batch = build_ingest_batch(
        api_version="legacy",
        campaign_year=2026,
        run_id="run-1",
        source="gosuslugi",
        source_key="competition:42",
        snapshot_at="2026-07-31T18:00:00+00:00",
        competitions=[],
        payload_sha256="abc",
    )

    assert batch["fetched_at"] == "2026-07-31T18:00:00+00:00"
    assert batch["payload_sha256"] == "abc"
    assert "campaign_year" not in batch


def test_competition_id_manifest_is_deduplicated(tmp_path):
    manifest = tmp_path / "competition-ids.txt"
    manifest.write_text("# campaign 2026\n42\n43\n42\n", encoding="utf-8")
    args = argparse.Namespace(
        url=None,
        url_file=None,
        competition_id=[43, 44],
        competition_id_file=manifest,
    )

    sources = read_vuznav_sources(args)

    assert [source["competition_id"] for source in sources] == [43, 44, 42]
    assert all(source["url"].startswith("https://www.gosuslugi.ru/") for source in sources)
    assert all(source["direct_id"] is True for source in sources)


def test_vuznav_competition_preserves_existing_2026_source_identity():
    competition = vuznav_competition(
        {
            "competition_id": 138272,
            "organization_id": 1214,
            "okso": "2.19.03.01",
            "education_level": "2",
            "education_form": "1",
            "program_query": "",
        },
        {"applicants": []},
        {},
        "2026-07-31T19:00:00+00:00",
    )

    assert competition["source_path"] == "gosuslugi2026/competition/138272/applicants.json"
    assert competition["file_name"] == "applicants.json"
