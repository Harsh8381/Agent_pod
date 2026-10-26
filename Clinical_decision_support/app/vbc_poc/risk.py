"""Transparent workload prioritization (not a clinical prediction model).

Each signal adds visible points; the contributing factors are always returned beside the score.
Thresholds are configuration: 0-2 Low, 3-5 Medium, 6+ High.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.vbc_poc.dataset import Event, Patient

RISK_SIGNALS = (
    ("recent_inpatient", "Recent inpatient admission (discharged ≤ 30 days)", 4),
    ("ed_90", "ED visit in last 90 days", 3),
    ("multiple_gaps", "Two or more open measure gaps", 2),
    ("uncontrolled", "Uncontrolled chronic outcome", 3),
    ("age_65", "Age 65 or older", 1),
    ("no_pcp", "No PCP visit in 12 months", 2),
)
CATEGORY_THRESHOLDS = ((6, "High"), (3, "Medium"), (0, "Low"))
RECENT_INPATIENT_DAYS = 30
ED_WINDOW_DAYS = 90
PCP_TYPES = ("pcp_office", "annual_wellness")


def category(score: int) -> str:
    return next(label for threshold, label in CATEGORY_THRESHOLDS if score >= threshold)


def score_patient(patient: Patient, events: list[Event], results: dict, as_of: date) -> dict:
    encounters = [event for event in events if event.event_type == "encounter" and event.event_date and event.event_date <= as_of]
    factors = []

    def hit(key: str, detail: str):
        label, points = next((label, points) for name, label, points in RISK_SIGNALS if name == key)
        factors.append({"signal": key, "label": label, "points": points, "detail": detail})

    inpatient = [event for event in encounters if event.encounter_type == "inpatient" and event.end_date
                 and as_of - timedelta(days=RECENT_INPATIENT_DAYS) <= event.end_date <= as_of]
    if inpatient:
        latest = max(inpatient, key=lambda event: event.end_date)
        hit("recent_inpatient", f"Discharged {latest.end_date:%d %b} ({(as_of - latest.end_date).days} days ago)")
    ed = [event for event in encounters if event.encounter_type == "ed" and (as_of - event.event_date).days <= ED_WINDOW_DAYS]
    if ed:
        hit("ed_90", f"ED visit {max(event.event_date for event in ed):%d %b}")
    open_gaps = [code for code, result in results.items() if result.get("actionable")]
    if len(open_gaps) >= 2:
        hit("multiple_gaps", ", ".join(open_gaps))
    uncontrolled = [f"{code} {result['outcome'].get('value')}" for code, result in results.items() if result["state"] == "OUTCOME_NOT_CONTROLLED"]
    if uncontrolled:
        hit("uncontrolled", "; ".join(uncontrolled))
    age = patient.age_on(as_of)
    if age is not None and age >= 65:
        hit("age_65", f"Age {age}")
    pcp = [event for event in encounters if event.encounter_type in PCP_TYPES and (as_of - event.event_date).days <= 365]
    if not pcp:
        last = max((event.event_date for event in encounters if event.encounter_type in PCP_TYPES), default=None)
        hit("no_pcp", f"Last PCP visit {last:%d %b %Y}" if last else "No PCP visit on record")
    total = sum(factor["points"] for factor in factors)
    return {"score": total, "category": category(total), "factors": factors,
            "disclaimer": "Workload priority from visible rules — not a validated prediction of harm."}
