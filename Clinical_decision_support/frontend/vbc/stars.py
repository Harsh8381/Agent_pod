"""Star projections and explainable gap ranking.

Cut points are configuration (estimated), never hard-coded truths. Ranking is lexicographic:
clinical urgency first, then days left, closure likelihood and one-action closures; Star weight
is only ever the last tie-breaker (guardrail: "let Star weight win" is never allowed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from frontend.vbc.catalog import DASHBOARD_MEASURES, MEASURES, MP, WORKABLE_STATUSES
from frontend.vbc.engine import Evaluation, MeasureResult, Rate

# One action can close several gaps for the same person.
ACTION_GROUPS = {
    "GSD": "lab draw", "KED": "lab draw",
    "CBP": "visit", "COA-FSA": "visit",
    "EED": "eye referral",
    "BCS-E": "screening outreach", "COL-E": "screening outreach",
    "SPD": "medication review", "AIS-E": "vaccine", "TRC": "transition", "FMC": "follow-up",
}


def star_level(code: str, value: float | None) -> int | None:
    measure = MEASURES[code]
    if value is None or not measure.cut_points:
        return None
    if measure.direction == "lower":
        return 1 + sum(value <= cut for cut in measure.cut_points)
    return 1 + sum(value >= cut for cut in measure.cut_points)


def next_cut(code: str, value: float | None) -> float | None:
    measure = MEASURES[code]
    if value is None or not measure.cut_points:
        return None
    if measure.direction == "lower":
        return next((cut for cut in measure.cut_points if value > cut), None)
    return next((cut for cut in measure.cut_points if value < cut), None)


def closures_needed(code: str, rate: Rate) -> int | None:
    target = next_cut(code, rate.value)
    if target is None or MEASURES[code].direction == "lower" or not rate.denominator:
        return 0 if target is None else None
    return max(0, math.ceil(target / 100 * rate.denominator - rate.numerator))


def projected_value(code: str, rate: Rate, evaluation: Evaluation, person_ids: set | None = None) -> float | None:
    """Current rate plus expected closures: booked visits, pending evidence, orders in flight."""
    if MEASURES[code].direction == "lower" or not rate.denominator:
        return rate.value
    if code in ("TRC", "FMC"):
        return rate.value
    expected = sum(
        result.likelihood for result in evaluation.for_measure(code)
        if result.status in WORKABLE_STATUSES and (person_ids is None or result.person_id in person_ids)
    )
    return round(min(100.0, (rate.numerator + expected) / rate.denominator * 100), 1)


def measure_status(code: str, rate: Rate, projected: float | None) -> str:
    if rate.value is None:
        return "No data"
    if MEASURES[code].direction == "lower":
        return "Watch" if rate.value > 1.0 else "On track"
    target = next_cut(code, rate.value)
    if target is None:
        return "On track"
    if projected is not None and projected >= target:
        return "On track"
    if projected is not None and target - projected <= 3:
        return "Close"
    return "At risk"


def overall_stars(levels: dict[str, int | None]) -> float | None:
    weighted = [(MEASURES[code].star_weight, level) for code, level in levels.items() if level and MEASURES[code].rated]
    total = sum(weight for weight, _ in weighted)
    if not total:
        return None
    return round(sum(weight * level for weight, level in weighted) / total * 2) / 2


def scorecard(rates: dict[str, Rate], evaluation: Evaluation, person_ids: set | None = None) -> list[dict]:
    rows = []
    for code in DASHBOARD_MEASURES:
        rate = rates[code]
        projected = projected_value(code, rate, evaluation, person_ids)
        rows.append({
            "code": code,
            "measure": MEASURES[code],
            "rate": rate,
            "current": rate.value,
            "projected": projected,
            "next_cut": next_cut(code, rate.value),
            "gaps_to_goal": closures_needed(code, rate),
            "level": star_level(code, rate.value),
            "projected_level": star_level(code, projected),
            "status": measure_status(code, rate, projected),
        })
    return rows


# ---------------------------------------------------------------------------
# Gap ranking
# ---------------------------------------------------------------------------


@dataclass
class RankedPerson:
    person_id: str
    gaps: list = field(default_factory=list)  # MeasureResults
    urgency: float = 0.0
    urgency_band: str = "Low"
    days_left: int = 0
    likelihood: float = 0.0
    one_action: int = 0
    star_weight: int = 0
    rank: int = 0

    @property
    def priority_label(self) -> str:
        return f"{self.rank} · {self.urgency_band}"

    @property
    def lead(self) -> MeasureResult:
        return max(self.gaps, key=lambda result: (result.urgency, MEASURES[result.measure].star_weight))


def urgency_band(score: float) -> str:
    return "High" if score >= 0.75 else "Med" if score >= 0.45 else "Low"


def rank_gaps(results: list[MeasureResult], as_of: date) -> list[RankedPerson]:
    by_person: dict[str, RankedPerson] = {}
    for result in results:
        if result.status not in WORKABLE_STATUSES:
            continue
        by_person.setdefault(result.person_id, RankedPerson(result.person_id)).gaps.append(result)
    days_left = max((MP.end - as_of).days, 0)
    for item in by_person.values():
        item.urgency = max(result.urgency for result in item.gaps)
        item.urgency_band = urgency_band(item.urgency)
        item.days_left = days_left
        for result in item.gaps:
            for case in result.cases or []:
                due = getattr(case, "due", None) or (case.stay.discharge if hasattr(case, "stay") and case.stay.discharge else None)
                if due:
                    item.days_left = min(item.days_left, max((due - as_of).days, 0))
        item.likelihood = round(max(result.likelihood for result in item.gaps), 2)
        groups = {}
        for result in item.gaps:
            if result.status == "GAP_OPEN":
                group = ACTION_GROUPS.get(result.measure)
                groups[group] = groups.get(group, 0) + 1
        item.one_action = max(groups.values(), default=0)
        item.star_weight = max(MEASURES[result.measure].star_weight for result in item.gaps)
    band_order = {"High": 0, "Med": 1, "Low": 2}
    ranked = sorted(
        by_person.values(),
        key=lambda item: (band_order[item.urgency_band], item.days_left, -item.likelihood, -item.one_action, -item.star_weight),
    )
    for position, item in enumerate(ranked, start=1):
        item.rank = position
    return ranked
