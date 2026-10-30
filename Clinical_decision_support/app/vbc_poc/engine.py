"""Measure engine: the generic eleven-stage evaluation model, deterministic and fully traced.

Measurement context -> initial population -> denominator -> exclusions -> evidence normalization ->
evidence validity -> numerator/service -> outcome -> data quality -> actionability -> persistence.
Each stage writes a trace entry, so any result can be reproduced and explained later.
A completed test and a controlled outcome are always separate questions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from app.vbc_poc.catalog import (
    BP_TARGET,
    COMPLETED_PENDING_VERIFICATION,
    DATA_QUALITY_ISSUE,
    DENOMINATOR_NOT_MET,
    DUE_DAYS,
    ENROLLMENT_ALLOWED_GAP_DAYS,
    EVIDENCE_REVIEW,
    EXCLUDED,
    EXCLUSION_CONCEPTS,
    FOLLOW_UP_REQUIRED,
    GLYCEMIC_TARGET,
    MEASURE_STATES,
    MEASURES,
    MET,
    NOT_ELIGIBLE,
    OPEN_GAP,
    OUTCOME_NOT_CONTROLLED,
    OWNER_ROLES,
    PERIOD,
    RULE_VERSION,
    SOURCES,
    STATE_GAP_TYPE,
    Measure,
)
from app.vbc_poc.dataset import Event, Patient
from app.vbc_poc.evidence import Evidence, assess, concept_of, normalize


def default_as_of(today: date | None = None) -> date:
    today = today or date.today()
    return min(max(today, PERIOD.start), PERIOD.end)


def enrollment_gaps(patient: Patient, as_of: date) -> list[tuple[date, date]]:
    """Uncovered spans within the measurement period up to the evaluation date."""
    end = min(as_of, PERIOD.end)
    spans = sorted((max(start, PERIOD.start), min(stop, end)) for start, stop in patient.enrollment if stop >= PERIOD.start and start <= end)
    gaps, cursor = [], PERIOD.start
    for start, stop in spans:
        if start > cursor:
            gaps.append((cursor, start - timedelta(days=1)))
        cursor = max(cursor, stop + timedelta(days=1))
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def _fmt(day: date | None) -> str:
    return day.strftime("%d %b %Y") if day else "undated"


class _Trace:
    def __init__(self):
        self.steps: list[dict] = []

    def add(self, stage: int, name: str, decision: str, detail: str, evidence: list | None = None) -> None:
        self.steps.append({"stage": stage, "name": name, "decision": decision, "detail": detail, "evidence": evidence or []})


def evaluate_measure(patient: Patient, events: list[Event], measure: Measure, as_of: date, run_id: str = "adhoc") -> dict:
    trace = _Trace()
    trace.add(1, "Measurement context", "fixed", f"{measure.measure_id} · rule {RULE_VERSION} · {PERIOD.period_id} "
              f"({_fmt(PERIOD.start)} – {_fmt(PERIOD.end)}) · evaluated as of {_fmt(as_of)} · run {run_id}")
    known = [event for event in events if event.recorded_at <= as_of]

    # Stage 2 — initial population
    age = patient.age_on(PERIOD.end)
    reasons = []
    if age is None:
        reasons.append("date of birth missing")
    elif not measure.age[0] <= age <= measure.age[1]:
        reasons.append(f"age {age} outside {measure.age[0]}–{measure.age[1]}")
    if measure.sex and patient.sex != measure.sex:
        reasons.append("measure applies to female patients")
    condition_events = []
    if measure.requires_condition:
        condition_events = [event for event in known if event.event_type == "condition" and concept_of(event) == measure.requires_condition
                            and event.verification == "verified" and (event.event_date or as_of) <= as_of]
        if not condition_events:
            reasons.append(f"no {measure.requires_condition} diagnosis on record")
    if reasons:
        trace.add(2, "Initial population", "not met", "; ".join(reasons).capitalize())
        return _result(patient, measure, as_of, run_id, NOT_ELIGIBLE, "Not eligible: " + "; ".join(reasons), trace, [],
                       population={"met": False, "reason": "; ".join(reasons)})
    population_reason = f"Age {age}" + (f", {measure.requires_condition} diagnosis {condition_events[0].code}" if condition_events else "") + (
        ", female" if measure.sex else "")
    trace.add(2, "Initial population", "met", population_reason, [event.event_id for event in condition_events])

    # Stage 3 — denominator (continuous enrollment and attribution)
    gaps = enrollment_gaps(patient, as_of)
    longest = max(((stop - start).days + 1 for start, stop in gaps), default=0)
    denominator_reasons = []
    if len(gaps) > 1 or longest > ENROLLMENT_ALLOWED_GAP_DAYS:
        denominator_reasons.append(f"enrollment gap of {longest} days (allowed: one gap up to {ENROLLMENT_ALLOWED_GAP_DAYS})")
    if not patient.attributed_provider or patient.status != "active":
        denominator_reasons.append("no active attribution")
    if denominator_reasons:
        detail = "; ".join(denominator_reasons)
        trace.add(3, "Denominator", "not met", detail.capitalize())
        return _result(patient, measure, as_of, run_id, DENOMINATOR_NOT_MET, "Denominator not met: " + detail, trace, [],
                       population={"met": True, "reason": population_reason}, denominator={"met": False, "reason": detail})
    trace.add(3, "Denominator", "met", f"Continuously enrolled; attributed to {patient.attributed_provider} · {patient.program}")

    # Stage 4 — exclusions (verified, structured, in the right timeframe)
    exclusion = None
    for event in known:
        concept = concept_of(event)
        if concept not in measure.exclusions or event.source == "narrative" or event.verification != "verified":
            continue
        if not event.event_date or event.event_date > as_of:
            continue
        if concept in ("hospice", "palliative_care") and event.event_date < PERIOD.start:
            continue
        exclusion = (concept, event)
        break
    if exclusion:
        concept, event = exclusion
        text = f"{EXCLUSION_CONCEPTS[concept]} ({event.display}, {_fmt(event.event_date)})"
        trace.add(4, "Exclusion review", "excluded", text, [event.event_id])
        return _result(patient, measure, as_of, run_id, EXCLUDED, "Excluded: " + text, trace, [],
                       population={"met": True, "reason": population_reason}, denominator={"met": True, "reason": "Enrolled and attributed"},
                       exclusion={"applied": True, "concept": concept, "reason": text, "evidence_ids": [event.event_id]})
    trace.add(4, "Exclusion review", "none", "No verified exclusion applies")

    # Stages 5-6 — normalization and validity
    concepts = measure.evidence_concepts + {"BCS-P": ("breast_ultrasound",)}.get(measure.measure_id, ())
    items = [assess(item, measure, patient, as_of) for item in normalize(known, concepts)]
    duplicates = [item for item in items if item.verdict == "duplicate"]
    trace.add(5, "Evidence normalization", f"{len(items)} candidate(s)",
              f"{len(items) - len(duplicates)} distinct record(s); {len(duplicates)} duplicate(s) linked to a primary record",
              [item.event.event_id for item in items])
    accepted = [item for item in items if item.verdict == "accepted"]
    review = [item for item in items if item.verdict == "review"]
    invalid = [item for item in items if item.verdict == "invalid"]
    trace.add(6, "Evidence validity", f"{len(accepted)} accepted · {len(review)} review · {len(invalid)} invalid",
              "; ".join(f"{item.event.event_id}: {item.reason}" for item in items if item.verdict != "accepted") or "All candidates accepted",
              [item.event.event_id for item in accepted])

    # Stages 7-8 — numerator/service and outcome
    numerator = {"met": False, "evidence_id": None, "date": None}
    outcome = {"status": "not applicable", "value": None, "date": None, "target": measure.target}
    conflict: list[Evidence] = []
    state = None
    if accepted:
        if measure.kind == "outcome":
            latest_day = max(item.event.event_date for item in accepted)
            same_day = [item for item in accepted if item.event.event_date == latest_day]
            numerator = {"met": True, "evidence_id": same_day[0].event.event_id, "date": latest_day.isoformat()}
            trace.add(7, "Numerator / service", "met", f"{measure.short}: assessment on record {_fmt(latest_day)}",
                      [item.event.event_id for item in same_day])
            state, conflict = _outcome(measure, same_day, outcome, trace)
        else:
            chosen = max(accepted, key=lambda item: item.event.event_date)
            numerator = {"met": True, "evidence_id": chosen.event.event_id, "date": chosen.event.event_date.isoformat()}
            trace.add(7, "Numerator / service", "met", f"{chosen.event.display} on {_fmt(chosen.event.event_date)} ({chosen.event.source})",
                      [chosen.event.event_id])
            state = MET
            positive = [item for item in accepted if item.event.result_flag == "positive" and item.concept in ("fit", "sdna_fit")]
            if positive:
                latest_positive = max(item.event.event_date for item in positive)
                followed = [item for item in accepted if item.concept == "colonoscopy" and item.event.event_date >= latest_positive]
                if not followed:
                    state = FOLLOW_UP_REQUIRED
                    outcome.update(status="follow-up required", date=latest_positive.isoformat(),
                                   target="Diagnostic colonoscopy after a positive stool test")
                    trace.add(8, "Outcome assessment", "follow-up required",
                              f"Positive stool test {_fmt(latest_positive)}; no diagnostic colonoscopy since", [positive[0].event.event_id])
            if state == MET:
                trace.add(8, "Outcome assessment", "not applicable", "Screening measure; service evidence is sufficient")
    else:
        trace.add(7, "Numerator / service", "not met" if not review else "uncertain",
                  "No accepted evidence in the permitted window" + (" — candidate evidence needs review" if review else ""))
        trace.add(8, "Outcome assessment", "not applicable", "No accepted result to assess")
        uploads = [item for item in review if item.event.source == "workflow_upload"]
        state = (COMPLETED_PENDING_VERIFICATION if uploads else EVIDENCE_REVIEW if review else DATA_QUALITY_ISSUE if invalid else OPEN_GAP)

    # Stage 9 — data quality
    flags = []
    if invalid:
        flags.append(f"{len(invalid)} invalid record(s): " + ", ".join(sorted({item.reason for item in invalid})))
    if conflict:
        flags.append("conflicting same-day results without source precedence")
    if review:
        flags.append(f"{len(review)} record(s) pending review")
    if duplicates:
        flags.append(f"{len(duplicates)} duplicate(s) resolved")
    quality = ("conflicting" if conflict else "invalid" if invalid and not accepted else "incomplete" if (invalid or review) else "complete")
    trace.add(9, "Data quality", quality, "; ".join(flags) or "Complete — no missing, conflicting or invalid records",
              [item.event.event_id for item in invalid + review])

    # Stage 10 — actionability
    gap_type = "FOLLOW_UP" if state == FOLLOW_UP_REQUIRED else STATE_GAP_TYPE.get(state)
    action = None
    if gap_type:
        orders = [event for event in known if event.event_type == "order" and concept_of(event) in measure.evidence_concepts]
        action = {"gap_type": gap_type, "action": measure.actions.get(gap_type, "Review"), "owner_role": OWNER_ROLES[gap_type],
                  "due_days": DUE_DAYS[gap_type]}
        if orders and gap_type == "CLINICAL":
            latest_order = max(orders, key=lambda event: event.event_date or date.min)
            action["action"] = f"Confirm completion of {latest_order.display.lower()} ({_fmt(latest_order.event_date)})" + (
                f" — {latest_order.note}" if latest_order.note else "")
        trace.add(10, "Actionability", gap_type, f"{action['action']} · owner role {action['owner_role']} · due in {action['due_days']} days")
    else:
        trace.add(10, "Actionability", "none", "No operational work required")
    reason = _reason(measure, state, accepted, review, invalid, items, outcome)
    return _result(patient, measure, as_of, run_id, state, reason, trace, items,
                   population={"met": True, "reason": population_reason}, denominator={"met": True, "reason": "Enrolled and attributed"},
                   numerator=numerator, outcome=outcome, quality={"status": quality, "flags": flags}, action=action)


def _outcome(measure: Measure, same_day: list[Evidence], outcome: dict, trace: _Trace) -> tuple[str, list]:
    day = same_day[0].event.event_date
    if measure.measure_id == "GSD-P":
        values = {item.event.value for item in same_day if item.event.value is not None}
        if len(values) > 1:
            best_rank = min(_rank_of(item) for item in same_day)
            top = [item for item in same_day if _rank_of(item) == best_rank]
            if len({item.event.value for item in top}) > 1:
                trace.add(8, "Outcome assessment", "conflicting",
                          f"Same-day HbA1c values disagree ({', '.join(f'{v:g}%' for v in sorted(values))}) with equal source precedence",
                          [item.event.event_id for item in top])
                outcome.update(status="conflicting", date=day.isoformat())
                return EVIDENCE_REVIEW, top
            same_day = top
        value = same_day[0].event.value
        controlled = value < GLYCEMIC_TARGET
        outcome.update(status="controlled" if controlled else "not controlled", value=f"{value:g}%", date=day.isoformat())
        trace.add(8, "Outcome assessment", outcome["status"], f"Latest HbA1c {value:g}% on {_fmt(day)}; target < {GLYCEMIC_TARGET:g}%",
                  [same_day[0].event.event_id])
        return (MET if controlled else OUTCOME_NOT_CONTROLLED), []
    systolic = min(item.event.value for item in same_day)
    diastolic = min(item.event.value2 for item in same_day)
    controlled = systolic < BP_TARGET[0] and diastolic < BP_TARGET[1]
    note = f" (lowest systolic and lowest diastolic of {len(same_day)} same-day readings)" if len(same_day) > 1 else ""
    outcome.update(status="controlled" if controlled else "not controlled", value=f"{systolic:g}/{diastolic:g}", date=day.isoformat())
    trace.add(8, "Outcome assessment", outcome["status"],
              f"Latest acceptable BP {systolic:g}/{diastolic:g} on {_fmt(day)}{note}; target < {BP_TARGET[0]}/{BP_TARGET[1]}",
              [item.event.event_id for item in same_day])
    return (MET if controlled else OUTCOME_NOT_CONTROLLED), []


def _rank_of(item: Evidence) -> int:
    return SOURCES.get(item.event.source, ("", 9))[1]


def _reason(measure: Measure, state: str, accepted, review, invalid, items, outcome) -> str:
    if state == MET:
        if measure.kind == "outcome":
            return f"{measure.short} met: latest result {outcome['value']} on {outcome['date']} is within target"
        latest = max(accepted, key=lambda item: item.event.event_date)
        return f"{latest.event.display} on {_fmt(latest.event.event_date)} satisfies the rule"
    if state == OUTCOME_NOT_CONTROLLED:
        return f"Assessment done, but latest result {outcome['value']} ({outcome['date']}) is outside target {outcome['target'].split(' ', 2)[-1]}"
    if state == FOLLOW_UP_REQUIRED:
        return "Screening done (positive stool test) — diagnostic colonoscopy still required"
    if state == EVIDENCE_REVIEW:
        if outcome.get("status") == "conflicting":
            return "Same-day results disagree; a reviewer must decide which is correct"
        first = review[0]
        return f"Candidate evidence needs review: {first.event.display} — {first.reason.lower()}"
    if state == COMPLETED_PENDING_VERIFICATION:
        return "Completion evidence received; awaiting verification before the gap can close"
    if state == DATA_QUALITY_ISSUE:
        return "Record cannot be trusted: " + "; ".join(sorted({f"{item.event.display.lower()} — {item.reason.lower()}" for item in invalid}))
    rejected = [item for item in items if item.verdict == "rejected"]
    if rejected:
        latest = max(rejected, key=lambda item: item.event.event_date or date.min)
        return f"No acceptable evidence: {latest.event.display} ({_fmt(latest.event.event_date)}) — {latest.reason.lower()}"
    return f"No {measure.short.lower()} evidence on record for this period"


def _result(patient: Patient, measure: Measure, as_of: date, run_id: str, state: str, reason: str, trace: _Trace, items: list,
            population=None, denominator=None, exclusion=None, numerator=None, outcome=None, quality=None, action=None) -> dict:
    if not any(step["stage"] == 11 for step in trace.steps):
        trace.add(11, "Persistence and audit", "stored", f"State {state}; rule {RULE_VERSION}; input snapshot fingerprinted")
    evidence = [item.to_dict() for item in items]
    fingerprint = hashlib.sha256(json.dumps({"state": state, "evidence": [(e["event_id"], e["verdict"]) for e in evidence],
                                             "rule": RULE_VERSION}, sort_keys=True).encode()).hexdigest()[:16]
    info = MEASURE_STATES[state]
    return {
        "patient_id": patient.patient_id, "measure_id": measure.measure_id, "rule_version": RULE_VERSION, "period": PERIOD.period_id,
        "as_of": as_of.isoformat(), "run_id": run_id, "state": state, "state_label": info.label, "reason": reason,
        "in_denominator": info.in_denominator, "numerator_met": info.numerator_met, "actionable": info.actionable,
        "population": population or {"met": False, "reason": ""}, "denominator": denominator or {"met": False, "reason": ""},
        "exclusion": exclusion or {"applied": False}, "numerator": numerator or {"met": False}, "outcome": outcome or {},
        "data_quality": quality or {"status": "not assessed", "flags": []}, "action": action, "evidence": evidence,
        "accepted_ids": [e["event_id"] for e in evidence if e["verdict"] == "accepted"], "trace": trace.steps, "fingerprint": fingerprint,
    }


def evaluate_patient(patient: Patient, events: list[Event], as_of: date, run_id: str = "adhoc") -> dict[str, dict]:
    return {measure_id: evaluate_measure(patient, events, measure, as_of, run_id) for measure_id, measure in MEASURES.items()}
