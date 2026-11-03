"""Review-to-closure care-gap workflow with specializations (Care Gap Management workstream).

The extract's 505 care gaps become writable. Each gap type has its own workflow definition —
states, transitions, the role allowed to make each move, required fields and service-level targets:

  Clinical Opportunity          screening / service not done
      DETECTED > REVIEWED > ASSIGNED > OUTREACH > SCHEDULED > COMPLETED > VERIFIED > CLOSED
  Outcome Management            done, but the result is out of target
      DETECTED > REVIEWED > ASSIGNED > CLINICAL_REVIEW > PLAN_UPDATED > FOLLOW_UP_SCHEDULED > RECHECK_RESULTED > VERIFIED > CLOSED
  Documentation Reconciliation  the care happened, the record is missing or unverified
      DETECTED > REVIEWED > ASSIGNED > RECORD_REQUESTED > RECORD_RECEIVED > ABSTRACTED > VERIFIED > CLOSED

Rules shared by every specialization:
  * evidence captured in the workflow is NON-STANDARD supplemental data: it counts only after a second
    person verifies it, and then flows through the evidence normalizer like any other source;
  * a gap closes only when the measure, recalculated with that verified evidence, is compliant —
    or by an approved disposition (declined, unable to contact after three attempts, exclusion candidate);
  * verifier != the person who captured or abstracted the evidence;
  * every transition is on the hash-chained audit trail; overdue states are flagged against the SLA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.hedis import calculator, normalizer, sources, store, versions
from app.hedis.errors import Conflict, HedisError, NotFound, SegregationOfDuties

MODULE = "Care Gap Workflow"


@dataclass(frozen=True)
class Transition:
    to: str
    label: str
    fields: tuple = ()
    roles: tuple = ()  # empty = anyone with Edit on the workflow module
    approve: bool = False  # needs Approve on the workflow module
    captures: bool = False  # records evidence
    four_eyes_against: str | None = None  # state whose actor may not make this move
    closes: bool = False


@dataclass(frozen=True)
class Workflow:
    gap_type: str
    key: str
    label: str
    purpose: str
    states: tuple
    transitions: dict
    sla_days: dict
    seed_map: dict = field(default_factory=dict)


PHYSICIAN = ("Physician / APP", "Clinical Reviewer")
ABSTRACTORS = ("Abstractor", "HEDIS Analyst", "HEDIS Lead")

WORKFLOWS = {
    "Clinical Opportunity": Workflow(
        "Clinical Opportunity", "clinical", "Screening / service gap", "A needed screening or service has not been done.",
        ("DETECTED", "REVIEWED", "ASSIGNED", "OUTREACH", "SCHEDULED", "COMPLETED", "VERIFIED", "CLOSED"),
        {
            "DETECTED": (Transition("REVIEWED", "Confirm the gap is real", ("note",)),),
            "REVIEWED": (Transition("ASSIGNED", "Assign an owner", ("assignee",)),),
            "ASSIGNED": (Transition("OUTREACH", "Contact the member", ("channel",)),
                         Transition("SCHEDULED", "Book the service", ("schedule_date",))),
            "OUTREACH": (Transition("SCHEDULED", "Book the service", ("schedule_date",)),
                         Transition("OUTREACH", "Log another attempt", ("channel",))),
            "SCHEDULED": (Transition("COMPLETED", "Record completion evidence", ("service_date",), captures=True),),
            "COMPLETED": (Transition("VERIFIED", "Verify the evidence (second person)", approve=True, four_eyes_against="COMPLETED"),),
            "VERIFIED": (Transition("CLOSED", "Close — measure recalculated as compliant", closes=True),),
        },
        {"DETECTED": 3, "REVIEWED": 5, "ASSIGNED": 7, "OUTREACH": 14, "SCHEDULED": 30, "COMPLETED": 5, "VERIFIED": 2},
        {"Detected": "DETECTED", "Reviewed": "REVIEWED", "Assigned": "ASSIGNED", "Outreach": "OUTREACH", "Scheduled": "SCHEDULED"},
    ),
    "Outcome Management": Workflow(
        "Outcome Management", "outcome", "Outcome out of target", "The test was done but the result is not controlled.",
        ("DETECTED", "REVIEWED", "ASSIGNED", "CLINICAL_REVIEW", "PLAN_UPDATED", "FOLLOW_UP_SCHEDULED", "RECHECK_RESULTED", "VERIFIED", "CLOSED"),
        {
            "DETECTED": (Transition("REVIEWED", "Confirm the uncontrolled result", ("note",)),),
            "REVIEWED": (Transition("ASSIGNED", "Assign a care manager", ("assignee",)),),
            "ASSIGNED": (Transition("CLINICAL_REVIEW", "Send for clinician review", ("note",)),),
            "CLINICAL_REVIEW": (Transition("PLAN_UPDATED", "Record the clinical plan", ("plan",), roles=PHYSICIAN),),
            "PLAN_UPDATED": (Transition("FOLLOW_UP_SCHEDULED", "Schedule the recheck", ("schedule_date",)),),
            "FOLLOW_UP_SCHEDULED": (Transition("RECHECK_RESULTED", "Record the recheck result", ("service_date",), captures=True),),
            "RECHECK_RESULTED": (Transition("VERIFIED", "Verify the recheck (second person)", approve=True, four_eyes_against="RECHECK_RESULTED"),),
            "VERIFIED": (Transition("CLOSED", "Close — recalculated as controlled", closes=True),
                         Transition("CLINICAL_REVIEW", "Still out of target — back to the clinician", ("note",))),
        },
        {"DETECTED": 3, "REVIEWED": 5, "ASSIGNED": 5, "CLINICAL_REVIEW": 7, "PLAN_UPDATED": 5, "FOLLOW_UP_SCHEDULED": 45,
         "RECHECK_RESULTED": 5, "VERIFIED": 2},
        {"Detected": "DETECTED", "Reviewed": "REVIEWED", "Assigned": "ASSIGNED", "Outreach": "CLINICAL_REVIEW", "Scheduled": "FOLLOW_UP_SCHEDULED"},
    ),
    "Documentation Reconciliation": Workflow(
        "Documentation Reconciliation", "documentation", "Missing or unverified record",
        "Care likely happened, but the supporting record is missing or unverified.",
        ("DETECTED", "REVIEWED", "ASSIGNED", "RECORD_REQUESTED", "RECORD_RECEIVED", "ABSTRACTED", "VERIFIED", "CLOSED"),
        {
            "DETECTED": (Transition("REVIEWED", "Confirm what is missing", ("note",)),),
            "REVIEWED": (Transition("ASSIGNED", "Assign a documentation coordinator", ("assignee",)),),
            "ASSIGNED": (Transition("RECORD_REQUESTED", "Request the record", ("requested_from",)),),
            "RECORD_REQUESTED": (Transition("RECORD_RECEIVED", "Log the record received", ("document_ref",)),
                                 Transition("RECORD_REQUESTED", "Chase the request", ("requested_from",))),
            "RECORD_RECEIVED": (Transition("ABSTRACTED", "Abstract the record", ("service_date",), roles=ABSTRACTORS, captures=True),),
            "ABSTRACTED": (Transition("VERIFIED", "Primary-source verify the abstraction", approve=True, four_eyes_against="ABSTRACTED"),),
            "VERIFIED": (Transition("CLOSED", "Close — recalculated as compliant", closes=True),),
        },
        {"DETECTED": 3, "REVIEWED": 5, "ASSIGNED": 5, "RECORD_REQUESTED": 21, "RECORD_RECEIVED": 5, "ABSTRACTED": 5, "VERIFIED": 2},
        {"Detected": "DETECTED", "Reviewed": "REVIEWED", "Assigned": "ASSIGNED", "Outreach": "RECORD_REQUESTED", "Scheduled": "RECORD_REQUESTED"},
    ),
}
DISPOSITIONS = {
    "PATIENT_DECLINED": "Member declined after an informed discussion",
    "UNABLE_TO_CONTACT": "Unable to reach the member after three documented attempts",
    "EXCLUSION_CANDIDATE": "Clinical exclusion likely; routed to exclusion review",
}
MIN_ATTEMPTS = 3
# What a completion records for each measure: evidence concept and the values it needs.
CAPTURE = {
    "GSD": ("hba1c", "LOINC", "4548-4", ("value",), "%"),
    "BPD-E": ("bp", "LOINC", "85354-9", ("value", "value2"), "mmHg"),
    "KED": ("egfr", "LOINC", "98979-8", ("value",), "mL/min/1.73m2"),
    "BCS-E": ("mammogram", "CPT", "77067", (), ""),
    "COL-E": ("fit_result", "LOINC", "29771-3", ("result_flag",), ""),
    "TRC": ("outpatient_visit", "ENC", "Post-discharge follow-up", (), ""),
}


def _seed(state: dict) -> None:
    flow = state.setdefault("workflow", {"gaps": {}, "seeded_from": None, "evidence": {}})
    if flow["gaps"]:
        return
    for row in sources.table("care_gaps"):
        definition = WORKFLOWS.get(row["gap_type"])
        if not definition:
            continue
        status = definition.seed_map.get(row["status"], "DETECTED")
        flow["gaps"][row["gap_id"]] = {
            "gap_id": row["gap_id"], "patient_id": row["patient_id"], "measure_id": row["measure_id"], "gap_type": row["gap_type"],
            "workflow": definition.key, "status": status, "priority": row["priority"], "owner_role": row["owner"], "assignee": None,
            "detected": row["detected_date"], "due": row["due_date"], "entered_at": row["detected_date"], "attempts": 0,
            "disposition": None, "captures": [], "fields": {},
            "history": [{"at": row["detected_date"], "by": "extract", "from": None, "to": status, "note": f"Seeded from extract status {row['status']}"}],
        }
    flow["seeded_from"] = sources.dataset_sha256()


def _ready(path: Path | None) -> dict:
    state = store.load(path)
    if not state["workflow"]["gaps"]:
        with store.transaction(path) as writable:
            _seed(writable)
            if writable["workflow"]["gaps"]:
                store.audit(writable, "system", "workflow.seeded", "workflow", "care_gaps",
                            details=f"{len(writable['workflow']['gaps'])} gaps from the extract")
        state = store.load(path)
    return state


def _today() -> date:
    return sources.period()[1]


def _overdue(gap: dict) -> int:
    if gap["status"] == "CLOSED":
        return 0
    sla = WORKFLOWS[gap["gap_type"]].sla_days.get(gap["status"])
    if not sla:
        return 0
    entered = date.fromisoformat(gap["entered_at"][:10])
    return max(0, (_today() - entered).days - sla)


def _calculated(state: dict, patient_id: str, measure_id: str) -> dict:
    """Recalculate one member's measure with the active version and all verified workflow evidence."""
    _, spec = versions.active_spec()
    normalized = normalizer.normalize(versions._workflow_evidence(state))
    members = {patient_id: normalizer.patient_demographics()[patient_id]}
    one = {"measures": {measure_id: spec["measures"][measure_id]}}
    return calculator.calculate(one, normalized, members)[(patient_id, measure_id)]


def list_gaps(path: Path | None = None, *, gap_type: str | None = None, status: str | None = None, measure_id: str | None = None,
              overdue_only: bool = False, include_closed: bool = False) -> list[dict]:
    state = _ready(path)
    members = normalizer.patient_demographics()
    rows = []
    for gap in state["workflow"]["gaps"].values():
        if not include_closed and gap["status"] == "CLOSED" or gap_type and gap["gap_type"] != gap_type:
            continue
        if status and gap["status"] != status or measure_id and gap["measure_id"] != measure_id:
            continue
        overdue = _overdue(gap)
        if overdue_only and not overdue:
            continue
        rows.append({**{k: v for k, v in gap.items() if k not in ("history", "captures")}, "name": members.get(gap["patient_id"], {}).get("name", ""),
                     "overdue_days": overdue, "workflow_label": WORKFLOWS[gap["gap_type"]].label})
    priority = {"High": 0, "Medium": 1, "Low": 2}
    rows.sort(key=lambda r: (r["status"] == "CLOSED", priority.get(r["priority"], 3), -r["overdue_days"], r["due"]))
    return rows


def detail(gap_id: str, path: Path | None = None) -> dict:
    state = _ready(path)
    gap = state["workflow"]["gaps"].get(gap_id)
    if not gap:
        raise NotFound(f"Care gap {gap_id} not found.")
    definition = WORKFLOWS[gap["gap_type"]]
    allowed = [{"to": t.to, "label": t.label, "fields": list(t.fields), "roles": list(t.roles), "approve": t.approve,
                "captures": t.captures, "closes": t.closes} for t in definition.transitions.get(gap["status"], ())]
    captures = [state["workflow"]["evidence"][c] for c in gap["captures"] if c in state["workflow"]["evidence"]]
    return {"gap": {**gap, "overdue_days": _overdue(gap)}, "workflow": {"label": definition.label, "purpose": definition.purpose,
                                                                         "states": list(definition.states), "sla_days": definition.sla_days},
            "allowed": allowed, "dispositions": DISPOSITIONS if gap["status"] != "CLOSED" else {}, "captures": captures,
            "capture_spec": dict(zip(("concept", "code_system", "code", "values", "unit"), CAPTURE[gap["measure_id"]], strict=True)),
            "calculated": _calculated(state, gap["patient_id"], gap["measure_id"]),
            "member": normalizer.patient_demographics().get(gap["patient_id"], {})}


def _last_actor(gap: dict, state_name: str) -> str | None:
    return next((h["by"] for h in reversed(gap["history"]) if h["to"] == state_name), None)


def _capture(state: dict, gap: dict, actor: str, fields: dict) -> str:
    concept, system, code, values, unit = CAPTURE[gap["measure_id"]]
    if gap["measure_id"] == "KED" and fields.get("component") == "uacr":
        concept, code, unit = "uacr", "9318-7", "mg/g"
    item = {"capture_id": store.next_id(state, "WFE"), "gap_id": gap["gap_id"], "patient_id": gap["patient_id"], "measure_id": gap["measure_id"],
            "concept": concept, "code_system": system, "code": code, "service_date": fields["service_date"], "unit": unit,
            "captured_by": actor, "captured_at": store.now_iso(), "verified_by": None, "verified_at": None,
            "document_ref": fields.get("document_ref") or gap["fields"].get("document_ref", "")}
    service = date.fromisoformat(fields["service_date"])
    if service > _today():
        raise HedisError("The service date cannot be in the future.")
    for name in values:
        raw = fields.get(name)
        if raw in (None, ""):
            raise HedisError(f"{gap['measure_id']} completion needs {name.replace('value2', 'diastolic').replace('value', 'the result value')}.")
        if name == "result_flag":
            if str(raw).lower() not in ("positive", "negative"):
                raise HedisError("FIT result must be positive or negative.")
            item[name] = str(raw).lower()
        else:
            item[name] = float(raw)
    state["workflow"]["evidence"][item["capture_id"]] = item
    gap["captures"].append(item["capture_id"])
    return item["capture_id"]


def transition(gap_id: str, to: str, actor: str, actor_role: str, permitted, fields: dict | None = None, path: Path | None = None) -> dict:
    """Move a gap one step. `permitted(module, action)` answers for the signed-in person."""
    fields = {k: v for k, v in (fields or {}).items() if v not in (None, "")}
    if actor.startswith("agent:"):
        raise SegregationOfDuties("Agents propose; people move care gaps.")
    state = _ready(path)
    gap = state["workflow"]["gaps"].get(gap_id)
    if not gap:
        raise NotFound(f"Care gap {gap_id} not found.")
    definition = WORKFLOWS[gap["gap_type"]]
    move = next((t for t in definition.transitions.get(gap["status"], ()) if t.to == to), None)
    if not move:
        raise Conflict(f"{definition.label}: {gap['status']} cannot move to {to}.")
    if not permitted(MODULE, "Approve" if move.approve else "Edit"):
        raise SegregationOfDuties(f"Requires {'Approve' if move.approve else 'Edit'} on {MODULE}.")
    if move.roles and actor_role not in move.roles and actor_role != "Super Admin":
        raise SegregationOfDuties(f"'{move.label}' is done by {', '.join(move.roles)}.")
    missing = [name for name in move.fields if not fields.get(name)]
    if missing:
        raise HedisError(f"'{move.label}' needs: {', '.join(missing)}.")
    if move.four_eyes_against and _last_actor(gap, move.four_eyes_against) == actor:
        raise SegregationOfDuties("The person who recorded the evidence cannot verify it.")
    if move.closes:
        result = _calculated(state, gap["patient_id"], gap["measure_id"])
        if not result["numerator_met"]:
            raise Conflict(f"Recalculated as {result['label']}: {result['reason']}. Only compliant measures (or an approved disposition) close a gap.")
    with store.transaction(path) as writable:
        _seed(writable)
        gap = writable["workflow"]["gaps"][gap_id]
        previous = gap["status"]
        if move.captures:
            fields["capture_id"] = _capture(writable, gap, actor, fields)
        if move.to == "VERIFIED":
            for capture_id in gap["captures"]:
                capture = writable["workflow"]["evidence"][capture_id]
                if not capture["verified_by"]:
                    capture.update(verified_by=actor, verified_at=store.now_iso())
        if move.to == "OUTREACH":
            gap["attempts"] += 1
        if "assignee" in fields:
            gap["assignee"] = fields["assignee"]
        gap["fields"].update({k: v for k, v in fields.items() if k != "note"})
        gap.update(status=move.to, entered_at=store.now_iso())
        gap["history"].append({"at": store.now_iso(), "by": actor, "role": actor_role, "from": previous, "to": move.to,
                               "note": fields.get("note") or fields.get("plan") or "", "fields": {k: v for k, v in fields.items() if k != "note"}})
        store.audit(writable, actor, "workflow.transition", "care_gap", gap_id, previous=previous, new=move.to, trigger=move.label,
                    evidence_ids=[fields["capture_id"]] if "capture_id" in fields else [],
                    details=f"{gap['patient_id']} · {gap['measure_id']} · {definition.label}" + (f" · {fields.get('note')}" if fields.get("note") else ""))
        return gap


def close_with_disposition(gap_id: str, disposition: str, actor: str, permitted, note: str, path: Path | None = None) -> dict:
    if disposition not in DISPOSITIONS:
        raise HedisError(f"Disposition must be one of {', '.join(DISPOSITIONS)}.")
    if not permitted(MODULE, "Approve"):
        raise SegregationOfDuties(f"Closing with a disposition needs Approve on {MODULE}.")
    if len(note.strip()) < 10:
        raise HedisError("Document the disposition (at least 10 characters).")
    state = _ready(path)
    gap = state["workflow"]["gaps"].get(gap_id)
    if not gap:
        raise NotFound(f"Care gap {gap_id} not found.")
    if gap["status"] == "CLOSED":
        raise Conflict(f"{gap_id} is already closed.")
    if disposition == "UNABLE_TO_CONTACT" and gap["attempts"] < MIN_ATTEMPTS:
        raise Conflict(f"Unable to contact needs {MIN_ATTEMPTS} documented outreach attempts; {gap['attempts']} on record.")
    with store.transaction(path) as writable:
        gap = writable["workflow"]["gaps"][gap_id]
        previous = gap["status"]
        gap.update(status="CLOSED", disposition=disposition, entered_at=store.now_iso())
        gap["history"].append({"at": store.now_iso(), "by": actor, "from": previous, "to": "CLOSED", "note": f"{disposition}: {note.strip()}"})
        store.audit(writable, actor, "workflow.closed_disposition", "care_gap", gap_id, previous=previous, new="CLOSED", trigger=disposition,
                    details=note.strip())
        return gap


def summary(path: Path | None = None) -> dict:
    state = _ready(path)
    gaps = list(state["workflow"]["gaps"].values())
    by_type = {}
    for gap in gaps:
        entry = by_type.setdefault(gap["gap_type"], {"total": 0, "closed": 0, "overdue": 0, "states": {}})
        entry["total"] += 1
        entry["closed"] += gap["status"] == "CLOSED"
        entry["overdue"] += bool(_overdue(gap))
        entry["states"][gap["status"]] = entry["states"].get(gap["status"], 0) + 1
    return {"total": len(gaps), "open": sum(g["status"] != "CLOSED" for g in gaps), "closed": sum(g["status"] == "CLOSED" for g in gaps),
            "overdue": sum(bool(_overdue(g)) for g in gaps), "by_type": by_type,
            "captures": len(state["workflow"]["evidence"]), "verified_captures": sum(bool(c["verified_by"]) for c in state["workflow"]["evidence"].values()),
            "workflows": {name: {"label": wf.label, "purpose": wf.purpose, "states": list(wf.states)} for name, wf in WORKFLOWS.items()},
            "dispositions": DISPOSITIONS, "as_of": _today().isoformat()}
