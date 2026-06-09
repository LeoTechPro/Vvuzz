"""Admission chance helper for Vvuzz Online.

The formula is an educational heuristic over public admission list data. It is
not a legal prediction or an enrollment guarantee.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChanceResult:
    chance_percent: float | None
    chance_label: str
    projected_rank: float | None
    effective_blockers: float
    above_consent_here: int
    above_consent_elsewhere: int
    above_no_consent: int
    explanation: str


EXPLANATION = (
    "Это эвристическая оценка по текущим публичным данным, не гарантия "
    "зачисления. Люди выше с согласием здесь учитываются с весом 1.0, "
    "с согласием в другом конкурсе - 0.15, без найденного согласия - 0.55."
)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def chance_label(chance_percent: float | None) -> str:
    if chance_percent is None:
        return "неизвестно"
    if chance_percent >= 75:
        return "высокий шанс"
    if chance_percent >= 40:
        return "средний шанс"
    return "низкий шанс"


def calculate_chance(
    seats: int | None,
    above_consent_here: int,
    above_consent_elsewhere: int,
    above_no_consent: int,
) -> ChanceResult:
    effective_blockers = (
        1.0 * max(above_consent_here, 0)
        + 0.15 * max(above_consent_elsewhere, 0)
        + 0.55 * max(above_no_consent, 0)
    )
    projected_rank = 1 + effective_blockers
    chance_percent = None
    if seats and seats > 0:
        chance_percent = round(clamp((seats - effective_blockers) / seats * 100), 1)
    return ChanceResult(
        chance_percent=chance_percent,
        chance_label=chance_label(chance_percent),
        projected_rank=round(projected_rank, 1),
        effective_blockers=round(effective_blockers, 1),
        above_consent_here=max(above_consent_here, 0),
        above_consent_elsewhere=max(above_consent_elsewhere, 0),
        above_no_consent=max(above_no_consent, 0),
        explanation=EXPLANATION,
    )
