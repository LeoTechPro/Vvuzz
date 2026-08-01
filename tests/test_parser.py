import argparse

import pytest

from gosuslugi_parser import (
    build_ingest_batch,
    build_parser,
    build_vuznav_ingest_batch,
    finish_v2_ingest,
    read_vuznav_sources,
    post_vuznav_ingest_batch,
    vuznav_batch_source_key,
    vuznav_competition,
    vuznav_request,
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


def test_direct_competition_id_preserves_metadata_even_when_details_mismatch():
    competition = vuznav_competition(
        {
            "competition_id": 138272,
            "organization_id": 0,
            "okso": "",
            "education_level": "",
            "education_form": "",
            "program_query": "",
            "direct_id": True,
        },
        {"applicants": []},
        {"items": [{"id": 999999, "organizationName": "Чужой вуз"}]},
        "2026-07-31T19:00:00+00:00",
    )

    assert competition["preserve_metadata"] is True
    assert competition["university"] == "organization:0"


def test_vuznav_batch_source_key_is_stable_and_order_sensitive():
    competitions = [
        {"source_path": "gosuslugi2026/competition/42/applicants.json"},
        {"source_path": "gosuslugi2026/competition/43/applicants.json"},
    ]

    first = vuznav_batch_source_key("run-1", competitions)
    second = vuznav_batch_source_key("run-1", list(competitions))

    assert first == second
    assert first.startswith("gosuslugi2026/run/run-1/batch/")
    assert first != vuznav_batch_source_key("run-1", list(reversed(competitions)))


def test_vuznav_multi_competition_batch_keeps_v2_contract():
    args = argparse.Namespace(ingest_version="v2", campaign_year=2026)
    competitions = [
        {"source_path": "gosuslugi2026/competition/42/applicants.json", "applications": []},
        {"source_path": "gosuslugi2026/competition/43/applicants.json", "applications": []},
    ]

    batch = build_vuznav_ingest_batch(
        args, "run-1", "2026-07-31T18:00:00+00:00", competitions
    )

    assert batch["competitions"] == competitions
    assert batch["refresh_stats"] is False
    assert batch["finalize_campaign"] is False
    assert batch["source_key"] == vuznav_batch_source_key("run-1", competitions)


def test_vuznav_default_keeps_one_competition_per_ingest_request():
    args = build_parser().parse_args(
        ["vuznav-fetch", "--mode", "raw-only", "--competition-id", "42"]
    )

    assert args.ingest_batch_size == 1
    assert args.ingest_max_bytes == 20 * 1024 * 1024


def test_vuznav_ingest_refuses_payload_above_hard_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "gosuslugi_parser.post_ingest",
        lambda *values: calls.append(values) or {"accepted": True},
    )
    args = argparse.Namespace(
        ingest_version="v2",
        campaign_year=2026,
        ingest_token="secret",
        timeout=30,
        ingest_max_bytes=100,
    )
    competition = {
        "source_path": "gosuslugi2026/competition/42/applicants.json",
        "applications": [{"applicant_id": "x" * 200}],
    }

    with pytest.raises(RuntimeError, match="above --ingest-max-bytes"):
        post_vuznav_ingest_batch(
            args,
            [("ingest", "https://vvuzz.intdata.pro/api/ingest/v2/batch")],
            "run-1",
            "2026-07-31T18:00:00+00:00",
            [competition],
        )

    assert calls == []


def test_vuznav_request_retries_transient_network_error(monkeypatch):
    attempts = []

    class Response:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"{}"

    def urlopen(request, timeout):
        attempts.append((request.full_url, timeout))
        if len(attempts) < 3:
            raise ConnectionResetError("transient reset")
        return Response()

    monkeypatch.setattr("gosuslugi_parser.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("gosuslugi_parser.time.sleep", lambda seconds: None)

    payload, content_type, status = vuznav_request(
        "https://www.gosuslugi.ru/api/example", 30
    )

    assert (payload, content_type, status) == (b"{}", "application/json", 200)
    assert len(attempts) == 3


def test_vuznav_request_does_not_retry_http_error(monkeypatch):
    attempts = []

    def urlopen(request, timeout):
        attempts.append(request.full_url)
        raise __import__("urllib.error").error.HTTPError(
            request.full_url, 429, "rate limited", {}, None
        )

    monkeypatch.setattr("gosuslugi_parser.urllib.request.urlopen", urlopen)

    with pytest.raises(__import__("urllib.error").error.HTTPError):
        vuznav_request("https://www.gosuslugi.ru/api/example", 30)

    assert len(attempts) == 1
