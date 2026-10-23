"""Read-only, synthetic VBC population dashboard calculations.

The 80% measure target, performance bands, 20-point systolic/10-point diastolic
same-day blood-pressure conflict threshold, and freshness windows are POC
conventions only. They are not official HEDIS or CMS specifications.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from app.vbc.dataset import VbcDatasetRepository


MEASURE_LABEL = "HEDIS-inspired POC measures"
POC_TARGET_RATE = 80.0
EXCLUDED_MEASURE_STATES = {
    "not eligible",
    "excluded",
    "denominator not met",
}
TERMINAL_GAP_STATUSES = {"verified", "closed"}
INCOMPLETE_DOCUMENT_STATUSES = {
    "pending verification",
    "unverified",
    "missing",
    "rejected",
}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
FRESHNESS_DAYS = {"current": 7, "aging": 30}

DATE_FIELDS = {
    "encounters.csv": "date",
    "observations.csv": "date",
    "claims.csv": "service_date",
    "documents.csv": "document_date",
    "outreach.csv": "date",
}
ADDITIONAL_DATASET_DATE_FIELDS = (
    ("conditions.csv", "onset_date"),
    ("procedures.csv", "service_date"),
    ("care_gaps.csv", "due_date"),
    ("care_plans.csv", "target_date"),
    ("audit_trail.csv", "event_timestamp"),
)


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def _display_category(value: Any) -> str:
    return str(value).strip() if value is not None and str(value).strip() else "Unknown"


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        for date_format in ("%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, date_format).date()
            except ValueError:
                continue
    return None


def _as_of_date(
    supplied: date | str | None,
    summary: dict[str, Any],
    dated_rows: list[tuple[str, dict[str, str]]],
    warnings: list[str],
) -> date:
    if supplied is not None:
        parsed = _parse_date(supplied)
        if parsed is None:
            raise ValueError("as_of_date must be a valid date.")
        return parsed

    summary_date = _parse_date(summary.get("as_of_date"))
    if summary_date is not None:
        return summary_date

    valid_dates = [
        parsed
        for field, row in dated_rows
        if (parsed := _parse_date(row.get(field))) is not None
    ]
    if valid_dates:
        warnings.append(
            "Population summary as-of date was unavailable; using the latest "
            "valid date in the VBC dataset."
        )
        return max(valid_dates)

    warnings.append(
        "No valid dataset date was available; the machine date is used as a "
        "last-resort POC reference."
    )
    return date.today()


def _measurement_period(
    dated_rows: list[tuple[str, dict[str, str]]],
) -> tuple[str | None, str | None]:
    valid_dates = [
        parsed
        for field, row in dated_rows
        if (parsed := _parse_date(row.get(field))) is not None
    ]
    if not valid_dates:
        return None, None
    return min(valid_dates).isoformat(), max(valid_dates).isoformat()


def _configured_target_rate(warnings: list[str]) -> float:
    raw_value = os.getenv("VBC_POC_TARGET_RATE")
    if raw_value is None:
        return POC_TARGET_RATE
    try:
        value = float(raw_value)
    except ValueError:
        warnings.append(
            "VBC_POC_TARGET_RATE is invalid; the 80% POC target is being used."
        )
        return POC_TARGET_RATE
    if not math.isfinite(value) or not 0 <= value <= 100:
        warnings.append(
            "VBC_POC_TARGET_RATE must be between 0 and 100; the 80% POC target "
            "is being used."
        )
        return POC_TARGET_RATE
    return value


def _mask_name(patient: dict[str, str]) -> str:
    first_name = str(patient.get("first_name") or "").strip()
    last_name = str(patient.get("last_name") or "").strip()
    if not first_name and not last_name:
        return "Unknown"
    first_initial = f"{first_name[0].upper()}." if first_name else ""
    if not last_name:
        return f"{first_initial} Unknown".strip()
    masked_surname = f"{last_name[0].upper()}****"
    return f"{first_initial} {masked_surname}".strip()


def _risk_data(
    patients: list[dict[str, str]],
    risk_scores: list[dict[str, str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], set[str]]:
    patient_by_id = {row.get("patient_id", ""): row for row in patients}
    score_by_id: dict[str, dict[str, Any]] = {}
    malformed_factors: set[str] = set()
    for row in risk_scores:
        patient_id = row.get("patient_id", "")
        if not patient_id:
            continue
        try:
            score = float(row.get("risk_score", ""))
            if not math.isfinite(score):
                score = None
        except (TypeError, ValueError):
            score = None
        factors_raw = row.get("contributing_factors", "")
        if isinstance(factors_raw, list):
            factors = factors_raw
        elif isinstance(factors_raw, str) and factors_raw.strip():
            try:
                decoded = json.loads(factors_raw)
            except json.JSONDecodeError:
                factors = []
                malformed_factors.add(patient_id)
            else:
                factors = decoded if isinstance(decoded, list) else []
                if not isinstance(decoded, list):
                    malformed_factors.add(patient_id)
        else:
            factors = []
        score_by_id[patient_id] = {
            "risk_score": score,
            "risk_category": str(row.get("risk_category") or "").strip()
            or str(patient_by_id.get(patient_id, {}).get("risk_category") or "").strip(),
            "contributing_factors": factors,
        }

    for patient_id, patient in patient_by_id.items():
        score_by_id.setdefault(
            patient_id,
            {
                "risk_score": None,
                "risk_category": str(patient.get("risk_category") or "").strip(),
                "contributing_factors": [],
            },
        )

    risk_categories = {
        patient_id: str(details.get("risk_category") or "").strip()
        for patient_id, details in score_by_id.items()
    }
    return score_by_id, risk_categories, malformed_factors


def _counter_distribution(
    rows: list[dict[str, str]],
    key: str,
) -> dict[str, int]:
    counts = Counter(_display_category(row.get(key)) for row in rows)
    return dict(sorted(counts.items(), key=lambda item: item[0].casefold()))


def _percentage(numerator: int, denominator: int) -> float:
    return numerator / denominator * 100 if denominator else 0.0


def _measure_status(rate: float, eligible_count: int) -> str:
    if eligible_count == 0:
        return "No Eligible Population"
    if rate >= 80:
        return "On Track"
    if rate >= 70:
        return "Close"
    if rate >= 60:
        return "Watch"
    return "At Risk"


def _date_quality(
    rows: list[dict[str, str]],
    field: str,
) -> tuple[date | None, int, int]:
    parsed_dates = []
    invalid_count = 0
    missing_count = 0
    for row in rows:
        raw_value = row.get(field)
        if not str(raw_value or "").strip():
            missing_count += 1
            continue
        parsed = _parse_date(raw_value)
        if parsed is None:
            invalid_count += 1
        else:
            parsed_dates.append(parsed)
    return (max(parsed_dates) if parsed_dates else None, invalid_count, missing_count)


def _freshness_summary(
    repository: VbcDatasetRepository,
    as_of: date,
    patient_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    source_getters = (
        ("encounters", repository.get_encounters, "date"),
        ("observations", repository.get_observations, "date"),
        ("claims", repository.get_claims, "service_date"),
        ("documents", repository.get_documents, "document_date"),
        ("outreach", repository.get_outreach, "date"),
    )
    freshness = []
    for source_name, getter, date_field in source_getters:
        rows = getter()
        if patient_ids is not None:
            rows = [row for row in rows if row.get("patient_id") in patient_ids]
        latest, invalid_count, missing_count = _date_quality(rows, date_field)
        age_days = (as_of - latest).days if latest is not None else None
        if latest is None:
            status = "Unknown"
        elif latest > as_of:
            status = "Future Dated"
        elif age_days <= FRESHNESS_DAYS["current"]:
            status = "Current"
        elif age_days <= FRESHNESS_DAYS["aging"]:
            status = "Aging"
        else:
            status = "Stale"
        freshness.append(
            {
                "source_name": source_name,
                "latest_valid_date": latest.isoformat() if latest else None,
                "age_days": age_days,
                "status": status,
                "invalid_date_count": invalid_count,
                "missing_date_count": missing_count,
            }
        )
    return freshness


def _parse_bp(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    parts = text.split("/")
    if len(parts) != 2:
        return None
    try:
        systolic, diastolic = (int(part.strip()) for part in parts)
    except ValueError:
        return None
    return systolic, diastolic


def _blood_pressure_quality(
    observations: list[dict[str, str]],
) -> tuple[int, int]:
    invalid_count = 0
    readings_by_patient_day: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for row in observations:
        if "blood pressure" not in _normalized(row.get("type")) and _normalized(row.get("type")) != "bp":
            continue
        reading = _parse_bp(row.get("value"))
        if reading is None:
            invalid_count += 1
            continue
        systolic, diastolic = reading
        if (
            systolic < 40
            or systolic > 300
            or diastolic < 20
            or diastolic > 200
            or systolic <= diastolic
        ):
            invalid_count += 1
        parsed_day = _parse_date(row.get("date"))
        patient_id = row.get("patient_id", "")
        if parsed_day is not None and patient_id:
            readings_by_patient_day[(patient_id, parsed_day.isoformat())].append(reading)

    conflicting_groups = 0
    for readings in readings_by_patient_day.values():
        has_material_conflict = any(
            abs(first[0] - second[0]) >= 20
            or abs(first[1] - second[1]) >= 10
            for index, first in enumerate(readings)
            for second in readings[index + 1 :]
        )
        if has_material_conflict:
            conflicting_groups += 1
    return invalid_count, conflicting_groups


def _duplicate_laboratory_count(observations: list[dict[str, str]]) -> int:
    signatures = [
        (
            row.get("patient_id", ""),
            row.get("type", ""),
            row.get("value", ""),
            row.get("unit", ""),
            row.get("date", ""),
        )
        for row in observations
        if _normalized(row.get("type")) != "blood pressure"
        and _normalized(row.get("type")) != "bp"
    ]
    return sum(count - 1 for count in Counter(signatures).values() if count > 1)


def _data_quality_summary(
    patients: list[dict[str, str]],
    observations: list[dict[str, str]],
    documents: list[dict[str, str]],
    care_gaps: list[dict[str, str]],
    patients_by_id: dict[str, dict[str, str]],
    providers: list[dict[str, str]],
    encounters: list[dict[str, str]],
    patient_scoped_rows: list[list[dict[str, str]]],
    warnings: list[str],
) -> dict[str, int]:
    missing_dob = sum(not str(row.get("dob") or "").strip() for row in patients)
    missing_observation_dates = sum(
        not str(row.get("date") or "").strip() for row in observations
    )
    invalid_bp_count, conflicting_bp_groups = _blood_pressure_quality(observations)
    duplicate_lab_count = _duplicate_laboratory_count(observations)
    pending_verification = sum(
        _normalized(row.get("verification_status")) == "pending verification"
        for row in documents
    )
    missing_due_date_count = sum(
        not str(row.get("due_date") or "").strip() for row in care_gaps
    )
    invalid_due_date_count = sum(
        bool(str(row.get("due_date") or "").strip())
        and _parse_date(row.get("due_date")) is None
        for row in care_gaps
    )
    malformed_due_dates = missing_due_date_count + invalid_due_date_count
    if malformed_due_dates:
        warnings.append(
            f"{malformed_due_dates} selected care gap(s) have a missing or "
            "malformed due date; they remain visible and are not counted overdue."
        )

    known_provider_ids = {row.get("provider_id", "") for row in providers}
    unresolved_provider_references = sum(
        bool(row.get("attributed_provider_id"))
        and row.get("attributed_provider_id") not in known_provider_ids
        for row in patients
    )
    unresolved_provider_references += sum(
        bool(row.get("provider_id")) and row.get("provider_id") not in known_provider_ids
        for row in encounters
    )

    known_patient_ids = set(patients_by_id)
    unresolved_patient_references = sum(
        bool(row.get("patient_id")) and row.get("patient_id") not in known_patient_ids
        for rows in patient_scoped_rows
        for row in rows
    )

    return {
        "missing_dob": missing_dob,
        "missing_observation_dates": missing_observation_dates,
        "invalid_bp_values": invalid_bp_count,
        "conflicting_same_day_bp_readings": conflicting_bp_groups,
        "duplicate_laboratory_results": duplicate_lab_count,
        "pending_document_verification": pending_verification,
        "documents_not_verified": sum(
            _normalized(row.get("verification_status")) != "verified"
            for row in documents
        ),
        "documents_requiring_verification": pending_verification,
        "malformed_due_dates": malformed_due_dates,
        "missing_due_dates": missing_due_date_count,
        "invalid_due_dates": invalid_due_date_count,
        "unresolved_provider_references": unresolved_provider_references,
        "unresolved_patient_references": unresolved_patient_references,
    }


def _worklist(
    gaps: list[dict[str, str]],
    patients_by_id: dict[str, dict[str, str]],
    risk_by_id: dict[str, dict[str, Any]],
    providers_by_id: dict[str, dict[str, str]],
    as_of: date,
    measure_names_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for gap in gaps:
        patient_id = gap.get("patient_id", "")
        if patient_id in patients_by_id:
            grouped[patient_id].append(gap)

    rows = []
    for patient_id, patient_gaps in grouped.items():
        patient = patients_by_id[patient_id]
        risk = risk_by_id.get(patient_id, {})
        priorities = [_display_category(gap.get("priority")) for gap in patient_gaps]
        highest_priority = min(
            priorities,
            key=lambda value: PRIORITY_RANK.get(_normalized(value), 3),
            default="Unknown",
        )
        due_dates = [
            parsed
            for gap in patient_gaps
            if (parsed := _parse_date(gap.get("due_date"))) is not None
        ]
        overdue_count = sum(
            _parse_date(gap.get("due_date")) is not None
            and _parse_date(gap.get("due_date")) < as_of
            and _normalized(gap.get("status")) not in TERMINAL_GAP_STATUSES
            for gap in patient_gaps
        )
        provider_id = str(patient.get("attributed_provider_id") or "")
        measure_ids = sorted(
            {str(gap.get("measure_id") or "").strip() for gap in patient_gaps if gap.get("measure_id")}
        )
        measure_names = sorted(
            {
                measure_names_by_id.get(measure_value, measure_value)
                for measure_value in measure_ids
            }
        )
        rows.append(
            {
                "patient_id": patient_id,
                "masked_display_name": _mask_name(patient),
                "cohort": _display_category(patient.get("cohort")),
                "attributed_provider_id": provider_id or "Unknown",
                "provider_name": str(
                    providers_by_id.get(provider_id, {}).get("provider_name") or "Unknown"
                ),
                "risk_score": risk.get("risk_score"),
                "risk_category": _display_category(risk.get("risk_category")),
                "contributing_factors": risk.get("contributing_factors", []),
                "care_gap_count": len(patient_gaps),
                "high_priority_gap_count": sum(
                    _normalized(gap.get("priority")) == "high" for gap in patient_gaps
                ),
                "highest_priority": highest_priority or "Unknown",
                "measure_ids": measure_ids,
                "measure_names": measure_names,
                "owners": sorted(
                    {_display_category(gap.get("owner")) for gap in patient_gaps},
                    key=str.casefold,
                ),
                "earliest_due_date": min(due_dates).isoformat() if due_dates else None,
                "overdue_gap_count": overdue_count,
                "current_statuses": sorted(
                    {_display_category(gap.get("status")) for gap in patient_gaps},
                    key=str.casefold,
                ),
                "_risk_rank": PRIORITY_RANK.get(
                    _normalized(risk.get("risk_category")), 3
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            row["_risk_rank"],
            -row["high_priority_gap_count"],
            -row["care_gap_count"],
            row["earliest_due_date"] or "9999-12-31",
            row["patient_id"],
        )
    )
    for row in rows:
        row.pop("_risk_rank", None)
    return rows


def _dashboard(
    repository: VbcDatasetRepository,
    *,
    as_of_date: date | str | None,
    cohort: str | None,
    provider_id: str | None,
    risk_category: str | None,
    measure_id: str | None,
    gap_type: str | None,
    priority: str | None,
    owner: str | None,
    status: str | None,
) -> dict[str, Any]:
    patients_all = repository.get_patients()
    providers_all = repository.get_providers()
    encounters_all = repository.get_encounters()
    conditions_all = repository.get_conditions()
    observations_all = repository.get_observations()
    procedures_all = repository.get_procedures()
    claims_all = repository.get_claims()
    documents_all = repository.get_documents()
    measure_results_all = repository.get_measure_results()
    care_gaps_all = repository.get_care_gaps()
    risk_scores_all = repository.get_risk_scores()
    care_plans_all = repository.get_care_plans()
    outreach_all = repository.get_outreach()
    audit_trail_all = repository.get_audit_trail()
    summary = repository.get_population_summary()
    warnings: list[str] = []

    all_dated_rows = [
        (field, row)
        for member_name, field in DATE_FIELDS.items()
        for row in {
            "encounters.csv": encounters_all,
            "observations.csv": observations_all,
            "claims.csv": claims_all,
            "documents.csv": documents_all,
            "outreach.csv": outreach_all,
        }[member_name]
    ]
    additional_date_sources = {
        "conditions.csv": conditions_all,
        "procedures.csv": procedures_all,
        "care_gaps.csv": care_gaps_all,
        "care_plans.csv": care_plans_all,
        "audit_trail.csv": audit_trail_all,
    }
    as_of_dated_rows = all_dated_rows + [
        (field, row)
        for member_name, field in ADDITIONAL_DATASET_DATE_FIELDS
        for row in additional_date_sources[member_name]
    ]
    resolved_as_of = _as_of_date(as_of_date, summary, as_of_dated_rows, warnings)
    period_start, period_end = _measurement_period(all_dated_rows)
    target_rate = _configured_target_rate(warnings)

    patients_by_id_all = {
        str(row.get("patient_id") or ""): row
        for row in patients_all
        if row.get("patient_id")
    }
    risk_by_id_all, risk_categories, malformed_factor_patients = _risk_data(
        patients_all, risk_scores_all
    )
    if malformed_factor_patients:
        warnings.append(
            f"{len(malformed_factor_patients)} risk-score record(s) have malformed "
            "contributing_factors; those factors are omitted from the worklist."
        )

    filtered_patients = list(patients_all)
    if cohort is not None:
        filtered_patients = [
            row for row in filtered_patients if row.get("cohort") == cohort
        ]
    if provider_id is not None:
        filtered_patients = [
            row
            for row in filtered_patients
            if row.get("attributed_provider_id") == provider_id
        ]
    if risk_category is not None:
        filtered_patients = [
            row
            for row in filtered_patients
            if risk_categories.get(str(row.get("patient_id") or "")) == risk_category
            or row.get("risk_category") == risk_category
        ]

    filtered_patient_ids = {
        str(row.get("patient_id") or "") for row in filtered_patients
    }
    filtered_patient_ids.discard("")
    patients_by_id = {
        patient_id: patients_by_id_all[patient_id]
        for patient_id in filtered_patient_ids
        if patient_id in patients_by_id_all
    }
    risk_by_id = {
        patient_id: risk_by_id_all.get(patient_id, {})
        for patient_id in filtered_patient_ids
    }
    providers_by_id = {
        str(row.get("provider_id") or ""): row for row in providers_all
    }

    filtered_measure_results = [
        row
        for row in measure_results_all
        if row.get("patient_id") in filtered_patient_ids
        and (measure_id is None or row.get("measure_id") == measure_id)
    ]
    filtered_gaps = [
        row
        for row in care_gaps_all
        if row.get("patient_id") in filtered_patient_ids
        and (measure_id is None or row.get("measure_id") == measure_id)
        and (gap_type is None or row.get("gap_type") == gap_type)
        and (priority is None or row.get("priority") == priority)
        and (owner is None or row.get("owner") == owner)
        and (status is None or row.get("status") == status)
    ]
    filtered_observations = [
        row
        for row in observations_all
        if row.get("patient_id") in filtered_patient_ids
    ]
    filtered_documents = [
        row for row in documents_all if row.get("patient_id") in filtered_patient_ids
    ]
    filtered_encounters = [
        row for row in encounters_all if row.get("patient_id") in filtered_patient_ids
    ]

    eligible_results = [
        row
        for row in filtered_measure_results
        if _normalized(row.get("state")) not in EXCLUDED_MEASURE_STATES
    ]
    met_results = [
        row for row in filtered_measure_results if _normalized(row.get("state")) == "met"
    ]
    open_gaps = [
        row
        for row in filtered_gaps
        if _normalized(row.get("status")) not in TERMINAL_GAP_STATUSES
    ]

    verification_rows = filtered_documents
    verified_documents = sum(
        _normalized(row.get("verification_status")) == "verified"
        for row in verification_rows
    )
    incomplete_documents = len(verification_rows) - verified_documents
    if not verification_rows:
        evidence_completeness = 100.0
        warnings.append(
            "No documents require verification for this population; evidence "
            "completeness is reported as 100% by POC convention."
        )
    else:
        evidence_completeness = _percentage(
            verified_documents, len(verification_rows)
        )
    if not eligible_results:
        warnings.append(
            "No eligible measure evaluations are available for the selected "
            "population; quality performance is reported as 0%."
        )

    measure_ids = sorted(
        {row.get("measure_id", "") for row in measure_results_all if row.get("measure_id")}
    )
    if measure_id is not None:
        measure_ids = [item for item in measure_ids if item == measure_id]
    measure_names = {
        row.get("measure_id", ""): _display_category(row.get("measure_name"))
        for row in measure_results_all
        if row.get("measure_id")
    }

    measure_performance = []
    for current_measure_id in measure_ids:
        measure_results = [
            row
            for row in filtered_measure_results
            if row.get("measure_id") == current_measure_id
        ]
        measure_eligible = [
            row
            for row in measure_results
            if _normalized(row.get("state")) not in EXCLUDED_MEASURE_STATES
        ]
        measure_met = sum(
            _normalized(row.get("state")) == "met" for row in measure_results
        )
        measure_gaps = [
            row
            for row in open_gaps
            if row.get("measure_id") == current_measure_id
        ]
        state_counts = Counter(_normalized(row.get("state")) for row in measure_results)
        eligible_count = len(measure_eligible)
        performance_rate = _percentage(measure_met, eligible_count)
        required_met_count = math.ceil(eligible_count * target_rate / 100)
        measure_performance.append(
            {
                "measure_id": current_measure_id,
                "measure_name": measure_names.get(current_measure_id, current_measure_id),
                "total_evaluations": len(measure_results),
                "eligible_count": eligible_count,
                "met_count": measure_met,
                "open_gap_count": len(measure_gaps),
                "uncontrolled_count": state_counts["outcome not controlled"],
                "evidence_review_count": (
                    state_counts["evidence review"]
                    + state_counts["completed pending verification"]
                ),
                "data_quality_count": state_counts["data quality issue"],
                "follow_up_required_count": state_counts["follow-up required"],
                "current_performance_rate": performance_rate,
                "poc_target_rate": target_rate,
                "required_met_count": required_met_count,
                "gaps_to_goal": max(required_met_count - measure_met, 0)
                if eligible_count
                else 0,
                "status": _measure_status(performance_rate, eligible_count),
            }
        )

    gap_distribution = {
        "by_gap_type": _counter_distribution(filtered_gaps, "gap_type"),
        "by_priority": _counter_distribution(filtered_gaps, "priority"),
        "by_status": _counter_distribution(filtered_gaps, "status"),
        "by_owner": _counter_distribution(filtered_gaps, "owner"),
        "by_measure_id": _counter_distribution(filtered_gaps, "measure_id"),
    }

    worklist = _worklist(
        filtered_gaps,
        patients_by_id_all,
        risk_by_id,
        providers_by_id,
        resolved_as_of,
        measure_names,
    )
    # Data-quality checks are scoped to the selected cohort/provider/risk and
    # gap filters, while observation/document source counts remain population-wide
    # within those selected patients.
    selected_rows_by_patient = [
        conditions_all,
        observations_all,
        procedures_all,
        claims_all,
        documents_all,
        measure_results_all,
        care_gaps_all,
        risk_scores_all,
        care_plans_all,
        outreach_all,
        audit_trail_all,
    ]
    data_quality = _data_quality_summary(
        filtered_patients,
        filtered_observations,
        filtered_documents,
        filtered_gaps,
        patients_by_id,
        providers_all,
        filtered_encounters,
        selected_rows_by_patient,
        warnings,
    )
    data_quality["evidence_review_cases"] = sum(
        _normalized(row.get("state"))
        in {"evidence review", "completed pending verification"}
        for row in filtered_measure_results
    )
    data_quality["data_quality_cases"] = sum(
        _normalized(row.get("state")) == "data quality issue"
        for row in filtered_measure_results
    )

    risk_counts = Counter(
        _display_category(risk_by_id.get(patient_id, {}).get("risk_category"))
        for patient_id in filtered_patient_ids
    )
    high_risk_patients = sum(
        _normalized(risk_by_id.get(patient_id, {}).get("risk_category")) == "high"
        or _normalized(patients_by_id[patient_id].get("risk_category")) == "high"
        for patient_id in filtered_patient_ids
        if patient_id in patients_by_id
    )
    outcome_management_cases = sum(
        _normalized(row.get("gap_type")) == "outcome management"
        for row in filtered_gaps
    )
    open_gap_count = len(open_gaps)
    kpis = {
        "total_patients": len(filtered_patient_ids),
        "eligible_evaluations": len(eligible_results),
        "met_evaluations": len(met_results),
        "quality_performance_rate": _percentage(
            len(met_results), len(eligible_results)
        ),
        "open_actionable_care_gaps": open_gap_count,
        "evidence_completeness_rate": evidence_completeness,
        "verified_documents": verified_documents,
        "documents_requiring_verification": len(verification_rows),
        "pending_verification_documents": incomplete_documents,
        "high_risk_patients": high_risk_patients,
        "data_quality_cases": data_quality["data_quality_cases"],
        "evidence_review_cases": data_quality["evidence_review_cases"],
        "outcome_management_cases": outcome_management_cases,
    }

    freshness = _freshness_summary(
        repository, resolved_as_of, patient_ids=filtered_patient_ids
    )
    active_filters = {
        "as_of_date": resolved_as_of.isoformat(),
        "cohort": cohort,
        "provider_id": provider_id,
        "risk_category": risk_category,
        "measure_id": measure_id,
        "gap_type": gap_type,
        "priority": priority,
        "owner": owner,
        "status": status,
    }
    return {
        "metadata": {
            "synthetic_data_notice": str(
                summary.get("synthetic_data_notice")
                or "Synthetic VBC POC data. HEDIS-inspired measures. Not for "
                "clinical care or official quality reporting."
            ),
            "data_source": "data/synthetic_vbc_dataset.zip",
            "as_of_date": resolved_as_of.isoformat(),
            "measurement_period_start": period_start,
            "measurement_period_end": period_end,
            "measure_label": MEASURE_LABEL,
            "official_reporting": False,
            "evidence_completeness_method": (
                "Verified documents divided by all document records in the "
                "selected population; 100% when no document records exist."
            ),
            "poc_target_rate": target_rate,
            "poc_status_thresholds": {
                "On Track": ">= 80%",
                "Close": ">= 70% and < 80%",
                "Watch": ">= 60% and < 70%",
                "At Risk": "< 60%",
            },
        },
        "active_filters": active_filters,
        "kpis": kpis,
        "cohort_distribution": _counter_distribution(filtered_patients, "cohort"),
        "risk_distribution": dict(
            sorted(risk_counts.items(), key=lambda item: item[0].casefold())
        ),
        "measure_performance": measure_performance,
        "gap_distribution": gap_distribution,
        "priority_worklist": worklist,
        "data_quality_summary": data_quality,
        "freshness_summary": freshness,
        "warnings": list(dict.fromkeys(warnings)),
    }


def get_population_dashboard(
    as_of_date: date | str | None = None,
    cohort: str | None = None,
    provider_id: str | None = None,
    risk_category: str | None = None,
    measure_id: str | None = None,
    gap_type: str | None = None,
    priority: str | None = None,
    owner: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Return filtered aggregate metrics and a masked patient worklist.

    All filters use exact values. Patient-level filters constrain the selected
    cohort, provider, and risk population; measure and gap filters are then
    applied to their corresponding evaluations and care-gap records.
    """
    repository = VbcDatasetRepository()
    return _dashboard(
        repository,
        as_of_date=as_of_date,
        cohort=cohort,
        provider_id=provider_id,
        risk_category=risk_category,
        measure_id=measure_id,
        gap_type=gap_type,
        priority=priority,
        owner=owner,
        status=status,
    )
