"""The action catalogue and the agents that use it, one section per tier."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.hedis import reconciliation, registry, store, supplemental
from app.hedis.agents.base import ActionDef, AgentSpec, Finding
from app.hedis.config import NON_STANDARD_SUPPLEMENTAL, PAYERS, PRACTITIONERS
from app.hedis.errors import Conflict
from app.vbc_poc import repository as poc_repo
from app.vbc_poc import service as vbc

SUPPLEMENTAL_MODULE = "Supplemental Submission"
RECONCILIATION_MODULE = "Payer Reconciliation"
REGISTRY_MODULE = "HEDIS Registry"

# ---------------------------------------------------------------------------
# Action handlers: (payload, actor, path) -> result
# ---------------------------------------------------------------------------


def _open_dq_task(payload: dict, actor: str, path: Path | None) -> dict:
    with store.transaction(path) as state:
        tasks = state["agents"]["dq_tasks"]
        task = tasks.get(payload["key"])
        if task and task["status"] == "OPEN":
            return task
        task = {"key": payload["key"], "code": payload["code"], "registry_key": payload["registry_key"], "detail": payload["detail"],
                "severity": payload["severity"], "status": "OPEN", "opened_by": actor, "opened_at": store.now_iso()}
        tasks[payload["key"]] = task
        store.audit(state, actor, "agent.dq_task_opened", "dq_task", payload["key"], new="OPEN", trigger="agent", details=payload["detail"])
        return task


def _set_priorities(payload: dict, actor: str, path: Path | None) -> dict:
    with store.transaction(path) as state:
        previous = len(state["agents"]["priorities"])
        state["agents"]["priorities"] = payload["items"]
        state["agents"]["priorities_at"] = store.now_iso()
        store.audit(state, actor, "agent.priorities_set", "worklist", "registry-gaps", previous=str(previous), new=str(len(payload["items"])),
                    trigger="agent", details="Top items: " + ", ".join(f"{i['member_id']}·{i['measure_id']}" for i in payload["items"][:5]))
    return {"items": len(payload["items"])}


def _build_draft(payload: dict, actor: str, path: Path | None) -> dict:
    batch = supplemental.build_batch(payload["payer"], actor, path)
    return {"batch_id": batch["batch_id"], "records": len(batch["records"])}


def _apply_correction(payload: dict, actor: str, path: Path | None) -> dict:
    return supplemental.apply_correction(payload["record_key"], payload["field"], payload["value"], actor, payload["reason"], path)


def _open_dispute(payload: dict, actor: str, path: Path | None) -> dict:
    return reconciliation.open_dispute(payload["run_id"], payload["item_id"], actor, payload["note"], path)


def _create_task(payload: dict, actor: str, path: Path | None) -> dict:
    return reconciliation.create_task(payload["run_id"], payload["item_id"], actor, path)


ACTIONS = {
    "dq.open_task": ActionDef("dq.open_task", ("L1",), (REGISTRY_MODULE, "Edit"), True, _open_dq_task, "Open a data-quality task"),
    "gaps.set_priorities": ActionDef("gaps.set_priorities", ("L1",), (REGISTRY_MODULE, "Edit"), True, _set_priorities, "Set the gap worklist order"),
    "supplemental.build_draft": ActionDef("supplemental.build_draft", ("L1",), (SUPPLEMENTAL_MODULE, "Edit"), True, _build_draft,
                                          "Assemble a draft supplemental batch (unsigned)"),
    "supplemental.apply_correction": ActionDef("supplemental.apply_correction", ("L2",), (SUPPLEMENTAL_MODULE, "Edit"), True,
                                               _apply_correction, "Correct a payer-rejected supplemental record"),
    "reconciliation.open_dispute": ActionDef("reconciliation.open_dispute", ("L2",), (RECONCILIATION_MODULE, "Approve"), False,
                                             _open_dispute, "Open a payer dispute"),
    "reconciliation.create_task": ActionDef("reconciliation.create_task", ("L2",), (RECONCILIATION_MODULE, "Edit"), True,
                                            _create_task, "Route a reconciliation item to its owner"),
}


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# L1 — autonomous
# ---------------------------------------------------------------------------


def dq_sentinel(path: Path | None, poc_path: Path | None) -> list[Finding]:
    rows = registry.build_rows(poc_path)
    findings = [
        Finding("dq.open_task", issue["key"], f"{issue['code']} on {issue['key']}", f"Registry integrity check {issue['code']}: {issue['detail']}.",
                _key("dq", issue["code"], issue["key"]), {"key": f"{issue['code']}|{issue['key']}", "code": issue["code"],
                                                          "registry_key": issue["key"], "detail": issue["detail"], "severity": issue["severity"]},
                severity="high" if issue["severity"] == "block" else "low")
        for issue in registry.issues(rows)
    ]
    for record in supplemental.candidates(None, path, poc_path):
        for error in record["errors"]:
            findings.append(Finding("dq.open_task", record["registry_key"], f"{error['code']} on supplemental record {record['record_key']}",
                                    f"Supplemental record for {record['member_id']} · {record['measure_id']} fails {error['code']}: {error['detail']}.",
                                    _key("dq-sup", error["code"], record["record_key"]),
                                    {"key": f"{error['code']}|{record['record_key']}", "code": error["code"], "registry_key": record["registry_key"],
                                     "detail": error["detail"], "severity": "block"}, severity="high"))
    return findings


MEASURE_WEIGHT = {"GSD-P": 3, "BPD-P": 3, "BCS-P": 1, "COL-P": 1}  # outcome measures weigh triple, as in Star Ratings
STATE_EFFORT = {"EVIDENCE_REVIEW": 6, "COMPLETED_PENDING_VERIFICATION": 6, "OPEN_GAP": 5, "DATA_QUALITY_ISSUE": 4, "OUTCOME_NOT_CONTROLLED": 3}


def gap_prioritizer(path: Path | None, poc_path: Path | None) -> list[Finding]:
    rows = [row for row in registry.build_rows(poc_path) if row["in_denominator"] and not row["numerator_met"]]
    items = []
    for row in rows:
        weight, effort = MEASURE_WEIGHT.get(row["measure_id"], 1), STATE_EFFORT.get(row["state"], 2)
        score = weight * 10 + effort
        items.append({"member_id": row["member_id"], "measure_id": row["measure_id"], "state": row["state"], "payer": row["payer"],
                      "score": score, "why": f"weight {weight} (outcome measures x3) + closability {effort} ({row['state_label'].lower()})"})
    items.sort(key=lambda item: (-item["score"], item["member_id"], item["measure_id"]))
    if not items:
        return []
    return [Finding("gaps.set_priorities", "registry-gaps", f"Re-rank {len(items)} open registry gaps",
                    f"{len(items)} members are in a denominator without a compliant record. Ranked by measure weight and how quickly the "
                    f"gap can close (evidence awaiting review first). Top: {items[0]['member_id']} · {items[0]['measure_id']} ({items[0]['why']}).",
                    _key("prio", [(i["member_id"], i["measure_id"], i["score"]) for i in items]), {"items": items}, severity="low")]


def supplemental_preparer(path: Path | None, poc_path: Path | None) -> list[Finding]:
    state = store.load(path)
    open_drafts = {b["payer"] for b in state["supplemental"]["batches"].values() if b["status"] == "DRAFT"}
    findings = []
    for payer, spec in PAYERS.items():
        ready = [r for r in supplemental.candidates(payer, path, poc_path) if r["submittable"]]
        if not ready or payer in open_drafts:
            continue
        warned = sum(bool(r["warnings"]) for r in ready)
        findings.append(Finding("supplemental.build_draft", payer, f"Draft {len(ready)} supplemental record(s) for {spec.name}",
                                f"{len(ready)} compliant registry rows rely on data {spec.name} cannot see in claims and pass every file-spec check"
                                + (f"; {warned} carry a roster warning the signer should review" if warned else "") +
                                ". The draft is unsigned: a HEDIS lead must sign before anything is sent.",
                                _key("draft", payer, sorted(r["record_key"] for r in ready)), {"payer": payer},
                                evidence=[r["evidence_id"] for r in ready]))
    return findings


# ---------------------------------------------------------------------------
# L2 — assisted (propose, a person approves)
# ---------------------------------------------------------------------------


def rejection_resolver(path: Path | None, poc_path: Path | None) -> list[Finding]:
    findings = []
    for record in supplemental.candidates(None, path, poc_path):
        if record["status"] != "NEEDS_CORRECTION" or record["reason_code"] != "R11":
            continue
        payer = PAYERS[record["payer"]]
        practitioner = PRACTITIONERS.get(record["provider"], {})
        group = practitioner.get("group_npi")
        if group and group in payer.roster_npis:
            findings.append(Finding(
                "supplemental.apply_correction", record["record_key"],
                f"Bill {record['member_id']} · {record['measure_id']} under the group NPI",
                f"{payer.name} rejected the record (R11): rendering NPI {record['rendering_npi']} ({record['provider']}) is not on its roster. "
                f"The practice group NPI {group} is on the roster and covers {record['provider']} (TIN {practitioner.get('tin')}). "
                f"Approving corrects the record so it can be resubmitted.",
                _key("fix", record["record_key"], group),
                {"record_key": record["record_key"], "field": "rendering_npi", "value": group,
                 "reason": f"R11 roster rejection: bill under group NPI {group}"},
                evidence=[record["evidence_id"]], confidence=0.95))
    return findings


def reconciliation_analyst(path: Path | None, poc_path: Path | None) -> list[Finding]:
    state = store.load(path)
    findings = []
    for payer, run in reconciliation.latest_runs(state).items():
        for item in run["items"]:
            if item["category"] == "AGREE":
                continue
            if item["disputable"]:
                note = (f"{item['root_cause_label']}: our registry shows {item['member_id']} {item['measure_id']} as "
                        f"{(item['internal_status'] or '').replace('_', ' ').lower()}; payer file {run['run_id']} reports "
                        f"{(item['payer_status'] or 'no row').replace('_', ' ').lower()}.")
                findings.append(Finding("reconciliation.open_dispute", item["item_id"], f"Dispute {item['member_id']} · {item['measure_id']} with {PAYERS[payer].name}",
                                        note + " A dispute is the documented remedy for this root cause.",
                                        _key("dispute", payer, item["item_id"], item["root_cause"]),
                                        {"run_id": run["run_id"], "item_id": item["item_id"], "note": note}, confidence=0.8, severity="high"))
            else:
                findings.append(Finding("reconciliation.create_task", item["item_id"], f"Route {item['member_id']} · {item['measure_id']} to {item['owner_role']}",
                                        f"{item['category_label']} — {item['root_cause_label']}. Recommended: {item['action']}.",
                                        _key("task", payer, item["item_id"], item["root_cause"]),
                                        {"run_id": run["run_id"], "item_id": item["item_id"]}, confidence=0.9,
                                        severity="high" if item["priority"] == "high" else "medium"))
    return findings


# ---------------------------------------------------------------------------
# L3 — escalation only
# ---------------------------------------------------------------------------


def clinical_escalation(path: Path | None, poc_path: Path | None) -> list[Finding]:
    findings = []
    for row in registry.build_rows(poc_path):
        if row["state"] == "OUTCOME_NOT_CONTROLLED":
            findings.append(Finding("escalate", row["key"], f"Clinical review: {row['measure']} not controlled for {row['member_id']}",
                                    f"{row['reason']}. Intensifying treatment is a clinical decision; this agent does not recommend therapy.",
                                    _key("clin", row["key"], row["fingerprint"]), {"registry_key": row["key"], "patient_id": row["patient_id"]},
                                    evidence=row["evidence_ids"], escalate_to=("Physician / APP",), severity="high"))
        elif row["state"] == "FOLLOW_UP_REQUIRED":
            findings.append(Finding("escalate", row["key"], f"Diagnostic follow-up owed for {row['member_id']}",
                                    f"{row['reason']}. The screening counts, but a positive result needs a clinician-ordered diagnostic follow-up.",
                                    _key("fu", row["key"], row["fingerprint"]), {"registry_key": row["key"], "patient_id": row["patient_id"]},
                                    evidence=row["evidence_ids"], escalate_to=("Physician / APP",), severity="high"))
    return findings


def audit_sentinel(path: Path | None, poc_path: Path | None) -> list[Finding]:
    state = store.load(path)
    findings = []
    intact, broken_at = store.verify_chain(state)
    if not intact:
        findings.append(Finding("escalate", "hedis-audit", "HEDIS audit chain broken", f"Hash chain fails at event {broken_at}; history may have been edited.",
                                _key("chain-hedis", broken_at), {}, escalate_to=("HEDIS Lead", "Auditor"), severity="critical"))
    poc_intact, poc_broken = poc_repo.verify_chain(vbc.ensure_initialized(poc_path))
    if not poc_intact:
        findings.append(Finding("escalate", "poc-audit", "Measure-engine audit chain broken", f"Hash chain fails at event {poc_broken}.",
                                _key("chain-poc", poc_broken), {}, escalate_to=("HEDIS Lead", "Auditor"), severity="critical"))
    locked = state["registry"]["locked"]
    if locked:
        current = registry.content_hash(registry.build_rows(poc_path))
        if current != locked["content_hash"]:
            findings.append(Finding("escalate", "registry", "Registry changed after the period was locked",
                                    f"Locked on {locked['snapshot_id']} ({locked['content_hash'][:12]}), current hash {current[:12]}. "
                                    "Reported rates may no longer match the locked registry.",
                                    _key("drift", locked["content_hash"], current), {}, escalate_to=("HEDIS Lead", "Auditor"), severity="critical"))
    for batch in state["supplemental"]["batches"].values():
        if batch["signed_by"] and batch["signed_by"] == batch["built_by"]:
            findings.append(Finding("escalate", batch["batch_id"], f"Segregation-of-duties breach on {batch['batch_id']}",
                                    f"{batch['batch_id']} was built and signed by {batch['signed_by']}.", _key("sod", batch["batch_id"]), {},
                                    escalate_to=("HEDIS Lead", "Auditor"), severity="critical"))
        unverified = [r for r in batch["records"] if r["source_class"] == NON_STANDARD_SUPPLEMENTAL and r.get("psv") != "VERIFIED"]
        if batch["status"] not in ("DRAFT", "DISCARDED") and unverified:
            findings.append(Finding("escalate", batch["batch_id"], f"Non-standard data without PSV in {batch['batch_id']}",
                                    f"{len(unverified)} submitted record(s) lack primary-source verification (IS 5.0).",
                                    _key("psv", batch["batch_id"], len(unverified)), {}, escalate_to=("HEDIS Lead", "Auditor"), severity="high"))
    for late in supplemental.overdue_acks(state):
        findings.append(Finding("escalate", late["batch_id"], f"{late['batch_id']} acknowledgement overdue",
                                f"{PAYERS[late['payer']].name} acknowledgement was due {late['due']}; numerators in this batch are not yet credited.",
                                _key("ack", late["batch_id"]), {}, escalate_to=("HEDIS Lead",), severity="medium"))
    return findings


AGENTS = {
    "dq_sentinel": (AgentSpec("dq_sentinel", "Registry quality sentinel", "L1",
                              "Runs registry and file-spec integrity checks and opens a task for each failure.",
                              ("registry", "supplemental records"), ("dq.open_task",)), dq_sentinel),
    "gap_prioritizer": (AgentSpec("gap_prioritizer", "Gap prioritizer", "L1",
                                  "Orders open registry gaps by measure weight and how quickly each can close.",
                                  ("registry",), ("gaps.set_priorities",)), gap_prioritizer),
    "supplemental_preparer": (AgentSpec("supplemental_preparer", "Supplemental preparer", "L1",
                                        "Assembles validated supplemental records into an unsigned draft batch per payer.",
                                        ("registry", "supplemental ledger"), ("supplemental.build_draft",)), supplemental_preparer),
    "rejection_resolver": (AgentSpec("rejection_resolver", "Rejection resolver", "L2",
                                     "Diagnoses payer rejections and proposes the record correction for approval.",
                                     ("supplemental ledger", "practitioner roster"), ("supplemental.apply_correction",)), rejection_resolver),
    "reconciliation_analyst": (AgentSpec("reconciliation_analyst", "Reconciliation analyst", "L2",
                                         "Turns reconciliation differences into routed tasks or drafted disputes for approval.",
                                         ("reconciliation runs",), ("reconciliation.create_task", "reconciliation.open_dispute")), reconciliation_analyst),
    "clinical_escalation": (AgentSpec("clinical_escalation", "Clinical escalation", "L3",
                                      "Packages uncontrolled outcomes and owed diagnostic follow-ups for a clinician.",
                                      ("registry",), (), ("Physician / APP",)), clinical_escalation),
    "audit_sentinel": (AgentSpec("audit_sentinel", "Audit-risk sentinel", "L3",
                                 "Watches for conditions an NCQA auditor would cite and escalates them.",
                                 ("audit chains", "registry lock", "batches"), (), ("HEDIS Lead", "Auditor")), audit_sentinel),
}

__all__ = ["ACTIONS", "AGENTS", "Conflict"]
