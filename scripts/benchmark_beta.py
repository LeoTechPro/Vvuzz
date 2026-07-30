#!/usr/bin/env python3
"""Read-only HTTP acceptance benchmark for an approved Vvuzz beta contour."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HttpResult:
    status: int
    header_seconds: float
    total_seconds: float
    payload: dict[str, Any]


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile_value * len(ordered)))
    return ordered[rank - 1]


def validated_ids(path: Path) -> list[str]:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if not value.isascii() or not value.isdigit() or not 4 <= len(value) <= 20:
            raise ValueError("IDs file contains an invalid value")
        values.append(value)
    if not values:
        raise ValueError("IDs file must contain at least one ID")
    return values


def endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def sanitized_base_url(base_url: str) -> str:
    parts = urllib.parse.urlsplit(base_url)
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parts.port}" if parts.port is not None else ""
    return urllib.parse.urlunsplit(
        (parts.scheme, f"{hostname}{port}", parts.path.rstrip("/"), "", "")
    )


def http_json(
    base_url: str,
    *,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float,
) -> HttpResult:
    encoded = (
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if body is not None
        else None
    )
    headers = {
        "Accept": "application/json",
        "User-Agent": "vvuzz-beta-acceptance/1",
    }
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        endpoint(base_url, path),
        data=encoded,
        headers=headers,
        method=method,
    )
    started = time.perf_counter()
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        header_seconds = time.perf_counter() - started
        with response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        header_seconds = time.perf_counter() - started
        raw = exc.read()
        status = exc.code
    total_seconds = time.perf_counter() - started
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return HttpResult(
        status=status,
        header_seconds=header_seconds,
        total_seconds=total_seconds,
        payload=payload if isinstance(payload, dict) else {},
    )


def public_lookup_page(
    base_url: str,
    *,
    year: int,
    applicant_id: str,
    offset: int,
    timeout: float,
) -> tuple[HttpResult, HttpResult | None]:
    lookup = http_json(
        base_url,
        method="POST",
        path="/api/public/lookup",
        body={"year": year, "applicant_id": applicant_id, "offset": offset},
        timeout=timeout,
    )
    if lookup.status != 200:
        return lookup, None
    ids = [
        item.get("competition_id")
        for item in lookup.payload.get("results", [])
        if isinstance(item, dict) and item.get("competition_id") is not None
    ]
    if not ids:
        return lookup, None
    details = http_json(
        base_url,
        method="POST",
        path="/api/public/competition",
        body={
            "year": year,
            "applicant_id": applicant_id,
            "competition_ids": ids,
        },
        timeout=timeout,
    )
    return lookup, details


def first_screen_seconds(
    base_url: str,
    *,
    year: int,
    applicant_id: str,
    timeout: float,
) -> tuple[float, bool]:
    started = time.perf_counter()
    lookup, details = public_lookup_page(
        base_url,
        year=year,
        applicant_id=applicant_id,
        offset=0,
        timeout=timeout,
    )
    ok = lookup.status == 200 and (details is None or details.status == 200)
    return time.perf_counter() - started, ok


def full_result_seconds(
    base_url: str,
    *,
    year: int,
    applicant_id: str,
    timeout: float,
) -> tuple[float, bool, int]:
    started = time.perf_counter()
    offset = 0
    result_count = 0
    ok = True
    while True:
        lookup, details = public_lookup_page(
            base_url,
            year=year,
            applicant_id=applicant_id,
            offset=offset,
            timeout=timeout,
        )
        if lookup.status != 200 or (details is not None and details.status != 200):
            ok = False
            break
        result_count += len(lookup.payload.get("results", []))
        next_offset = lookup.payload.get("next_offset")
        if next_offset is None:
            break
        offset = int(next_offset)
    return time.perf_counter() - started, ok, result_count


def metric(values: list[float]) -> dict[str, float | int | None]:
    return {
        "samples": len(values),
        "min_seconds": min(values) if values else None,
        "median_seconds": statistics.median(values) if values else None,
        "p95_seconds": percentile(values, 0.95),
        "max_seconds": max(values) if values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run read-only HTTP performance checks after explicit beta approval. "
            "Raw IDs are read from a local file and never printed."
        )
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--ids-file", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--mode",
        choices=("performance", "parallel", "all"),
        default="all",
        help=(
            "performance: latency thresholds only; parallel: public 25-client "
            "check only; all: both for a direct beta upstream"
        ),
    )
    args = parser.parse_args()
    if args.samples < 1 or args.concurrency < 1:
        parser.error("samples and concurrency must be positive")

    applicant_ids = validated_ids(args.ids_file)
    errors: list[str] = []
    parallel_errors: list[str] = []

    parallel_durations: list[float] = []
    if args.mode in {"parallel", "all"}:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.concurrency
        ) as executor:
            futures = [
                executor.submit(
                    first_screen_seconds,
                    args.base_url,
                    year=args.year,
                    applicant_id=applicant_ids[index % len(applicant_ids)],
                    timeout=args.timeout,
                )
                for index in range(args.concurrency)
            ]
            for future in futures:
                try:
                    duration, ok = future.result()
                    parallel_durations.append(duration)
                    if not ok:
                        parallel_errors.append("parallel_search_http_error")
                except Exception as exc:  # bounded summary; no request data
                    parallel_errors.append(type(exc).__name__)
        errors.extend(parallel_errors)

    homepage_headers = []
    archive_totals = []
    first_screen_totals = []
    extreme_duration: float | None = None
    extreme_ok: bool | None = None
    extreme_results: int | None = None
    if args.mode in {"performance", "all"}:
        for index in range(args.samples):
            home = http_json(
                args.base_url,
                method="GET",
                path="/",
                timeout=args.timeout,
            )
            if home.status == 200:
                homepage_headers.append(home.header_seconds)
            else:
                errors.append("homepage_http_error")

            archive = http_json(
                args.base_url,
                method="GET",
                path=f"/api/public/archive/{args.year}/overview",
                timeout=args.timeout,
            )
            if archive.status == 200:
                archive_totals.append(archive.total_seconds)
            else:
                errors.append("archive_http_error")

            try:
                duration, ok = first_screen_seconds(
                    args.base_url,
                    year=args.year,
                    applicant_id=applicant_ids[index % len(applicant_ids)],
                    timeout=args.timeout,
                )
                first_screen_totals.append(duration)
                if not ok:
                    errors.append("first_screen_http_error")
            except Exception as exc:
                errors.append(type(exc).__name__)

        extreme_duration, extreme_ok, extreme_results = full_result_seconds(
            args.base_url,
            year=args.year,
            applicant_id=applicant_ids[-1],
            timeout=args.timeout,
        )
        if not extreme_ok:
            errors.append("extreme_result_http_error")

    report = {
        "base_url": sanitized_base_url(args.base_url),
        "mode": args.mode,
        "campaign_year": args.year,
        "id_count": len(applicant_ids),
        "raw_ids_emitted": False,
        "homepage_ttfb": metric(homepage_headers),
        "first_screen": metric(first_screen_totals),
        "archive_overview": metric(archive_totals),
        "parallel_first_screen": {
            **metric(parallel_durations),
            "concurrency": args.concurrency,
            "errors": len(parallel_errors),
        },
        "extreme_full_result": {
            "seconds": extreme_duration,
            "results": extreme_results,
            "ok": extreme_ok,
        },
    }
    thresholds: dict[str, bool] = {}
    if args.mode in {"performance", "all"}:
        thresholds.update(
            {
                "homepage_ttfb_le_0_3": (
                    percentile(homepage_headers, 0.95) or float("inf")
                )
                <= 0.3,
                "first_screen_p95_le_1_5": (
                    percentile(first_screen_totals, 0.95) or float("inf")
                )
                <= 1.5,
                "archive_p95_le_0_5": (
                    percentile(archive_totals, 0.95) or float("inf")
                )
                <= 0.5,
                "extreme_full_le_5": (
                    extreme_ok is True
                    and extreme_duration is not None
                    and extreme_duration <= 5.0
                ),
            }
        )
    if args.mode in {"parallel", "all"}:
        thresholds["parallel_without_errors"] = (
            not parallel_errors and len(parallel_durations) == args.concurrency
        )
    report["thresholds"] = thresholds
    report["error_count"] = len(errors)
    report["passed"] = not errors and all(thresholds.values())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
