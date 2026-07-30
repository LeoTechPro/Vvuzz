from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from chance import calculate_chance

from .security import digest


def as_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def classify(chance_percent: float | None, effective_blockers: float, seats: int) -> tuple[str, str]:
    margin = seats - effective_blockers
    if chance_percent is not None and margin >= max(3, seats * 0.15):
        return "stable", "Устойчивое положение"
    if chance_percent is not None and margin >= -max(2, seats * 0.1):
        return "borderline", "Пограничная ситуация"
    return "risk", "Высокий риск"


@dataclass
class LookupCache:
    key: bytes
    max_size: int = 256

    def __post_init__(self) -> None:
        self._values: OrderedDict[str, dict] = OrderedDict()
        self._lock = Lock()

    def make_key(self, year: int, applicant_id: str, snapshot_marker: str) -> str:
        return digest(f"{year}:{applicant_id}:{snapshot_marker}", self.key)

    def get(self, key: str) -> dict | None:
        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._values.move_to_end(key)
            return value

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > self.max_size:
                self._values.popitem(last=False)


def campaigns(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id, c.year, c.name, c.status, c.final_snapshot_id,
               s.competition_count, s.application_count, s.applicant_count,
               s.institution_count, s.program_count, s.freshness_at, s.computed_at
        FROM campaigns c
        LEFT JOIN campaign_stats s ON s.campaign_id = c.id
        ORDER BY c.year DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def campaign_for_year(conn: sqlite3.Connection, year: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT c.*, s.freshness_at, s.computed_at, s.snapshot_id
        FROM campaigns c
        LEFT JOIN campaign_stats s ON s.campaign_id = c.id
        WHERE c.year = ?
        """,
        (year,),
    ).fetchone()


def archive_overview(conn: sqlite3.Connection, year: int) -> dict | None:
    row = conn.execute(
        """
        SELECT c.year, c.name, c.status, s.*
        FROM campaigns c
        LEFT JOIN campaign_stats s ON s.campaign_id = c.id
        WHERE c.year = ?
        """,
        (year,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["institutions"] = json.loads(result.pop("institutions_json") or "[]")
    result["programs"] = json.loads(result.pop("programs_json") or "[]")
    return result


def lookup(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    year: int,
    applicant_id: str,
    freshness: str,
    limit: int,
) -> dict:
    rows = conn.execute(
        """
        WITH target AS (
          SELECT
            a.competition_id,
            a.ordinal,
            a.competition_priority,
            a.consent_text,
            a.has_consent,
            a.score_total,
            a.status
          FROM applicants ap
          JOIN applications a ON a.applicant_id = ap.id
          JOIN competitions_core c ON c.id = a.competition_id
          WHERE ap.applicant_id = ? AND c.campaign_id = ?
          ORDER BY a.ordinal
          LIMIT ?
        )
        SELECT
          t.*, c.seats, c.row_count, c.study_form, c.quota_type,
          c.education_level, c.city, u.name AS university, p.name AS program,
          count(above.id) AS total_before,
          coalesce(sum(CASE WHEN above.has_consent = 1 THEN 1 ELSE 0 END), 0) AS consent_here,
          coalesce(sum(CASE
            WHEN above.has_consent = 0 AND EXISTS (
              SELECT 1
              FROM applications other
              JOIN competitions_core other_competition
                ON other_competition.id = other.competition_id
              WHERE other.applicant_id = above.applicant_id
                AND other.competition_id <> above.competition_id
                AND other.has_consent = 1
                AND other_competition.campaign_id = c.campaign_id
            ) THEN 1 ELSE 0 END), 0) AS consent_elsewhere
        FROM target t
        JOIN competitions_core c ON c.id = t.competition_id
        LEFT JOIN universities u ON u.id = c.university_id
        LEFT JOIN programs p ON p.id = c.program_id
        LEFT JOIN applications above
          ON above.competition_id = t.competition_id
         AND above.ordinal < t.ordinal
        GROUP BY t.competition_id
        ORDER BY t.ordinal
        """,
        (applicant_id, campaign_id, limit),
    ).fetchall()
    items = []
    for row in rows:
        total_before = as_int(row["total_before"])
        consent_here = as_int(row["consent_here"])
        consent_elsewhere = as_int(row["consent_elsewhere"])
        no_consent = max(total_before - consent_here - consent_elsewhere, 0)
        chance = calculate_chance(row["seats"], consent_here, consent_elsewhere, no_consent)
        seats = as_int(row["seats"])
        risk, risk_label = classify(chance.chance_percent, chance.effective_blockers, seats)
        items.append(
            {
                "competition_id": row["competition_id"],
                "university": row["university"] or "Вуз не указан",
                "program": row["program"] or "Программа не указана",
                "quota_type": row["quota_type"] or "Не указана",
                "study_form": row["study_form"] or "Не указана",
                "education_level": row["education_level"] or "",
                "city": row["city"] or "",
                "position": row["ordinal"],
                "places": row["seats"],
                "priority": row["competition_priority"],
                "score": row["score_total"],
                "status": row["status"] or "",
                "consent_here": consent_here,
                "consent_elsewhere": consent_elsewhere,
                "no_consent": no_consent,
                "effective_competitors": chance.effective_blockers,
                "projected_position": chance.projected_rank,
                "chance_percent": chance.chance_percent,
                "risk": risk,
                "risk_label": risk_label,
                "formula": (
                    f"1,0 × {consent_here} + 0,15 × {consent_elsewhere} + "
                    f"0,55 × {no_consent} = {chance.effective_blockers}"
                ),
            }
        )
    order = {"stable": 0, "borderline": 1, "risk": 2}
    items.sort(key=lambda item: (order[item["risk"]], item["position"] or 10**9))
    return {
        "found": bool(items),
        "campaign_year": year,
        "freshness_at": freshness,
        "results": items,
        "truncated": len(items) >= limit,
        "warning": "Оценка основана на последнем снимке и не гарантирует поступление.",
    }


def competition_details(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    applicant_id: str,
    competition_ids: list[int],
) -> list[dict]:
    if not competition_ids:
        return []
    placeholders = ",".join("?" for _ in competition_ids)
    rows = conn.execute(
        f"""
        SELECT c.id, u.name AS university, p.name AS program, c.seats,
               a.ordinal AS position, a.competition_priority AS priority
        FROM applicants ap
        JOIN applications a ON a.applicant_id = ap.id
        JOIN competitions_core c ON c.id = a.competition_id
        LEFT JOIN universities u ON u.id = c.university_id
        LEFT JOIN programs p ON p.id = c.program_id
        WHERE ap.applicant_id = ?
          AND c.campaign_id = ?
          AND c.id IN ({placeholders})
        ORDER BY a.ordinal
        """,
        (applicant_id, campaign_id, *competition_ids),
    ).fetchall()
    return [dict(row) for row in rows]
