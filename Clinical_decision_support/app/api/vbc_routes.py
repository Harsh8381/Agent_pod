"""Value-Based Care POC API (/vbc). Included only when the VBC_POC_ENABLED feature flag is on."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.vbc_poc import catalog
from app.vbc_poc import service as vbc
from app.vbc_poc.dataset import PATIENTS

router = APIRouter(prefix="/vbc", tags=["vbc-poc"])


class Actor(BaseModel):
    actor: str = Field("api-user", description="Who is acting; recorded in the audit log")


class GapUpdate(Actor):
    status: str | None = None
    owner: str | None = None
    due_date: str | None = Field(None, description="ISO date")
    disposition: str | None = Field(None, description=f"One of {', '.join(catalog.DISPOSITIONS)} or empty to clear")
    note: str = ""
    schedule_date: str | None = None
    schedule_ref: str | None = None


class EvidenceIn(Actor):
    event_date: str
    value: float | None = None
    value2: float | None = None
    code: str | None = None
    unit: str | None = None
    document_ref: str = ""
    note: str = ""
    result_flag: str = ""


class Verification(Actor):
    decision: Literal["accepted", "rejected"]
    note: str = ""


class OutreachIn(Actor):
    channel: Literal["SMS", "Phone", "Letter", "Portal"] = "SMS"


class OutreachApproval(Actor):
    text: str | None = None


class OutreachOutcome(Actor):
    outcome: Literal["SCHEDULED", "UNABLE_TO_CONTACT", "PATIENT_DECLINED"]
    schedule_date: str | None = None
    note: str = ""


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except vbc.NotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (vbc.VbcError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TimeoutError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/meta")
def meta():
    return {
        "rule_version": catalog.RULE_VERSION, "period": catalog.PERIOD.period_id, "disclaimer": catalog.DISCLAIMER,
        "positioning": catalog.POSITIONING, "workflow": catalog.WORKFLOW, "dispositions": catalog.DISPOSITIONS,
        "measures": {code: {"name": m.name, "purpose": m.purpose, "target": m.target, "age": m.age, "sex": m.sex}
                     for code, m in catalog.MEASURES.items()},
        "states": {code: info.label for code, info in catalog.MEASURE_STATES.items()},
        "rule_change_log": catalog.RULE_CHANGE_LOG, "limitations": catalog.LIMITATIONS,
    }


@router.post("/evaluate/{patient_id}")
def evaluate_patient(patient_id: str, body: Actor | None = None):
    return _call(vbc.evaluate_patient, patient_id, (body or Actor()).actor)


@router.post("/evaluate-population")
def evaluate_population(body: Actor | None = None):
    return _call(vbc.evaluate_population, (body or Actor()).actor)


@router.get("/patients")
def patients():
    state = vbc.ensure_initialized()
    return [{"patient_id": p.patient_id, "name": p.name, "cohort": p.cohort, "risk": state["risk"].get(p.patient_id, {}).get("category")}
            for p in PATIENTS]


@router.get("/patients/{patient_id}/360")
def patient_360(patient_id: str):
    return _call(vbc.patient_360, patient_id)


@router.get("/gaps")
def gaps(status: str | None = None, measure_id: str | None = None, owner: str | None = None, priority: str | None = None,
         include_closed: bool = False, overdue_only: bool = False):
    return _call(vbc.list_gaps, status=status, measure_id=measure_id, owner=owner, priority=priority,
                 include_closed=include_closed, overdue_only=overdue_only)


@router.get("/gaps/{gap_id}")
def gap_detail(gap_id: str):
    return _call(vbc.gap_detail, gap_id)


@router.patch("/gaps/{gap_id}")
def update_gap(gap_id: str, body: GapUpdate):
    return _call(vbc.update_gap, gap_id, body.actor, status=body.status, owner=body.owner, due_date=body.due_date,
                 disposition=body.disposition, note=body.note, schedule_date=body.schedule_date, schedule_ref=body.schedule_ref)


@router.post("/gaps/{gap_id}/evidence")
def attach_evidence(gap_id: str, body: EvidenceIn):
    return _call(vbc.attach_evidence, gap_id, body.actor, event_date=body.event_date, value=body.value, value2=body.value2,
                 code=body.code, unit=body.unit, document_ref=body.document_ref, note=body.note, result_flag=body.result_flag)


@router.post("/evidence/{event_id}/verify")
def verify_evidence(event_id: str, body: Verification):
    return _call(vbc.verify_evidence, event_id, body.actor, body.decision, body.note)


@router.post("/gaps/{gap_id}/outreach")
def draft_outreach(gap_id: str, body: OutreachIn):
    return _call(vbc.draft_outreach, gap_id, body.channel, body.actor)


@router.post("/outreach/{outreach_id}/approve")
def approve_outreach(outreach_id: str, body: OutreachApproval):
    return _call(vbc.approve_outreach, outreach_id, body.actor, body.text)


@router.post("/outreach/{outreach_id}/send")
def send_outreach(outreach_id: str, body: Actor):
    return _call(vbc.send_outreach, outreach_id, body.actor)


@router.post("/outreach/{outreach_id}/outcome")
def outreach_outcome(outreach_id: str, body: OutreachOutcome):
    return _call(vbc.record_outreach_outcome, outreach_id, body.actor, body.outcome, body.schedule_date, body.note)


@router.post("/care-plans/{patient_id}/approve")
def approve_plan(patient_id: str, body: Actor):
    return _call(vbc.approve_care_plan, patient_id, body.actor)


@router.post("/care-plans/{patient_id}/complete")
def complete_plan(patient_id: str, body: Actor):
    return _call(vbc.complete_care_plan, patient_id, body.actor)


@router.get("/dashboard")
def dashboard():
    return _call(vbc.dashboard)


@router.get("/audit")
def audit():
    return _call(vbc.audit_log)


@router.post("/reset")
def reset(body: Actor):
    return _call(vbc.reset_demo, body.actor)
