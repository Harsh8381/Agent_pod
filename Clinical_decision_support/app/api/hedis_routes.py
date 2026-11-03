"""HEDIS engine API (/hedis). Every route except the token endpoint requires a bearer token and one named permission."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import hedis  # noqa: F401  (package docstring documents the scope)
from app.hedis import agents, ncqa, qa, reconciliation, registry, store, supplemental, versions, workflow
from app.hedis.errors import Conflict, HedisError, NotFound, SegregationOfDuties
from app.security import rbac, tokens
from app.security.api import require
from app.security.rbac import Principal
from frontend import access_control as ac

router = APIRouter(prefix="/hedis", tags=["hedis-engine"])


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except NotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SegregationOfDuties as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except Conflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except HedisError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TimeoutError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class Credentials(BaseModel):
    username: str
    password: str


@router.post("/auth/token")
def issue_token(body: Credentials):
    """Exchange credentials for a short-lived bearer token. Failed attempts count toward account lockout."""
    store_ = rbac.load_store()
    try:
        user = ac.authenticate(store_, body.username, body.password)
    except ac.AuthError as error:
        ac.save_store(rbac.store_path(), store_)
        raise HTTPException(status_code=401, detail=str(error)) from error
    ac.save_store(rbac.store_path(), store_)
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="Change the temporary password in the app before using the API.")
    token = tokens.issue(user["username"], user["role"])
    claims = tokens.verify(token)
    return {"access_token": token, "token_type": "bearer", "expires_in": claims["exp"] - claims["iat"], "role": user["role"]}


@router.get("/me")
def me(principal: Principal = Depends(require("registry:read"))):
    store_ = rbac.load_store()
    return {"username": principal.username, "name": principal.name, "role": principal.role, "phi": rbac.can_see_phi(principal),
            "permissions": sorted(name for name in rbac.PERMISSIONS if rbac.allowed(principal, name, store_))}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class Note(BaseModel):
    note: str = ""


@router.get("/registry")
def get_registry(principal: Principal = Depends(require("registry:read"))):
    phi = rbac.can_see_phi(principal)
    with store.transaction() as state:
        store.log_access(state, principal.actor, "registry", "identified" if phi else "masked")
    return _call(registry.view, phi=phi)


@router.post("/registry/snapshots")
def publish_snapshot(body: Note | None = None, principal: Principal = Depends(require("registry:publish"))):
    return _call(registry.publish_snapshot, principal.actor, (body or Note()).note)


@router.get("/registry/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, principal: Principal = Depends(require("registry:read"))):
    rows = _call(registry.snapshot_rows, snapshot_id)
    return rows if rbac.can_see_phi(principal) else [registry.mask(row) for row in rows]


@router.post("/registry/lock")
def lock_period(principal: Principal = Depends(require("registry:lock"))):
    return _call(registry.lock_period, principal.actor)


@router.post("/registry/reopen")
def reopen_period(body: Note, principal: Principal = Depends(require("registry:lock"))):
    return _call(registry.reopen_period, principal.actor, body.note)


# ---------------------------------------------------------------------------
# Supplemental submission
# ---------------------------------------------------------------------------


class BatchIn(BaseModel):
    payer: Literal["ATH", "CGH"]


class AckIn(BaseModel):
    text: str = Field(..., description="Payer acknowledgement file content")


class CorrectionIn(BaseModel):
    field: Literal["rendering_npi"] = "rendering_npi"
    value: str
    reason: str


@router.get("/supplemental")
def get_supplemental(principal: Principal = Depends(require("supplemental:read"))):
    return _call(supplemental.view)


@router.post("/supplemental/batches")
def build_batch(body: BatchIn, principal: Principal = Depends(require("supplemental:build"))):
    return _call(supplemental.build_batch, body.payer, principal.actor)


@router.post("/supplemental/batches/{batch_id}/sign")
def sign_batch(batch_id: str, principal: Principal = Depends(require("supplemental:sign"))):
    return _call(supplemental.sign_batch, batch_id, principal.actor)


@router.post("/supplemental/batches/{batch_id}/submit")
def submit_batch(batch_id: str, principal: Principal = Depends(require("supplemental:sign"))):
    return _call(supplemental.submit_batch, batch_id, principal.actor)


@router.post("/supplemental/batches/{batch_id}/discard")
def discard_batch(batch_id: str, body: Note | None = None, principal: Principal = Depends(require("supplemental:build"))):
    return _call(supplemental.discard_batch, batch_id, principal.actor, (body or Note()).note)


@router.get("/supplemental/batches/{batch_id}/file", response_class=PlainTextResponse)
def batch_file(batch_id: str, principal: Principal = Depends(require("supplemental:read"))):
    batch = next((b for b in supplemental.view()["batches"] if b["batch_id"] == batch_id), None)
    if not batch:
        raise HTTPException(status_code=404, detail=f"Supplemental batch {batch_id} not found.")
    return supplemental.render_file(batch)[0]


@router.post("/supplemental/batches/{batch_id}/ack")
def ingest_ack(batch_id: str, body: AckIn, principal: Principal = Depends(require("supplemental:build"))):
    return _call(supplemental.ingest_ack, batch_id, body.text, principal.actor)


@router.post("/supplemental/batches/{batch_id}/simulated-ack", response_class=PlainTextResponse)
def simulated_ack(batch_id: str, principal: Principal = Depends(require("supplemental:build"))):
    """Demo payer response for a submitted batch; ingest it with POST .../ack."""
    return _call(supplemental.simulate_ack, batch_id)


@router.post("/supplemental/records/{record_key}/correction")
def correct_record(record_key: str, body: CorrectionIn, principal: Principal = Depends(require("supplemental:build"))):
    return _call(supplemental.apply_correction, record_key, body.field, body.value, principal.actor, body.reason)


# ---------------------------------------------------------------------------
# Payer reconciliation
# ---------------------------------------------------------------------------


class ReconcileIn(BaseModel):
    payer: Literal["ATH", "CGH"]
    file_text: str | None = Field(None, description="Payer member-level CSV; omit to use the synthetic payer file")
    source_name: str = "API upload"


class DisputeIn(BaseModel):
    run_id: str
    item_id: str
    note: str


class ResolveDisputeIn(BaseModel):
    outcome: Literal["UPHELD", "DENIED", "WITHDRAWN"]
    note: str = ""


class TaskIn(BaseModel):
    run_id: str
    item_id: str


@router.get("/reconciliation")
def get_reconciliation(principal: Principal = Depends(require("reconciliation:read"))):
    return reconciliation.view()


@router.get("/reconciliation/payer-file/{payer}", response_class=PlainTextResponse)
def payer_file(payer: Literal["ATH", "CGH"], principal: Principal = Depends(require("reconciliation:run"))):
    return _call(reconciliation.generate_payer_file, payer)


@router.post("/reconciliation/runs")
def run_reconciliation(body: ReconcileIn, principal: Principal = Depends(require("reconciliation:run"))):
    text = body.file_text or _call(reconciliation.generate_payer_file, body.payer)
    source = body.source_name if body.file_text else "synthetic payer file"
    return _call(reconciliation.reconcile, body.payer, text, principal.actor, source)


@router.get("/reconciliation/runs/{run_id}")
def get_run(run_id: str, principal: Principal = Depends(require("reconciliation:read"))):
    run = store.load()["reconciliation"]["runs"].get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Reconciliation run {run_id} not found.")
    return run


@router.post("/reconciliation/disputes")
def open_dispute(body: DisputeIn, principal: Principal = Depends(require("reconciliation:dispute"))):
    return _call(reconciliation.open_dispute, body.run_id, body.item_id, principal.actor, body.note)


@router.post("/reconciliation/disputes/{dispute_id}/resolve")
def resolve_dispute(dispute_id: str, body: ResolveDisputeIn, principal: Principal = Depends(require("reconciliation:dispute"))):
    return _call(reconciliation.resolve_dispute, dispute_id, principal.actor, body.outcome, body.note)


@router.post("/reconciliation/tasks")
def create_task(body: TaskIn, principal: Principal = Depends(require("reconciliation:run"))):
    return _call(reconciliation.create_task, body.run_id, body.item_id, principal.actor)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = ""


class EscalationIn(BaseModel):
    outcome: Literal["ACKNOWLEDGED", "RESOLVED", "NOT_AN_ISSUE"]
    note: str


class TierSwitch(BaseModel):
    enabled: bool
    reason: str = ""


@router.get("/agents")
def get_agents(principal: Principal = Depends(require("agents:read"))):
    return agents.view()


@router.post("/agents/{agent_key}/run")
def run_agent(agent_key: str, principal: Principal = Depends(require("agents:run"))):
    return _call(agents.run_agent, agent_key, principal.actor)


@router.post("/agents/tiers/{tier}/run")
def run_tier(tier: Literal["L1", "L2", "L3"], principal: Principal = Depends(require("agents:run"))):
    return _call(agents.run_tier, tier, principal.actor)


@router.post("/agents/proposals/{proposal_id}/decision")
def decide(proposal_id: str, body: DecisionIn, principal: Principal = Depends(require("agents:decide"))):
    store_ = rbac.load_store()
    return _call(agents.decide, proposal_id, principal.actor, body.decision,
                 lambda module, action: rbac.module_allowed(principal, module, action, store_), body.note)


@router.post("/agents/escalations/{proposal_id}/resolve")
def resolve_escalation(proposal_id: str, body: EscalationIn, principal: Principal = Depends(require("agents:decide"))):
    return _call(agents.resolve_escalation, proposal_id, principal.actor, principal.role, body.outcome, body.note)


@router.post("/agents/proposals/{proposal_id}/revert")
def revert(proposal_id: str, principal: Principal = Depends(require("agents:run"))):
    return _call(agents.revert, proposal_id, principal.actor)


@router.put("/agents/tiers/{tier}")
def switch_tier(tier: Literal["L1", "L2", "L3"], body: TierSwitch, principal: Principal = Depends(require("agents:admin"))):
    return _call(agents.set_tier, tier, body.enabled, principal.actor, body.reason)


# ---------------------------------------------------------------------------
# NCQA readiness and audit
# ---------------------------------------------------------------------------


class AttestIn(BaseModel):
    note: str
    valid_days: int = Field(365, ge=30, le=730)


@router.get("/ncqa")
def get_readiness(principal: Principal = Depends(require("ncqa:read"))):
    return ncqa.evaluate()


@router.post("/ncqa/assessments")
def assess(principal: Principal = Depends(require("ncqa:assess"))):
    return _call(ncqa.record_assessment, principal.actor)


@router.post("/ncqa/attestations/{control_id}")
def attest(control_id: str, body: AttestIn, principal: Principal = Depends(require("ncqa:attest"))):
    return _call(ncqa.attest, control_id, principal.actor, principal.role, body.note, body.valid_days)


@router.get("/ncqa/evidence-pack", response_class=PlainTextResponse)
def evidence_pack(format: Literal["markdown", "json"] = Query("markdown"), principal: Principal = Depends(require("ncqa:read"))):
    return ncqa.evidence_pack()[format]


@router.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=2000), principal: Principal = Depends(require("ncqa:read"))):
    state = store.load()
    intact, broken_at = store.verify_chain(state)
    return {"chain_intact": intact, "broken_at": broken_at, "events": state["audit"][-limit:], "access_log": state["access_log"][-limit:]}


# ---------------------------------------------------------------------------
# Evidence & quality: cross-source normalization, supplied vs calculated QA
# ---------------------------------------------------------------------------


class SignOffIn(BaseModel):
    decision: Literal["ACCEPTED", "REJECTED"]
    note: str


@router.get("/evidence/normalization")
def normalization_report(principal: Principal = Depends(require("evidence:read"))):
    from app.hedis import normalizer

    result = normalizer.normalize(versions._workflow_evidence(store.load()))
    return {key: value for key, value in result.items() if key not in ("evidence", "by_patient")}


@router.get("/evidence/patients/{patient_id}")
def patient_evidence(patient_id: str, principal: Principal = Depends(require("evidence:read"))):
    from app.hedis import normalizer

    records = normalizer.normalize(versions._workflow_evidence(store.load()))["by_patient"].get(patient_id)
    if records is None:
        raise HTTPException(status_code=404, detail=f"No evidence for {patient_id}.")
    return [record.to_dict() for record in records]


@router.get("/qa")
def qa_compare(principal: Principal = Depends(require("evidence:read"))):
    return _call(qa.compare)


@router.post("/qa/runs")
def qa_run(principal: Principal = Depends(require("evidence:run"))):
    return _call(qa.record_run, principal.actor)["run"]


@router.post("/qa/runs/{run_id}/signoff")
def qa_signoff(run_id: str, body: SignOffIn, principal: Principal = Depends(require("qa:signoff"))):
    return _call(qa.sign_off, run_id, principal.actor, body.decision, body.note)


# ---------------------------------------------------------------------------
# Care-gap management: review-to-closure workflow
# ---------------------------------------------------------------------------


class TransitionIn(BaseModel):
    to: str
    fields: dict = Field(default_factory=dict, description="note, assignee, channel, schedule_date, service_date, value, value2, result_flag, ...")


class DispositionIn(BaseModel):
    disposition: Literal["PATIENT_DECLINED", "UNABLE_TO_CONTACT", "EXCLUSION_CANDIDATE"]
    note: str


@router.get("/workflow")
def workflow_summary(principal: Principal = Depends(require("workflow:read"))):
    return _call(workflow.summary)


@router.get("/workflow/gaps")
def workflow_gaps(gap_type: str | None = None, status: str | None = None, measure_id: str | None = None, overdue_only: bool = False,
                  include_closed: bool = False, principal: Principal = Depends(require("workflow:read"))):
    return _call(workflow.list_gaps, gap_type=gap_type, status=status, measure_id=measure_id, overdue_only=overdue_only,
                 include_closed=include_closed)


@router.get("/workflow/gaps/{gap_id}")
def workflow_gap(gap_id: str, principal: Principal = Depends(require("workflow:read"))):
    return _call(workflow.detail, gap_id)


@router.post("/workflow/gaps/{gap_id}/transition")
def workflow_transition(gap_id: str, body: TransitionIn, principal: Principal = Depends(require("workflow:edit"))):
    store_ = rbac.load_store()
    return _call(workflow.transition, gap_id, body.to, principal.actor, principal.role,
                 lambda module, action: rbac.module_allowed(principal, module, action, store_), body.fields)


@router.post("/workflow/gaps/{gap_id}/disposition")
def workflow_disposition(gap_id: str, body: DispositionIn, principal: Principal = Depends(require("workflow:edit"))):
    store_ = rbac.load_store()
    return _call(workflow.close_with_disposition, gap_id, body.disposition, principal.actor,
                 lambda module, action: rbac.module_allowed(principal, module, action, store_), body.note)


# ---------------------------------------------------------------------------
# Governance: measure version control center
# ---------------------------------------------------------------------------


class DraftIn(BaseModel):
    based_on: str
    changes: dict = Field(..., description='{"GSD.numerator.target_lt": 9.0}')
    rationale: str


class ActivateIn(BaseModel):
    effective_date: str | None = None


class RollbackIn(BaseModel):
    to_version: str
    reason: str


@router.get("/versions")
def versions_view(principal: Principal = Depends(require("versions:read"))):
    return versions.view()


@router.get("/versions/diff")
def versions_diff(a: str, b: str, principal: Principal = Depends(require("versions:read"))):
    return _call(versions.diff, a, b)


@router.post("/versions")
def versions_draft(body: DraftIn, principal: Principal = Depends(require("versions:draft"))):
    return _call(versions.create_draft, body.based_on, body.changes, principal.actor, body.rationale)


@router.post("/versions/{version_id}/submit")
def versions_submit(version_id: str, principal: Principal = Depends(require("versions:draft"))):
    return _call(versions.submit, version_id, principal.actor)


@router.post("/versions/{version_id}/impact")
def versions_impact(version_id: str, principal: Principal = Depends(require("versions:draft"))):
    return _call(versions.analyze_impact, version_id, principal.actor)


@router.post("/versions/{version_id}/approve")
def versions_approve(version_id: str, body: Note | None = None, principal: Principal = Depends(require("versions:approve"))):
    return _call(versions.approve, version_id, principal.actor, (body or Note()).note)


@router.post("/versions/{version_id}/reject")
def versions_reject(version_id: str, body: Note, principal: Principal = Depends(require("versions:approve"))):
    return _call(versions.reject, version_id, principal.actor, body.note)


@router.post("/versions/{version_id}/activate")
def versions_activate(version_id: str, body: ActivateIn | None = None, principal: Principal = Depends(require("versions:approve"))):
    return _call(versions.activate, version_id, principal.actor, (body or ActivateIn()).effective_date)


@router.post("/versions/rollback")
def versions_rollback(body: RollbackIn, principal: Principal = Depends(require("versions:approve"))):
    return _call(versions.rollback, body.to_version, principal.actor, body.reason)
