"""Population measure calculator: canonical evidence + a versioned measure specification -> member results.

The specification is data (thresholds, age bands, look-backs, same-day rules), owned by the version
control center; this module only interprets it. Every result names the evidence it used and why.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.hedis import sources

STATE_LABELS = {
    "NOT_ELIGIBLE": "Not Eligible", "EXCLUDED": "Excluded", "MET": "Met", "OPEN_GAP": "Open Care Gap",
    "OUTCOME_NOT_CONTROLLED": "Outcome Not Controlled", "EVIDENCE_REVIEW": "Evidence Review",
    "COMPLETED_PENDING_VERIFICATION": "Completed Pending Verification", "DATA_QUALITY_ISSUE": "Data Quality Issue",
    "FOLLOW_UP_REQUIRED": "Follow-Up Required",
}
LABEL_TO_STATE = {label.lower(): code for code, label in STATE_LABELS.items()}
NUMERATOR_STATES = ("MET", "FOLLOW_UP_REQUIRED")
OUTSIDE_DENOMINATOR = ("NOT_ELIGIBLE", "EXCLUDED")
MEASURE_ORDER = ("GSD", "BPD-E", "KED", "BCS-E", "COL-E", "TRC")


def _months_before(day: date, months: int) -> date:
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, 1)


def _in(record, start: date, end: date) -> bool:
    return bool(record.service_date) and start.isoformat() <= record.service_date <= end.isoformat()


def _result(state: str, reason: str, evidence=(), flags=()) -> dict:
    return {"state": state, "label": STATE_LABELS[state], "reason": reason, "evidence_ids": [e.evidence_id for e in evidence],
            "in_denominator": state not in OUTSIDE_DENOMINATOR, "numerator_met": state in NUMERATOR_STATES, "flags": list(flags)}


def _eligible(spec: dict, member: dict, records: list) -> tuple[bool, str, list]:
    population = spec["population"]
    flags = [member["age_flag"]] if member["age_flag"] else []
    age = member["age"]
    if age is None:
        return False, "Age cannot be determined (no DOB and no enrollment age)", flags
    if population.get("age_min") is not None and age < population["age_min"]:
        return False, f"Age {age} below {population['age_min']}", flags
    if population.get("age_max") is not None and age > population["age_max"]:
        return False, f"Age {age} above {population['age_max']}", flags
    if population.get("sex") and member["sex"] != population["sex"]:
        return False, f"Sex {member['sex']} outside the {population['sex']} population", flags
    if population.get("condition") and not any(r.concept == population["condition"] and r.usable for r in records):
        return False, f"No {population['condition']} diagnosis", flags
    return True, "Meets population criteria", flags


def _gsd(spec, records, start, end):
    numerator = spec["numerator"]
    hba1c = [r for r in records if r.concept == "hba1c"]
    dated = [r for r in hba1c if _in(r, start, end) and r.usable and r.value is not None]
    if not dated:
        if any("MISSING_DATE" in r.flags for r in hba1c):
            return _result("DATA_QUALITY_ISSUE", "Current glycemic result lacks a valid date", [r for r in hba1c if "MISSING_DATE" in r.flags])
        tests = [r for r in records if r.concept == "hba1c_test" and _in(r, start, end)]
        if tests:
            return _result("COMPLETED_PENDING_VERIFICATION", "HbA1c test billed without a result value", tests)
        return _result("OPEN_GAP", "No HbA1c result in the measurement period")
    latest = max(dated, key=lambda r: r.service_date)
    if latest.value < numerator["target_lt"]:
        return _result("MET", f"Latest HbA1c {latest.value:g}% on {latest.service_date} is below {numerator['target_lt']:g}%", [latest])
    return _result("OUTCOME_NOT_CONTROLLED", f"Latest HbA1c {latest.value:g}% on {latest.service_date} is not below {numerator['target_lt']:g}%", [latest])


def _bpd(spec, records, start, end):
    numerator = spec["numerator"]
    readings = [r for r in records if r.concept == "bp" and _in(r, start, end)]
    valid = [r for r in readings if r.usable]
    if not valid:
        if readings:
            return _result("DATA_QUALITY_ISSUE", "Only invalid or implausible blood-pressure readings in the period", readings)
        return _result("OPEN_GAP", "No blood-pressure reading in the measurement period")
    latest_day = max(r.service_date for r in valid)
    same_day = [r for r in valid if r.service_date == latest_day]
    if any(not r.usable for r in readings if r.service_date == latest_day):
        return _result("DATA_QUALITY_ISSUE", f"Invalid blood pressure on {latest_day} requires reconciliation",
                       [r for r in readings if r.service_date == latest_day])
    if len({(r.value, r.value2) for r in same_day}) > 1:
        if numerator["same_day_rule"] == "review":
            return _result("EVIDENCE_REVIEW", f"{len(same_day)} different readings on {latest_day}; same-day rule is human review", same_day)
        systolic, diastolic = min(r.value for r in same_day), min(r.value2 for r in same_day)
        basis = f"lowest systolic {systolic:g} and lowest diastolic {diastolic:g} of {len(same_day)} readings on {latest_day}"
    else:
        systolic, diastolic = same_day[0].value, same_day[0].value2
        basis = f"{systolic:g}/{diastolic:g} on {latest_day}"
    if systolic < numerator["systolic_lt"] and diastolic < numerator["diastolic_lt"]:
        return _result("MET", f"Representative BP {basis} is below {numerator['systolic_lt']}/{numerator['diastolic_lt']}", same_day)
    return _result("OUTCOME_NOT_CONTROLLED", f"Representative BP {basis} is not below {numerator['systolic_lt']}/{numerator['diastolic_lt']}", same_day)


def _ked(spec, records, start, end):
    found, missing = [], []
    for component in spec["numerator"]["components"]:
        hits = [r for r in records if r.concept == component and _in(r, start, end) and r.usable]
        (found.extend(hits[-1:]) if hits else missing.append(component))
    if missing:
        return _result("OPEN_GAP", f"Kidney evaluation incomplete: no {' or '.join(m.upper() for m in missing)} in the period", found)
    return _result("MET", "eGFR and uACR both present in the period", found)


def _bcs(spec, records, start, end):
    numerator = spec["numerator"]
    window_start = _months_before(end, numerator["lookback_months"])
    films = [r for r in records if r.concept == "mammogram" and _in(r, window_start, end) and not ({"NOT_COMPLETED", "FUTURE_DATE"} & set(r.flags))]
    if not films:
        return _result("OPEN_GAP", f"No mammogram since {window_start}")
    latest = max(films, key=lambda r: r.service_date)
    if latest.verification == "verified" or not numerator["report_required"]:
        return _result("MET", f"Mammogram on {latest.service_date} with a verified report", [latest])
    if latest.verification == "pending":
        return _result("EVIDENCE_REVIEW", f"Mammogram on {latest.service_date}: report pending verification", [latest])
    return _result("COMPLETED_PENDING_VERIFICATION", f"Mammogram on {latest.service_date} has no supporting report", [latest])


def _col(spec, records, start, end):
    numerator = spec["numerator"]
    scopes = [r for r in records if r.concept == "colonoscopy" and r.usable and _in(r, date(end.year - numerator["colonoscopy_years"] + 1, 1, 1), end)]
    if scopes:
        latest = max(scopes, key=lambda r: r.service_date)
        return _result("MET", f"Colonoscopy on {latest.service_date} within {numerator['colonoscopy_years']} years", [latest])
    fits = [r for r in records if r.concept == "fit_result" and r.usable and _in(r, start, end)]
    if not fits:
        tests = [r for r in records if r.concept == "fit_test" and _in(r, start, end)]
        if tests:
            return _result("COMPLETED_PENDING_VERIFICATION", "FIT billed without a result", tests)
        return _result("OPEN_GAP", "No FIT in the period and no colonoscopy in the look-back")
    latest = max(fits, key=lambda r: r.service_date)
    if latest.result_flag == "positive" and numerator["positive_fit_requires_followup"]:
        return _result("FOLLOW_UP_REQUIRED", f"Positive FIT on {latest.service_date} requires diagnostic colonoscopy", [latest])
    return _result("MET", f"{latest.result_flag.title()} FIT on {latest.service_date}", [latest])


def _trc(spec, records, start, end):
    numerator = spec["numerator"]
    stays = [r for r in records if r.concept == "inpatient_stay" and _in(r, start, end)]
    stay = max(stays, key=lambda r: r.service_date)
    admitted = date.fromisoformat(stay.service_date)
    used, missing = [stay], []
    if numerator["discharge_summary_required"]:
        summaries = [r for r in records if r.concept == "discharge_summary" and r.service_date and r.service_date >= stay.service_date]
        (used.extend(summaries[:1]) if summaries else missing.append("discharge summary"))
    follow = sorted((r for r in records if r.concept == "outpatient_visit" and r.usable and r.service_date
                     and stay.service_date < r.service_date <= (admitted + timedelta(days=numerator["followup_days"])).isoformat()),
                    key=lambda r: r.service_date)
    (used.append(follow[0]) if follow else missing.append(f"outpatient follow-up within {numerator['followup_days']} days"))
    if missing:
        return _result("OPEN_GAP", f"Transition after admission {stay.service_date} incomplete: missing {', '.join(missing)}", used)
    return _result("MET", f"Discharge summary and follow-up on {follow[0].service_date} after admission {stay.service_date}", used)


RULES = {"GSD": _gsd, "BPD-E": _bpd, "KED": _ked, "BCS-E": _bcs, "COL-E": _col, "TRC": _trc}


def calculate(spec: dict, normalized: dict, demographics: dict) -> dict:
    """{(patient_id, measure_id): result} for every member and every measure in the specification."""
    start, end = sources.period()
    results = {}
    for patient_id, member in demographics.items():
        records = normalized["by_patient"].get(patient_id, [])
        for measure_id in MEASURE_ORDER:
            measure = spec["measures"].get(measure_id)
            if not measure:
                continue
            eligible, why, flags = _eligible(measure, member, records)
            if eligible and measure["population"].get("encounter"):
                eligible = any(r.concept == measure["population"]["encounter"] and _in(r, start, end) for r in records)
                why = "Meets population criteria" if eligible else f"No {measure['population']['encounter'].replace('_', ' ')} in the period"
            if not eligible:
                result = _result("NOT_ELIGIBLE", why, flags=flags)
            else:
                result = RULES[measure_id](measure, records, start, end)
                result["flags"] += flags
            results[(patient_id, measure_id)] = result
    return results


def rates(results: dict) -> dict:
    out = {}
    for (_, measure_id), result in results.items():
        entry = out.setdefault(measure_id, {"denominator": 0, "numerator": 0, "states": {}})
        entry["states"][result["state"]] = entry["states"].get(result["state"], 0) + 1
        entry["denominator"] += result["in_denominator"]
        entry["numerator"] += result["numerator_met"]
    for entry in out.values():
        entry["rate"] = round(entry["numerator"] / entry["denominator"] * 100, 1) if entry["denominator"] else None
    return out
