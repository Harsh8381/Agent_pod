"""Population metrics, before/after comparison and monthly trends (section 10 formulas).

measure compliance   = patients meeting numerator / eligible denominator after exclusions
care-gap closure     = gaps closed / gaps open or created during the period
overdue rate         = open gaps past due / all open gaps
outreach success     = outreach that led to a booked or completed action / outreach attempts
data completeness    = evaluations with sufficient evidence / all evaluations of eligible patients
readmission signal   = discharges followed by an inpatient admission within 30 days / eligible discharges
"""

from __future__ import annotations

import calendar
from datetime import date

from app.vbc_poc import engine
from app.vbc_poc.catalog import DISCLAIMER, MEASURE_STATES, MEASURES, PERIOD, POSITIONING, RULE_VERSION
from app.vbc_poc.dataset import PATIENTS, PATIENTS_BY_ID
from app.vbc_poc.service import events_for, latest_results, summarize, today

REVIEW_STATES = ("EVIDENCE_REVIEW", "DATA_QUALITY_ISSUE", "COMPLETED_PENDING_VERIFICATION")
READMISSION_DAYS = 30
_TREND_CACHE: dict = {}


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def readmission_signal(state: dict, as_of: date) -> dict:
    discharges, readmitted, rows = 0, 0, []
    for patient in PATIENTS:
        stays = sorted((event for event in events_for(state, patient.patient_id)
                        if event.event_type == "encounter" and event.encounter_type == "inpatient" and event.end_date), key=lambda e: e.event_date)
        for index, stay in enumerate(stays):
            if not (PERIOD.start <= stay.end_date <= as_of):
                continue
            discharges += 1
            follow = next((other for other in stays[index + 1:] if 0 <= (other.event_date - stay.end_date).days <= READMISSION_DAYS), None)
            if follow:
                readmitted += 1
                rows.append({"patient_id": patient.patient_id, "name": patient.name, "discharge": stay.end_date.isoformat(),
                             "readmitted": follow.event_date.isoformat(), "days": (follow.event_date - stay.end_date).days})
    return {"discharges": discharges, "readmissions": readmitted, "rate": _pct(readmitted, discharges), "rows": rows}


def _month_ends(as_of: date) -> list[date]:
    ends = []
    for month in range(PERIOD.start.month, as_of.month + 1):
        last = date(as_of.year, month, calendar.monthrange(as_of.year, month)[1])
        ends.append(min(last, as_of))
    return ends


def trend(state: dict) -> list[dict]:
    """Re-evaluate every patient as of each month end, using only evidence known by then."""
    as_of = today()
    key = (state.get("revision"), as_of)
    if key in _TREND_CACHE:
        return _TREND_CACHE[key]
    rows, previous = [], set()
    for month_end in _month_ends(as_of):
        met = denominator = 0
        actionable = set()
        per_measure = {code: [0, 0] for code in MEASURES}
        for patient in PATIENTS:
            results = engine.evaluate_patient(patient, events_for(state, patient.patient_id, as_of=month_end), month_end, "trend")
            for code, result in results.items():
                if result["in_denominator"]:
                    denominator += 1
                    per_measure[code][1] += 1
                if result["numerator_met"]:
                    met += 1
                    per_measure[code][0] += 1
                if result["actionable"]:
                    actionable.add((patient.patient_id, code))
        rows.append({"month": month_end.strftime("%b"), "month_end": month_end.isoformat(), "rate": _pct(met, denominator),
                     "open_gaps": len(actionable), "opened": len(actionable - previous), "closed": len(previous - actionable),
                     "measures": {code: _pct(*values) for code, values in per_measure.items()}})
        previous = actionable
    _TREND_CACHE.clear()
    _TREND_CACHE[key] = rows
    return rows


def compute_dashboard(state: dict) -> dict:
    as_of = today()
    results = latest_results(state)
    summary = summarize(state)
    baseline = state.get("baseline") or {"totals": {}, "measures": {}}
    gaps = list(state["gaps"].values())
    open_gaps = [gap for gap in gaps if gap["status"] != "CLOSED"]
    closed = [gap for gap in gaps if gap["status"] == "CLOSED"]
    overdue = [gap for gap in open_gaps if date.fromisoformat(gap["due_date"]) < as_of]
    sent = [item for item in state["outreach"].values() if item["sent_at"]]
    successful = [item for item in sent if item["disposition"] == "SCHEDULED"]
    evaluations = [result for result in results.values() if result["state"] != "NOT_ELIGIBLE"]
    sufficient = [result for result in evaluations if result["state"] not in REVIEW_STATES]
    risk = state.get("risk", {})
    eligible_patients = {pid for (pid, _), result in results.items() if result["in_denominator"]}
    readmissions = readmission_signal(state, as_of)

    scorecards = []
    for code, measure in MEASURES.items():
        row = summary["measures"].get(code, {"denominator": 0, "numerator": 0, "rate": None, "states": {}})
        states = row["states"]
        scorecards.append({
            "measure_id": code, "name": measure.name, "short": measure.short, "target": measure.target,
            "denominator": row["denominator"], "numerator": row["numerator"], "rate": row["rate"],
            "baseline_rate": (baseline["measures"].get(code) or {}).get("rate"),
            "met": states.get("MET", 0), "open": states.get("OPEN_GAP", 0), "outcome": states.get("OUTCOME_NOT_CONTROLLED", 0),
            "review": states.get("EVIDENCE_REVIEW", 0) + states.get("COMPLETED_PENDING_VERIFICATION", 0),
            "data_quality": states.get("DATA_QUALITY_ISSUE", 0), "follow_up": states.get("FOLLOW_UP_REQUIRED", 0),
            "excluded": states.get("EXCLUDED", 0), "not_eligible": states.get("NOT_ELIGIBLE", 0) + states.get("DENOMINATOR_NOT_MET", 0),
        })

    queue = []
    for patient in PATIENTS:
        mine = [gap for gap in open_gaps if gap["patient_id"] == patient.patient_id]
        info = risk.get(patient.patient_id, {"score": 0, "category": "Low", "factors": []})
        if not mine and info["category"] == "Low":
            continue
        overdue_days = max(((as_of - date.fromisoformat(gap["due_date"])).days for gap in mine), default=0)
        queue.append({"patient_id": patient.patient_id, "name": patient.name, "age": patient.age_on(as_of), "risk_score": info["score"],
                      "risk_category": info["category"], "factors": [factor["label"] for factor in info["factors"]], "open_gaps": len(mine),
                      "unassigned": sum(1 for gap in mine if not gap["owner"]), "overdue_days": max(overdue_days, 0),
                      "next_due": min((gap["due_date"] for gap in mine), default=None), "gap_ids": [gap["gap_id"] for gap in mine]})
    queue.sort(key=lambda row: (-row["risk_score"], -row["overdue_days"], -row["open_gaps"]))

    data_quality = []
    for (pid, code), result in sorted(results.items()):
        status = result["data_quality"]["status"]
        if result["state"] == "NOT_ELIGIBLE" or status in ("complete", "not assessed"):
            continue
        gap = next((g for g in open_gaps if g["patient_id"] == pid and g["measure_id"] == code), None)
        data_quality.append({"patient_id": pid, "name": PATIENTS_BY_ID[pid].name, "measure_id": code, "state": result["state"],
                             "status": status, "flags": result["data_quality"]["flags"], "gap_id": gap["gap_id"] if gap else None})

    baseline_states = baseline.get("states", {})
    changes = []
    for (pid, code), result in results.items():
        before = baseline_states.get(f"{pid}|{code}")
        if before and before != result["state"]:
            since = baseline.get("versions", {}).get(f"{pid}|{code}", 0)
            added, removed = [], []
            for version in state["results"][f"{pid}|{code}"]:
                if version["version"] > since:
                    added += version["changed_evidence"]["added"]
                    removed += version["changed_evidence"]["removed"]
            described = {item["event_id"]: f"{item['display']} ({item['date'] or 'undated'}, {item['source_label']})" for item in result["evidence"]}
            evidence = [f"Accepted: {described.get(event_id, event_id)}" for event_id in dict.fromkeys(added) if event_id not in removed]
            evidence += [f"No longer accepted: {described.get(event_id, event_id)}" for event_id in dict.fromkeys(removed) if event_id not in added]
            decided = [f"Reviewer {item['decision']} {event_id}: {item.get('note') or 'no note'}"
                       for event_id, item in state.get("evidence_decisions", {}).items() if event_id.startswith(pid)
                       and event_id in described]
            changes.append({"patient_id": pid, "name": PATIENTS_BY_ID[pid].name, "measure_id": code, "from": before, "to": result["state"],
                            "evidence": evidence + decided,
                            "trigger": result["trigger"], "actor": result["actor"], "at": result["evaluated_at"]})
    history = [entry for gap in gaps for entry in gap["history"]]
    decisions = state.get("evidence_decisions", {})
    activity = {
        "scheduled": sum(1 for entry in history if entry["to"] == "SCHEDULED" and entry["from"] != "SCHEDULED"),
        "completed": sum(1 for entry in history if entry["to"] == "COMPLETED" and entry["from"] != "COMPLETED"),
        "reconciled": sum(1 for event_id in decisions if "-U" not in event_id),
        "verified": sum(1 for item in decisions.values() if item["decision"] == "accepted"),
        "closed": len(closed), "outreach_sent": len(sent),
    }
    totals = summary["totals"]
    return {
        "as_of": as_of.isoformat(), "period": {"id": PERIOD.period_id, "start": PERIOD.start.isoformat(), "end": PERIOD.end.isoformat()},
        "rule_version": RULE_VERSION, "disclaimer": DISCLAIMER, "positioning": POSITIONING,
        "kpis": {
            "patients": len(PATIENTS), "eligible_patients": len(eligible_patients), "open_gaps": len(open_gaps),
            "unassigned_gaps": sum(1 for gap in open_gaps if not gap["owner"]),
            "high_risk_patients": sum(1 for item in risk.values() if item.get("category") == "High"),
            "closure_rate": _pct(len(closed), len(gaps)), "gaps_total": len(gaps), "gaps_closed": len(closed),
            "compliance_rate": totals.get("rate"), "baseline_rate": baseline.get("totals", {}).get("rate"),
            "overdue_rate": _pct(len(overdue), len(open_gaps)), "overdue": len(overdue),
            "outreach_success_rate": _pct(len(successful), len(sent)), "outreach_sent": len(sent),
            "data_completeness_rate": _pct(len(sufficient), len(evaluations)),
            "readmission_rate": readmissions["rate"], "discharges": readmissions["discharges"], "readmissions": readmissions["readmissions"],
        },
        "totals": totals, "baseline": baseline, "scorecards": scorecards, "priority_queue": queue, "data_quality": data_quality,
        "changes": sorted(changes, key=lambda row: row["at"], reverse=True), "activity": activity, "readmissions": readmissions["rows"],
        "trend": trend(state), "state_labels": {code: info.label for code, info in MEASURE_STATES.items()},
    }
