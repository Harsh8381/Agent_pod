"""VBC POC service layer: evaluation runs, care-gap lifecycle, verification, care plans and outreach.

Rules enforced here:
  * gaps are created, updated, closed and reopened only by the engine after re-evaluation;
  * people move work forward (review, assign, outreach, schedule, attach evidence, verify);
  * an order, outreach attempt or appointment never closes a gap; only accepted evidence does;
  * one gap per patient, measure, period and line of work (service / outcome / follow-up) — no duplicates;
  * every transition records actor, trigger, evidence IDs and previous/new state in the audit log.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from app.vbc_poc import engine
from app.vbc_poc import repository as repo
from app.vbc_poc.careplan import CHANNELS, outreach_text, plan_content
from app.vbc_poc.catalog import (
    ALLOWED_TRANSITIONS,
    CONCEPT_LABELS,
    DISPOSITIONS,
    GAP_TYPES,
    HIGH_RISK_DUE_DAYS,
    MEASURE_STATES,
    MEASURES,
    PERIOD,
    RULE_VERSION,
    SOURCES,
)
from app.vbc_poc.dataset import EVENTS, PATIENTS, PATIENTS_BY_ID, Event
from app.vbc_poc.evidence import concept_of
from app.vbc_poc.risk import score_patient

ENGINE = "VBC engine"
FAMILY = {"OPEN_GAP": "SERVICE", "EVIDENCE_REVIEW": "SERVICE", "COMPLETED_PENDING_VERIFICATION": "SERVICE", "DATA_QUALITY_ISSUE": "SERVICE",
          "OUTCOME_NOT_CONTROLLED": "OUTCOME", "FOLLOW_UP_REQUIRED": "FOLLOW_UP"}
FAMILIES = ("SERVICE", "OUTCOME", "FOLLOW_UP")
TYPE_WEIGHT = {"OUTCOME": 2, "FOLLOW_UP": 2, "CLINICAL": 1, "DOCUMENTATION": 0, "DATA_CORRECTION": 0}
OUTREACH_OUTCOMES = ("SCHEDULED", "UNABLE_TO_CONTACT", "PATIENT_DECLINED")
EVIDENCE_DEFAULTS = {
    "GSD-P": ("LOINC", "4548-4", "Hemoglobin A1c (attached result)", "%"),
    "BPD-P": ("LOINC", "85354-9", "Blood pressure (attached reading)", "mmHg"),
    "BCS-P": ("CPT", "77067", "Screening mammography (attached report)", ""),
    "COL-P": ("CPT", "45378", "Colonoscopy (attached report)", ""),
}
COL_MODALITIES = {"45378": "Colonoscopy", "82274": "FIT, immunochemical", "81528": "sDNA-FIT", "74263": "CT colonography"}
ORDER_CODES = {
    "GSD-P": ("LOINC", "4548-4", "HbA1c lab draw scheduled"),
    "BPD-P": ("ENC", "BP-CHECK", "Blood pressure check appointment"),
    "BCS-P": ("CPT", "77067", "Screening mammogram scheduled"),
    "COL-P": ("CPT", "45378", "Colonoscopy scheduled"),
}


class VbcError(ValueError):
    """A request the workflow rules do not allow (shown to the user as-is)."""


class NotFound(VbcError):
    pass


def today() -> date:
    return engine.default_as_of()


def _patient(patient_id: str):
    patient = PATIENTS_BY_ID.get(patient_id)
    if patient is None:
        raise NotFound(f"Unknown patient {patient_id}")
    return patient


def priority_label(score: int) -> str:
    return "High" if score >= 7 else "Medium" if score >= 4 else "Low"


# ---------------------------------------------------------------------------
# Events (base synthetic history + workflow additions + reviewer decisions)
# ---------------------------------------------------------------------------


def events_for(state: dict, patient_id: str, as_of: date | None = None) -> list[Event]:
    decisions = state.get("evidence_decisions", {})
    added = [Event.from_dict(item) for item in state.get("added_events", []) if item["patient_id"] == patient_id]
    out = []
    for event in [event for event in EVENTS if event.patient_id == patient_id] + added:
        decision = decisions.get(event.event_id)
        if decision and (as_of is None or decision["at"][:10] <= as_of.isoformat()):
            event = replace(event, verification="verified" if decision["decision"] == "accepted" else "rejected")
        out.append(event)
    return out


def _find_event(state: dict, event_id: str) -> Event:
    for event in EVENTS:
        if event.event_id == event_id:
            return event
    for item in state.get("added_events", []):
        if item["event_id"] == event_id:
            return Event.from_dict(item)
    raise NotFound(f"Unknown evidence {event_id}")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _store_result(state: dict, result: dict, actor: str, trigger: str) -> dict:
    key = f"{result['patient_id']}|{result['measure_id']}"
    versions = state["results"].setdefault(key, [])
    latest = versions[-1] if versions else None
    stamp = repo.now_iso()
    if latest and latest["fingerprint"] == result["fingerprint"]:
        kept = {name: latest[name] for name in ("result_id", "version", "previous_result_id", "previous_state", "trigger", "actor",
                                                 "evaluated_at", "changed_evidence")}
        latest.clear()
        latest.update(result, **kept, last_evaluated_at=stamp)
        return latest
    previous_accepted = set(latest["accepted_ids"]) if latest else set()
    record = {
        **result, "result_id": repo.next_id(state, "RES"), "version": len(versions) + 1,
        "previous_result_id": latest["result_id"] if latest else None, "previous_state": latest["state"] if latest else None,
        "trigger": trigger, "actor": actor, "evaluated_at": stamp, "last_evaluated_at": stamp,
        "changed_evidence": {"added": sorted(set(result["accepted_ids"]) - previous_accepted),
                             "removed": sorted(previous_accepted - set(result["accepted_ids"]))},
    }
    versions.append(record)
    del versions[:-12]
    if latest is None or latest["state"] != result["state"] or result["actionable"]:
        repo.audit(state, actor, "measure.evaluated" if latest is None else "measure.result_versioned", "measure_result", record["result_id"],
                   previous=latest["state"] if latest else None, new=result["state"], trigger=trigger,
                   evidence_ids=record["changed_evidence"]["added"] or result["accepted_ids"],
                   details=f"{result['patient_id']} · {result['measure_id']} · {result['reason']}")
    return record


def _evaluate(state: dict, patient_id: str, actor: str, trigger: str, run_id: str, as_of: date) -> dict:
    patient = _patient(patient_id)
    events = events_for(state, patient_id)
    results = engine.evaluate_patient(patient, events, as_of, run_id)
    risk = score_patient(patient, events, results, as_of)
    previous = state["risk"].get(patient_id, {}).get("category")
    state["risk"][patient_id] = {**risk, "evaluated_at": repo.now_iso()}
    if previous and previous != risk["category"]:
        repo.audit(state, ENGINE, "risk.changed", "patient", patient_id, previous=previous, new=risk["category"], trigger=trigger,
                   details=f"Score {risk['score']}: " + ", ".join(f["label"] for f in risk["factors"]))
    stored = {measure_id: _store_result(state, result, actor, trigger) for measure_id, result in results.items()}
    _sync_gaps(state, patient, stored, risk, as_of, trigger)
    _refresh_plan(state, patient_id)
    return stored


def _run(state: dict, patient_ids: list[str], actor: str, trigger: str, scope: str) -> str:
    as_of = today()
    run_id = repo.next_id(state, "RUN")
    for patient_id in patient_ids:
        _evaluate(state, patient_id, actor, trigger, run_id, as_of)
    state["runs"].append({"run_id": run_id, "at": repo.now_iso(), "actor": actor, "trigger": trigger, "scope": scope,
                          "patients": len(patient_ids), "as_of": as_of.isoformat(), "rule_version": RULE_VERSION})
    del state["runs"][:-200]
    if state.get("baseline") is None and scope == "population":
        state["baseline"] = {"captured_at": repo.now_iso(), "run_id": run_id, **summarize(state),
                             "states": {key: versions[-1]["state"] for key, versions in state["results"].items() if versions},
                             "versions": {key: versions[-1]["version"] for key, versions in state["results"].items() if versions}}
        repo.audit(state, actor, "baseline.captured", "run", run_id, trigger=trigger,
                   details=f"Baseline: {state['baseline']['totals']['met']}/{state['baseline']['totals']['eligible']} met")
    return run_id


def ensure_initialized(path: Path | None = None) -> dict:
    state = repo.load(path)
    if state.get("runs"):
        return state
    with repo.transaction(path) as fresh:
        if not fresh.get("runs"):
            repo.audit(fresh, ENGINE, "dataset.loaded", "dataset", "synthetic-20", details=f"{len(PATIENTS)} synthetic patients · {len(EVENTS)} events")
            _run(fresh, [patient.patient_id for patient in PATIENTS], ENGINE, "initial load", "population")
        state = fresh
    return repo.load(path)


def evaluate_patient(patient_id: str, actor: str = "user", path: Path | None = None) -> dict:
    _patient(patient_id)
    ensure_initialized(path)
    with repo.transaction(path) as state:
        run_id = _run(state, [patient_id], actor, "manual evaluation", "patient")
    return {"run_id": run_id, **patient_360(patient_id, path)}


def evaluate_population(actor: str = "user", path: Path | None = None) -> dict:
    ensure_initialized(path)
    with repo.transaction(path) as state:
        run_id = _run(state, [patient.patient_id for patient in PATIENTS], actor, "population evaluation", "population")
    return {"run_id": run_id, **dashboard(path)}


def reset_demo(actor: str, path: Path | None = None) -> dict:
    path = path or repo.state_path()
    with repo.transaction(path) as state:
        state.clear()
        state.update(repo.empty_state())
        repo.audit(state, actor, "demo.reset", "dataset", "synthetic-20", details="Workflow state cleared; baseline will be recaptured")
    ensure_initialized(path)
    return dashboard(path)


# ---------------------------------------------------------------------------
# Care-gap lifecycle
# ---------------------------------------------------------------------------


def _move(state: dict, gap: dict, to: str, actor: str, trigger: str, evidence_ids: list | None = None, note: str = "") -> None:
    previous = gap["status"]
    gap["status"] = to
    gap["updated_at"] = repo.now_iso()
    gap["history"].append({"at": gap["updated_at"], "actor": actor, "from": previous, "to": to, "trigger": trigger,
                           "evidence_ids": evidence_ids or [], "note": note})
    repo.audit(state, actor, "gap.transition", "care_gap", gap["gap_id"], previous=previous, new=to, trigger=trigger,
               evidence_ids=evidence_ids, details=f"{gap['patient_id']} · {gap['measure_id']}" + (f" · {note}" if note else ""))


def _note(state: dict, gap: dict, actor: str, trigger: str, note: str, evidence_ids: list | None = None, action: str = "gap.updated") -> None:
    gap["updated_at"] = repo.now_iso()
    gap["history"].append({"at": gap["updated_at"], "actor": actor, "from": gap["status"], "to": gap["status"], "trigger": trigger,
                           "evidence_ids": evidence_ids or [], "note": note})
    repo.audit(state, actor, action, "care_gap", gap["gap_id"], previous=gap["status"], new=gap["status"], trigger=trigger,
               evidence_ids=evidence_ids, details=f"{gap['patient_id']} · {gap['measure_id']} · {note}")


def _due(as_of: date, gap_type: str, due_days: int, risk: dict) -> str:
    days = HIGH_RISK_DUE_DAYS if risk["category"] == "High" and gap_type in ("CLINICAL", "OUTCOME", "FOLLOW_UP") else due_days
    return (as_of + timedelta(days=days)).isoformat()


def _review_ids(result: dict) -> list:
    return [item["event_id"] for item in result["evidence"] if item["verdict"] in ("review", "invalid")]


def _sync_gaps(state: dict, patient, results: dict, risk: dict, as_of: date, trigger: str) -> None:
    pid = patient.patient_id
    for measure_id, result in results.items():
        family = FAMILY.get(result["state"])
        for line in FAMILIES:
            key = f"{pid}|{measure_id}|{PERIOD.period_id}|{line}"
            gap = state["gaps"].get(state["gap_index"].get(key, ""))
            if line == family:
                if gap is None:
                    gap = _create_gap(state, patient, measure_id, line, key, result, risk, as_of, trigger)
                elif gap["status"] == "CLOSED":
                    _reopen(state, gap, result, risk, as_of, trigger)
                else:
                    _refresh_gap(state, gap, result, risk, trigger)
            elif gap and gap["status"] != "CLOSED":
                _engine_close(state, gap, result, trigger)
    # Link follow-up gaps to the screening gap they came from.
    for measure_id in results:
        service = state["gap_index"].get(f"{pid}|{measure_id}|{PERIOD.period_id}|SERVICE")
        follow = state["gap_index"].get(f"{pid}|{measure_id}|{PERIOD.period_id}|FOLLOW_UP")
        if service and follow:
            state["gaps"][follow]["linked_gap_id"] = service


def _create_gap(state, patient, measure_id, line, key, result, risk, as_of, trigger) -> dict:
    action = result["action"]
    gap_type = action["gap_type"]
    gap_id = repo.next_id(state, "GAP")
    score = risk["score"] + TYPE_WEIGHT[gap_type]
    stamp = repo.now_iso()
    gap = {
        "gap_id": gap_id, "key": key, "line": line, "patient_id": patient.patient_id, "measure_id": measure_id, "period": PERIOD.period_id,
        "gap_type": gap_type, "measure_state": result["state"], "result_id": result["result_id"], "reason": result["reason"],
        "recommended_action": action["action"], "status": "DETECTED", "disposition": None, "priority_score": score,
        "priority": priority_label(score), "owner": None, "owner_role": action["owner_role"], "due_date": _due(as_of, gap_type, action["due_days"], risk),
        "detected_at": stamp, "created_at": stamp, "updated_at": stamp, "closed_at": None, "closure": None, "reopened": 0,
        "linked_gap_id": None, "schedule": None, "evidence_ids": [], "outreach_ids": [], "history": [],
    }
    state["gaps"][gap_id] = gap
    state["gap_index"][key] = gap_id
    gap["history"].append({"at": stamp, "actor": ENGINE, "from": None, "to": "DETECTED", "trigger": trigger,
                           "evidence_ids": _review_ids(result), "note": result["reason"]})
    repo.audit(state, ENGINE, "gap.created", "care_gap", gap_id, previous=None, new="DETECTED", trigger=trigger, evidence_ids=_review_ids(result),
               details=f"{patient.patient_id} · {measure_id} · {GAP_TYPES[gap_type]} · {result['reason']}")
    return gap


def _refresh_gap(state, gap, result, risk, trigger) -> None:
    action = result["action"]
    if result["state"] == "COMPLETED_PENDING_VERIFICATION":
        action = {**action, "gap_type": gap["gap_type"], "owner_role": gap["owner_role"], "action": gap["recommended_action"]}
    if action["gap_type"] != gap["gap_type"]:
        _note(state, gap, ENGINE, trigger, f"Gap type {GAP_TYPES[gap['gap_type']]} → {GAP_TYPES[action['gap_type']]}: {result['reason']}",
              _review_ids(result), "gap.type_changed")
        gap["gap_type"] = action["gap_type"]
        gap["owner_role"] = action["owner_role"]
    gap.update(measure_state=result["state"], result_id=result["result_id"], reason=result["reason"], recommended_action=action["action"])
    gap["priority_score"] = risk["score"] + TYPE_WEIGHT[gap["gap_type"]]
    gap["priority"] = priority_label(gap["priority_score"])


def _reopen(state, gap, result, risk, as_of, trigger) -> None:
    action = result["action"]
    gap["reopened"] += 1
    gap.update(gap_type=action["gap_type"], owner_role=action["owner_role"], measure_state=result["state"], result_id=result["result_id"],
               reason=result["reason"], recommended_action=action["action"], closed_at=None, closure=None, disposition=None, schedule=None,
               due_date=_due(as_of, action["gap_type"], action["due_days"], risk))
    gap["priority_score"] = risk["score"] + TYPE_WEIGHT[gap["gap_type"]]
    gap["priority"] = priority_label(gap["priority_score"])
    _move(state, gap, "DETECTED", ENGINE, trigger, _review_ids(result), f"Reopened: {result['reason']}")


def _closure_reason(state_code: str, line: str) -> str:
    if state_code == "MET":
        return "Re-evaluated as met with accepted evidence"
    if state_code == "FOLLOW_UP_REQUIRED" and line == "SERVICE":
        return "Screening completed; a linked follow-up gap tracks the next step"
    if state_code == "OUTCOME_NOT_CONTROLLED" and line == "SERVICE":
        return "Assessment completed; the uncontrolled outcome is tracked as a separate gap"
    if state_code == "EXCLUDED":
        return "Exclusion verified — gap suppressed with evidence"
    if state_code in ("NOT_ELIGIBLE", "DENOMINATOR_NOT_MET"):
        return "Patient no longer in the measure denominator"
    return f"Superseded by {MEASURE_STATES[state_code].label.lower()}"


def _engine_close(state, gap, result, trigger) -> None:
    accepted = result["accepted_ids"]
    if accepted and gap["status"] != "VERIFIED" and result["state"] in ("MET", "FOLLOW_UP_REQUIRED", "OUTCOME_NOT_CONTROLLED"):
        _move(state, gap, "VERIFIED", ENGINE, trigger, accepted, "Accepted evidence passed the rule checks")
    reason = _closure_reason(result["state"], gap["line"])
    gap["closure"] = {"reason": reason, "date": today().isoformat(), "evidence_ids": accepted, "result_id": result["result_id"],
                      "measure_state": result["state"]}
    gap["closed_at"] = repo.now_iso()
    gap["measure_state"] = result["state"]
    _move(state, gap, "CLOSED", ENGINE, trigger, accepted, reason)


def _gap(state: dict, gap_id: str) -> dict:
    gap = state["gaps"].get(gap_id)
    if gap is None:
        raise NotFound(f"Unknown gap {gap_id}")
    return gap


def update_gap(gap_id: str, actor: str, *, status: str | None = None, owner: str | None = None, due_date: str | None = None,
               disposition: str | None = None, note: str = "", schedule_date: str | None = None, schedule_ref: str | None = None,
               path: Path | None = None) -> dict:
    ensure_initialized(path)
    with repo.transaction(path) as state:
        gap = _gap(state, gap_id)
        if gap["status"] == "CLOSED":
            raise VbcError("Closed gaps are read-only. They reopen automatically if the evidence changes or a new period starts.")
        if owner is not None and owner != gap["owner"]:
            _note(state, gap, actor, "owner.changed", f"Owner {gap['owner'] or '—'} → {owner or '—'}", action="gap.owner")
            gap["owner"] = owner or None
        if due_date and due_date != gap["due_date"]:
            date.fromisoformat(due_date)
            _note(state, gap, actor, "due.changed", f"Due date {gap['due_date']} → {due_date}", action="gap.due_date")
            gap["due_date"] = due_date
        if disposition is not None and disposition != (gap["disposition"] or ""):
            if disposition and disposition not in DISPOSITIONS:
                raise VbcError(f"Unknown disposition {disposition}")
            gap["disposition"] = disposition or None
            _note(state, gap, actor, "disposition", f"Disposition: {disposition or 'cleared'} — measure state unchanged"
                  + (f" ({note})" if note else ""), action="gap.disposition")
        if status and status != gap["status"]:
            if status not in ALLOWED_TRANSITIONS.get(gap["status"], ()):
                raise VbcError(f"{gap['status'].title()} → {status.title()} is not allowed. Next steps: "
                               + (", ".join(ALLOWED_TRANSITIONS.get(gap["status"], ())) or "engine only"))
            if status == "ASSIGNED" and not gap["owner"]:
                raise VbcError("Choose an owner before assigning.")
            evidence = []
            if status == "SCHEDULED":
                if not schedule_date:
                    raise VbcError("Scheduling needs an appointment or order date.")
                when = date.fromisoformat(schedule_date)
                event = _order_event(state, gap, when, schedule_ref)
                gap["schedule"] = {"date": when.isoformat(), "ref": event["source_record_id"], "event_id": event["event_id"]}
                evidence = [event["event_id"]]
            _move(state, gap, status, actor, "workflow", evidence, note or _default_note(status, gap))
        elif note and not (owner or due_date or disposition is not None):
            _note(state, gap, actor, "note", note)
        _refresh_plan(state, gap["patient_id"])
        return dict(gap)


def _default_note(status: str, gap: dict) -> str:
    return {"REVIEWED": "Finding confirmed as valid", "ASSIGNED": f"Assigned to {gap['owner']}, due {gap['due_date']}",
            "OUTREACH": "Outreach started", "SCHEDULED": "Booked — the gap stays open until the service is completed and verified"}.get(status, "")


def _order_event(state: dict, gap: dict, when: date, ref: str | None) -> dict:
    system, code, display = ORDER_CODES[gap["measure_id"]]
    number = int(state["counters"].get("ORD", 0)) + 1
    state["counters"]["ORD"] = number
    event = Event(f"{gap['patient_id']}-O{number:02d}", gap["patient_id"], "order", system, code, display, when, today(), "ehr",
                  ref or f"ORD-{gap['patient_id'][-3:]}{number:02d}", status="scheduled", note=f"Booked via gap {gap['gap_id']}")
    data = event.to_dict()
    state["added_events"].append(data)
    return data


def attach_evidence(gap_id: str, actor: str, *, event_date: str, value: float | None = None, value2: float | None = None,
                    code: str | None = None, unit: str | None = None, document_ref: str = "", note: str = "", result_flag: str = "",
                    path: Path | None = None) -> dict:
    """Attach candidate closure evidence. It stays pending until a reviewer verifies it."""
    ensure_initialized(path)
    with repo.transaction(path) as state:
        gap = _gap(state, gap_id)
        if gap["status"] == "CLOSED":
            raise VbcError("This gap is closed; open evidence review on the patient instead.")
        measure_id = gap["measure_id"]
        system, default_code, display, default_unit = EVIDENCE_DEFAULTS[measure_id]
        code = code or default_code
        if measure_id == "COL-P":
            if code not in COL_MODALITIES:
                raise VbcError("Choose a supported screening modality.")
            display = f"{COL_MODALITIES[code]} (attached report)"
        if measure_id == "GSD-P" and value is None:
            raise VbcError("An HbA1c result needs a numeric value.")
        if measure_id == "BPD-P" and (value is None or value2 is None):
            raise VbcError("A blood pressure needs both systolic and diastolic values.")
        when = date.fromisoformat(event_date)
        number = int(state["counters"].get("UPL", 0)) + 1
        state["counters"]["UPL"] = number
        event = Event(f"{gap['patient_id']}-U{number:02d}", gap["patient_id"], "observation" if measure_id in ("GSD-P", "BPD-P") else "procedure",
                      system, code, display, when, today(), "workflow_upload", document_ref or f"UPL-{gap['patient_id'][-3:]}{number:02d}",
                      value=value, value2=value2, unit=unit if unit is not None else default_unit,
                      status="final" if measure_id in ("GSD-P", "BPD-P") else "completed", verification="pending",
                      context="outpatient", result_flag=result_flag, note=note or f"Attached to {gap_id} by {actor}")
        state["added_events"].append(event.to_dict())
        gap["evidence_ids"].append(event.event_id)
        if gap["status"] != "COMPLETED":
            _move(state, gap, "COMPLETED", actor, "evidence.attached", [event.event_id], "Completion evidence received — pending verification")
        else:
            _note(state, gap, actor, "evidence.attached", "Additional evidence attached", [event.event_id])
        _run(state, [gap["patient_id"]], actor, "evidence.attached", "patient")
        return {"event_id": event.event_id, "gap": dict(state["gaps"][gap_id])}


def verify_evidence(event_id: str, actor: str, decision: str, note: str = "", path: Path | None = None) -> dict:
    """Reviewer decision on candidate evidence; the affected measures re-run immediately."""
    if decision not in ("accepted", "rejected"):
        raise VbcError("Decision must be accepted or rejected.")
    ensure_initialized(path)
    with repo.transaction(path) as state:
        event = _find_event(state, event_id)
        if decision == "accepted" and event.source == "narrative":
            raise VbcError("Free text can't be accepted as evidence. Attach the report or a structured record instead.")
        pid = event.patient_id
        state["evidence_decisions"][event_id] = {"decision": decision, "by": actor, "at": repo.now_iso(), "note": note}
        repo.audit(state, actor, f"evidence.{decision}", "clinical_event", event_id, previous=event.verification,
                   new="verified" if decision == "accepted" else "rejected", trigger="evidence.review", evidence_ids=[event_id],
                   details=f"{pid} · {event.display}" + (f" · {note}" if note else ""))
        touched = [gap for gap in state["gaps"].values() if gap["patient_id"] == pid and event_id in gap["evidence_ids"]
                   and gap["status"] == "COMPLETED"]
        for gap in touched:
            if decision == "accepted":
                _move(state, gap, "VERIFIED", actor, "evidence.verified", [event_id], note or "Evidence verified by reviewer")
            else:
                _move(state, gap, "ASSIGNED", actor, "evidence.rejected", [event_id], f"Evidence rejected: {note or 'did not pass review'}")
        _run(state, [pid], actor, f"evidence.{decision}", "patient")
        for gap in state["gaps"].values():
            if gap["patient_id"] == pid and gap["status"] == "VERIFIED":
                _move(state, gap, "ASSIGNED", ENGINE, "re-evaluation", [event_id],
                      f"Re-evaluated as {MEASURE_STATES[gap['measure_state']].label.lower()} — work continues")
        return {"event_id": event_id, "decision": decision, "patient": pid}


# ---------------------------------------------------------------------------
# Care plans and outreach (human approval required)
# ---------------------------------------------------------------------------


def _refresh_plan(state: dict, patient_id: str) -> None:
    gaps = sorted((gap for gap in state["gaps"].values() if gap["patient_id"] == patient_id and gap["status"] != "CLOSED"),
                  key=lambda gap: -gap["priority_score"])
    plan = state["care_plans"].get(patient_id)
    if plan is None and not gaps:
        return
    if plan is None:
        plan = {"plan_id": repo.next_id(state, "PLAN"), "patient_id": patient_id, "status": "draft", "version": 1,
                "created_at": repo.now_iso(), "approved_by": None, "approved_at": None, "review_date": None, "gap_ids": [], "history": []}
        state["care_plans"][patient_id] = plan
        repo.audit(state, ENGINE, "care_plan.drafted", "care_plan", plan["plan_id"], new="draft", details=patient_id)
    goals, interventions, tasks = plan_content(gaps)
    signature = sorted(gap["gap_id"] for gap in gaps)
    plan.update(goals=goals, interventions=interventions, tasks=tasks)
    if plan["status"] == "active" and signature != plan["gap_ids"]:
        plan["status"] = "revision_pending" if gaps else "ready_to_complete"
        plan["history"].append({"at": repo.now_iso(), "actor": ENGINE, "to": plan["status"], "note": "Open gaps changed — needs clinician review"})
        repo.audit(state, ENGINE, "care_plan.needs_review", "care_plan", plan["plan_id"], previous="active", new=plan["status"], details=patient_id)
    if plan["status"] in ("draft", "completed") and gaps and signature != plan["gap_ids"]:
        plan["status"] = "draft"
    plan["gap_ids"] = signature


def approve_care_plan(patient_id: str, actor: str, note: str = "", path: Path | None = None) -> dict:
    ensure_initialized(path)
    with repo.transaction(path) as state:
        plan = state["care_plans"].get(patient_id)
        if plan is None:
            raise NotFound("No care plan for this patient.")
        if plan["status"] not in ("draft", "revision_pending"):
            raise VbcError(f"A {plan['status'].replace('_', ' ')} plan cannot be approved.")
        previous = plan["status"]
        plan.update(status="active", approved_by=actor, approved_at=repo.now_iso(), review_date=(today() + timedelta(days=90)).isoformat())
        if previous == "revision_pending":
            plan["version"] += 1
        plan["history"].append({"at": repo.now_iso(), "actor": actor, "to": "active", "note": note or "Approved"})
        repo.audit(state, actor, "care_plan.approved", "care_plan", plan["plan_id"], previous=previous, new="active", details=f"{patient_id} v{plan['version']}")
        return dict(plan)


def complete_care_plan(patient_id: str, actor: str, path: Path | None = None) -> dict:
    ensure_initialized(path)
    with repo.transaction(path) as state:
        plan = state["care_plans"].get(patient_id)
        if plan is None:
            raise NotFound("No care plan for this patient.")
        if any(gap["patient_id"] == patient_id and gap["status"] != "CLOSED" for gap in state["gaps"].values()):
            raise VbcError("Open gaps remain; the plan cannot be completed.")
        previous = plan["status"]
        plan["status"] = "completed"
        plan["history"].append({"at": repo.now_iso(), "actor": actor, "to": "completed", "note": "All gaps closed"})
        repo.audit(state, actor, "care_plan.completed", "care_plan", plan["plan_id"], previous=previous, new="completed", details=patient_id)
        return dict(plan)


def draft_outreach(gap_id: str, channel: str, actor: str, path: Path | None = None) -> dict:
    if channel not in CHANNELS:
        raise VbcError(f"Channel must be one of {', '.join(CHANNELS)}")
    ensure_initialized(path)
    with repo.transaction(path) as state:
        gap = _gap(state, gap_id)
        if gap["status"] == "CLOSED":
            raise VbcError("The gap is closed; no outreach needed.")
        patient = _patient(gap["patient_id"])
        text = outreach_text(patient, gap, channel)
        if text is None:
            raise VbcError("Data corrections are internal work — no patient outreach.")
        if channel == "SMS" and not patient.phone_on_file:
            raise VbcError("No mobile number on file.")
        outreach_id = repo.next_id(state, "OUT")
        record = {"outreach_id": outreach_id, "gap_id": gap_id, "patient_id": patient.patient_id, "measure_id": gap["measure_id"],
                  "channel": channel, "language": patient.language, "text": text, "status": "draft", "created_by": actor,
                  "created_at": repo.now_iso(), "approved_by": None, "approved_at": None, "sent_at": None, "disposition": None,
                  "disposition_at": None, "generator": "Template (AI wording optional, disabled in POC)"}
        state["outreach"][outreach_id] = record
        gap["outreach_ids"].append(outreach_id)
        repo.audit(state, actor, "outreach.drafted", "outreach", outreach_id, new="draft", details=f"{gap_id} · {channel}")
        return dict(record)


def _outreach(state: dict, outreach_id: str) -> dict:
    record = state["outreach"].get(outreach_id)
    if record is None:
        raise NotFound(f"Unknown outreach {outreach_id}")
    return record


def approve_outreach(outreach_id: str, actor: str, text: str | None = None, path: Path | None = None) -> dict:
    with repo.transaction(path) as state:
        record = _outreach(state, outreach_id)
        if record["status"] != "draft":
            raise VbcError("Only drafts can be approved.")
        if text:
            record["text"] = text.strip()
        record.update(status="approved", approved_by=actor, approved_at=repo.now_iso())
        repo.audit(state, actor, "outreach.approved", "outreach", outreach_id, previous="draft", new="approved", details=record["gap_id"])
        return dict(record)


def send_outreach(outreach_id: str, actor: str, path: Path | None = None) -> dict:
    with repo.transaction(path) as state:
        record = _outreach(state, outreach_id)
        if record["status"] != "approved":
            raise VbcError("A person must approve the message before it is sent.")
        gap = _gap(state, record["gap_id"])
        if gap["status"] not in ("ASSIGNED", "OUTREACH"):
            raise VbcError("Assign the gap to an owner before contacting the patient.")
        record.update(status="sent", sent_at=repo.now_iso())
        repo.audit(state, actor, "outreach.sent", "outreach", outreach_id, previous="approved", new="sent",
                   details=f"{record['channel']} · simulated delivery")
        if gap["status"] == "ASSIGNED":
            _move(state, gap, "OUTREACH", actor, "outreach.sent", [outreach_id], f"{record['channel']} sent")
        return dict(record)


def record_outreach_outcome(outreach_id: str, actor: str, outcome: str, schedule_date: str | None = None, note: str = "",
                            path: Path | None = None) -> dict:
    if outcome not in OUTREACH_OUTCOMES:
        raise VbcError(f"Outcome must be one of {', '.join(OUTREACH_OUTCOMES)}")
    with repo.transaction(path) as state:
        record = _outreach(state, outreach_id)
        if record["status"] != "sent":
            raise VbcError("Record an outcome after the message is sent.")
        record.update(status="closed", disposition=outcome, disposition_at=repo.now_iso())
        gap = _gap(state, record["gap_id"])
        repo.audit(state, actor, "outreach.outcome", "outreach", outreach_id, previous="sent", new=outcome, details=note or record["gap_id"])
        if outcome == "SCHEDULED" and gap["status"] in ("OUTREACH", "ASSIGNED"):
            when = date.fromisoformat(schedule_date) if schedule_date else today() + timedelta(days=14)
            event = _order_event(state, gap, when, None)
            gap["schedule"] = {"date": when.isoformat(), "ref": event["source_record_id"], "event_id": event["event_id"]}
            gap["disposition"] = None
            _move(state, gap, "SCHEDULED", actor, "outreach.outcome", [outreach_id, event["event_id"]], f"Booked for {when:%d %b %Y}")
        elif outcome != "SCHEDULED":
            gap["disposition"] = outcome
            _note(state, gap, actor, "outreach.outcome", f"Disposition {outcome} — not proof of care; stays in the denominator", [outreach_id],
                  "gap.disposition")
        _refresh_plan(state, gap["patient_id"])
        return dict(record)


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


def latest_results(state: dict, patient_id: str | None = None) -> dict:
    out = {}
    for key, versions in state.get("results", {}).items():
        pid, measure_id = key.split("|")
        if versions and (patient_id is None or pid == patient_id):
            out[(pid, measure_id)] = versions[-1]
    return out


def summarize(state: dict) -> dict:
    measures = {}
    totals = {"eligible": 0, "met": 0, "open": 0, "outcome": 0, "review": 0, "data_quality": 0, "follow_up": 0, "excluded": 0}
    for (pid, measure_id), result in latest_results(state).items():
        row = measures.setdefault(measure_id, {"denominator": 0, "numerator": 0, "states": {}})
        row["states"][result["state"]] = row["states"].get(result["state"], 0) + 1
        if result["in_denominator"]:
            row["denominator"] += 1
            totals["eligible"] += 1
        if result["numerator_met"]:
            row["numerator"] += 1
            totals["met"] += 1
        bucket = {"OPEN_GAP": "open", "OUTCOME_NOT_CONTROLLED": "outcome", "EVIDENCE_REVIEW": "review", "COMPLETED_PENDING_VERIFICATION": "review",
                  "DATA_QUALITY_ISSUE": "data_quality", "FOLLOW_UP_REQUIRED": "follow_up", "EXCLUDED": "excluded"}.get(result["state"])
        if bucket:
            totals[bucket] += 1
    for row in measures.values():
        row["rate"] = round(row["numerator"] / row["denominator"] * 100, 1) if row["denominator"] else None
    totals["rate"] = round(totals["met"] / totals["eligible"] * 100, 1) if totals["eligible"] else None
    return {"measures": measures, "totals": totals}


def evidence_badge(result: dict, gap: dict | None) -> str:
    pending = [item for item in result["evidence"] if item["verdict"] == "review"]
    if result["accepted_ids"] and result["state"] in ("MET", "FOLLOW_UP_REQUIRED", "OUTCOME_NOT_CONTROLLED"):
        badge = "Verified evidence"
    elif any(item["source"] == "workflow_upload" for item in pending):
        badge = "Completion evidence pending verification"
    elif pending:
        badge = "Candidate evidence under review"
    elif any(item["verdict"] == "invalid" for item in result["evidence"]):
        badge = "Invalid record — correction needed"
    elif gap and gap.get("schedule"):
        badge = f"Scheduled {gap['schedule']['date']} — completion pending"
    elif any(item["reason_code"] == "NOT_COMPLETED" for item in result["evidence"]):
        badge = "Order on file — completion pending"
    else:
        badge = "No acceptable evidence"
    return badge


def patient_360(patient_id: str, path: Path | None = None) -> dict:
    patient = _patient(patient_id)
    state = ensure_initialized(path)
    as_of = today()
    results = latest_results(state, patient_id)
    gaps = [gap for gap in state["gaps"].values() if gap["patient_id"] == patient_id]
    verdicts = {}
    for result in results.values():
        for item in result["evidence"]:
            verdicts.setdefault(item["event_id"], []).append(f"{result['measure_id']}: {item['verdict']}")
    timeline = []
    for event in sorted(events_for(state, patient_id), key=lambda e: (e.event_date or e.recorded_at), reverse=True):
        concept = concept_of(event)
        timeline.append({**event.to_dict(), "concept": concept, "concept_label": CONCEPT_LABELS.get(concept, ""),
                         "source_label": SOURCES.get(event.source, (event.source, 9))[0], "used_by": verdicts.get(event.event_id, []),
                         "undated": event.event_date is None})
    measures = []
    for measure_id, measure in MEASURES.items():
        result = results.get((patient_id, measure_id))
        if not result:
            continue
        open_gaps = [gap for gap in gaps if gap["measure_id"] == measure_id and gap["status"] != "CLOSED"]
        lead = open_gaps[0] if open_gaps else None
        measures.append({"measure_id": measure_id, "name": measure.name, "short": measure.short, "result": result,
                         "workflow": lead["status"] if lead else ("CLOSED" if any(g["measure_id"] == measure_id for g in gaps) else None),
                         "evidence_badge": evidence_badge(result, lead), "gap_ids": [gap["gap_id"] for gap in open_gaps]})
    related = {patient_id} | {gap["gap_id"] for gap in gaps} | {plan["plan_id"] for pid, plan in state["care_plans"].items() if pid == patient_id}
    related |= {result["result_id"] for versions in (state["results"].get(f"{patient_id}|{m}", []) for m in MEASURES) for result in versions}
    related |= {oid for oid, item in state["outreach"].items() if item["patient_id"] == patient_id}
    audit_rows = [event for event in state["audit"] if event["object_id"] in related or patient_id in event.get("details", "")]
    return {
        "patient": {**{key: getattr(patient, key) for key in ("patient_id", "name", "sex", "attributed_provider", "program", "cohort", "story",
                                                                "language", "preferred_channel", "status")},
                    "dob": patient.dob.isoformat() if patient.dob else None, "age": patient.age_on(as_of),
                    "enrollment": [(start.isoformat(), end.isoformat()) for start, end in patient.enrollment]},
        "risk": state["risk"].get(patient_id), "timeline": timeline, "measures": measures,
        "gaps": sorted(gaps, key=lambda gap: (gap["status"] == "CLOSED", -gap["priority_score"])),
        "care_plan": state["care_plans"].get(patient_id),
        "outreach": [item for item in state["outreach"].values() if item["patient_id"] == patient_id],
        "audit": audit_rows[-60:], "rule_version": RULE_VERSION, "as_of": as_of.isoformat(), "period": PERIOD.period_id,
    }


def gap_rows(state: dict, *, status: str | None = None, measure_id: str | None = None, owner: str | None = None,
             priority: str | None = None, include_closed: bool = False, overdue_only: bool = False) -> list[dict]:
    as_of = today()
    rows = []
    for gap in state["gaps"].values():
        if not include_closed and gap["status"] == "CLOSED":
            continue
        if status and gap["status"] != status or measure_id and gap["measure_id"] != measure_id:
            continue
        if owner and (gap["owner"] or "") != owner or priority and gap["priority"] != priority:
            continue
        overdue = (as_of - date.fromisoformat(gap["due_date"])).days if gap["status"] != "CLOSED" else 0
        if overdue_only and overdue <= 0:
            continue
        patient = PATIENTS_BY_ID[gap["patient_id"]]
        risk = state["risk"].get(gap["patient_id"], {})
        rows.append({**gap, "patient_name": patient.name, "risk_score": risk.get("score", 0), "risk_category": risk.get("category", "Low"),
                     "overdue_days": max(overdue, 0), "measure_short": MEASURES[gap["measure_id"]].short, "gap_type_label": GAP_TYPES[gap["gap_type"]]})
    rows.sort(key=lambda row: (row["status"] == "CLOSED", -row["priority_score"], -row["overdue_days"], row["due_date"]))
    return rows


def list_gaps(path: Path | None = None, **filters) -> list[dict]:
    return gap_rows(ensure_initialized(path), **filters)


def gap_detail(gap_id: str, path: Path | None = None) -> dict:
    state = ensure_initialized(path)
    gap = _gap(state, gap_id)
    result = latest_results(state, gap["patient_id"]).get((gap["patient_id"], gap["measure_id"]))
    patient = PATIENTS_BY_ID[gap["patient_id"]]
    used = [item for item in result["evidence"] if item["verdict"] == "accepted"]
    not_used = [item for item in result["evidence"] if item["verdict"] != "accepted"]
    return {"gap": gap, "result": result, "patient": {"patient_id": patient.patient_id, "name": patient.name, "language": patient.language,
                                                     "preferred_channel": patient.preferred_channel},
            "risk": state["risk"].get(gap["patient_id"]), "evidence_used": used, "evidence_not_accepted": not_used,
            "evidence_badge": evidence_badge(result, gap), "outreach": [state["outreach"][oid] for oid in gap["outreach_ids"] if oid in state["outreach"]],
            "allowed_next": list(ALLOWED_TRANSITIONS.get(gap["status"], ())), "linked_gap": state["gaps"].get(gap.get("linked_gap_id") or "")}


def dashboard(path: Path | None = None) -> dict:
    from app.vbc_poc.metrics import compute_dashboard

    return compute_dashboard(ensure_initialized(path))


def audit_log(path: Path | None = None) -> dict:
    state = ensure_initialized(path)
    intact, broken_at = repo.verify_chain(state)
    return {"events": state["audit"], "chain_intact": intact, "broken_at": broken_at, "runs": state["runs"], "rule_version": RULE_VERSION}
