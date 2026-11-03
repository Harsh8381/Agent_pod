"""Agent orchestrator: runs agents under their tier policy and records every finding as a proposal.

  L1 findings execute immediately (reversible catalogue actions only) and can be reverted.
  L2 findings wait as proposals; approval runs the action under the approver's name.
  L3 findings are escalations; a person in the target role records the outcome. Nothing executes.

The caller (API or UI) passes a permission check for the signed-in person, so the same role matrix
governs agents and people. Each tier has a kill switch.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.hedis import store
from app.hedis.agents.base import TIERS, Finding, narrate
from app.hedis.agents.catalog import ACTIONS, AGENTS
from app.hedis.errors import Conflict, HedisError, NotFound, SegregationOfDuties

OPEN_STATUSES = ("PROPOSED", "ESCALATED")
PermissionCheck = Callable[[str, str], bool]


def _proposal(state: dict, spec, finding: Finding, status: str) -> dict:
    action = ACTIONS.get(finding.action)
    return {
        "proposal_id": store.next_id(state, "AGP"), "agent": spec.key, "agent_name": spec.name, "tier": spec.tier,
        "action": finding.action, "action_label": action.label if action else "Escalate to an expert", "target": finding.target,
        "title": finding.title, "rationale": finding.rationale, "narrative": narrate(finding), "evidence": finding.evidence,
        "payload": finding.payload, "confidence": finding.confidence, "severity": finding.severity, "dedupe_key": finding.dedupe_key,
        "status": status, "created_at": store.now_iso(), "required_permission": list(action.permission) if action else None,
        "escalate_to": list(finding.escalate_to or spec.escalates_to), "reversible": bool(action and action.reversible),
        "decided_by": None, "decided_at": None, "note": "", "result": None, "error": None,
    }


def _seen(state: dict, dedupe_key: str) -> bool:
    """Idempotency: an open proposal, or an L1 action already executed for the same input, is not repeated."""
    return any(p["dedupe_key"] == dedupe_key and (p["status"] in OPEN_STATUSES or p["status"] == "AUTO_EXECUTED")
               for p in state["agents"]["proposals"].values())


def run_agent(key: str, triggered_by: str, path: Path | None = None, poc_path: Path | None = None) -> dict:
    if key not in AGENTS:
        raise NotFound(f"Unknown agent {key}.")
    spec, detect = AGENTS[key]
    if not store.load(path)["agents"]["enabled"].get(spec.tier, True):
        raise Conflict(f"{TIERS[spec.tier].label} ({spec.tier}) agents are switched off.")
    findings = detect(path, poc_path)
    actor = f"agent:{key}"
    counts = {"findings": len(findings), "executed": 0, "proposed": 0, "escalated": 0, "skipped": 0, "failed": 0}
    to_execute = []
    with store.transaction(path) as state:
        for finding in findings:
            if _seen(state, finding.dedupe_key):
                counts["skipped"] += 1
                continue
            if spec.tier == "L3":
                if finding.action != "escalate":
                    raise HedisError(f"{spec.name} is an L3 agent and may only escalate.")
                proposal = _proposal(state, spec, finding, "ESCALATED")
                counts["escalated"] += 1
            else:
                action = ACTIONS.get(finding.action)
                if not action or spec.tier not in action.tiers or finding.action not in spec.actions:
                    raise HedisError(f"{spec.name} ({spec.tier}) is not allowed to {finding.action}.")
                if spec.tier == "L1":
                    proposal = _proposal(state, spec, finding, "EXECUTING")
                    to_execute.append(proposal["proposal_id"])
                else:
                    proposal = _proposal(state, spec, finding, "PROPOSED")
                    counts["proposed"] += 1
            state["agents"]["proposals"][proposal["proposal_id"]] = proposal
            store.audit(state, actor, f"agent.{proposal['status'].lower()}", "agent_proposal", proposal["proposal_id"],
                        new=proposal["status"], trigger=f"{spec.tier} run by {triggered_by}", evidence_ids=finding.evidence[:20],
                        details=finding.title)
    for proposal_id in to_execute:  # each L1 action runs in its own transaction, under the agent's identity
        outcome = _execute(proposal_id, actor, path, final_status="AUTO_EXECUTED")
        counts["executed" if outcome["status"] == "AUTO_EXECUTED" else "failed"] += 1
    with store.transaction(path) as state:
        run = {"run_id": store.next_id(state, "AGR"), "agent": key, "tier": spec.tier, "at": store.now_iso(), "triggered_by": triggered_by, **counts}
        state["agents"]["runs"].append(run)
        del state["agents"]["runs"][:-300]
        store.audit(state, actor, "agent.run", "agent", key, trigger=triggered_by,
                    details=", ".join(f"{name} {value}" for name, value in counts.items() if value))
    return run


def run_tier(tier: str, triggered_by: str, path: Path | None = None, poc_path: Path | None = None) -> list[dict]:
    order = [key for key, (spec, _) in AGENTS.items() if spec.tier == tier]
    return [run_agent(key, triggered_by, path, poc_path) for key in order]


def _execute(proposal_id: str, actor: str, path: Path | None, final_status: str) -> dict:
    proposal = store.load(path)["agents"]["proposals"][proposal_id]
    action = ACTIONS[proposal["action"]]
    try:
        result, error, status = action.handler(proposal["payload"], actor, path), None, final_status
    except HedisError as exc:  # workflow rule refused it: recorded, never raised past the orchestrator
        result, error, status = None, str(exc), "FAILED"
    except Exception as exc:  # infrastructure failure: recorded as FAILED so nothing is left half-executed
        result, error, status = None, f"{type(exc).__name__}: {exc}", "FAILED"
    with store.transaction(path) as state:
        stored = state["agents"]["proposals"][proposal_id]
        stored.update(status=status, result=_compact(result), error=error, executed_at=store.now_iso())
        store.audit(state, actor, "agent.action_executed" if status != "FAILED" else "agent.action_failed", "agent_proposal", proposal_id,
                    previous="EXECUTING" if final_status == "AUTO_EXECUTED" else "PROPOSED", new=status, trigger=proposal["action"],
                    details=error or proposal["title"])
        return stored


def _compact(result):
    if isinstance(result, dict):
        return {key: value for key, value in result.items() if key not in ("records", "items", "history")}
    return result


def decide(proposal_id: str, actor: str, decision: str, permitted: PermissionCheck, note: str = "", path: Path | None = None) -> dict:
    """Human decision on an L2 proposal. Approval runs the action under the approver's identity."""
    if actor.startswith("agent:"):
        raise SegregationOfDuties("Agents cannot approve proposals.")
    if decision not in ("approve", "reject"):
        raise HedisError("Decision must be approve or reject.")
    state = store.load(path)
    proposal = state["agents"]["proposals"].get(proposal_id)
    if not proposal:
        raise NotFound(f"Proposal {proposal_id} not found.")
    if proposal["tier"] != "L2" or proposal["status"] != "PROPOSED":
        raise Conflict(f"{proposal_id} is a {proposal['tier']} item in status {proposal['status']}; only open L2 proposals take a decision.")
    module, action = proposal["required_permission"]
    if decision == "approve" and not permitted(module, action):
        raise SegregationOfDuties(f"Approving needs {action} on {module}.")
    if decision == "reject":
        with store.transaction(path) as state:
            stored = state["agents"]["proposals"][proposal_id]
            stored.update(status="REJECTED", decided_by=actor, decided_at=store.now_iso(), note=note)
            store.audit(state, actor, "agent.proposal_rejected", "agent_proposal", proposal_id, previous="PROPOSED", new="REJECTED",
                        trigger="human decision", details=note or proposal["title"])
            return stored
    with store.transaction(path) as state:
        state["agents"]["proposals"][proposal_id].update(decided_by=actor, decided_at=store.now_iso(), note=note)
        store.audit(state, actor, "agent.proposal_approved", "agent_proposal", proposal_id, previous="PROPOSED", new="APPROVED",
                    trigger="human decision", details=note or proposal["title"])
    return _execute(proposal_id, actor, path, final_status="APPROVED")


def resolve_escalation(proposal_id: str, actor: str, actor_role: str, outcome: str, note: str, path: Path | None = None) -> dict:
    """An expert records what they decided. The escalation never executes anything itself."""
    if actor.startswith("agent:"):
        raise SegregationOfDuties("Agents cannot resolve escalations.")
    if outcome not in ("ACKNOWLEDGED", "RESOLVED", "NOT_AN_ISSUE"):
        raise HedisError("Outcome must be ACKNOWLEDGED, RESOLVED or NOT_AN_ISSUE.")
    if not note.strip():
        raise HedisError("Record what was decided.")
    with store.transaction(path) as state:
        proposal = state["agents"]["proposals"].get(proposal_id)
        if not proposal:
            raise NotFound(f"Escalation {proposal_id} not found.")
        if proposal["tier"] != "L3" or proposal["status"] not in ("ESCALATED", "ACKNOWLEDGED"):
            raise Conflict(f"{proposal_id} is not an open escalation.")
        if actor_role not in proposal["escalate_to"] and actor_role not in ("HEDIS Lead", "Super Admin"):
            raise SegregationOfDuties(f"This case is routed to {', '.join(proposal['escalate_to'])}.")
        previous = proposal["status"]
        proposal.update(status=outcome, decided_by=actor, decided_at=store.now_iso(), note=note.strip())
        store.audit(state, actor, "agent.escalation_" + outcome.lower(), "agent_proposal", proposal_id, previous=previous, new=outcome,
                    trigger="expert review", details=note.strip())
        return proposal


def revert(proposal_id: str, actor: str, path: Path | None = None) -> dict:
    """Undo an L1 action — the property that lets L1 run without a person in the loop."""
    from app.hedis import supplemental

    state = store.load(path)
    proposal = state["agents"]["proposals"].get(proposal_id)
    if not proposal or proposal["status"] != "AUTO_EXECUTED":
        raise Conflict("Only an executed L1 action can be reverted.")
    if proposal["action"] == "supplemental.build_draft":
        supplemental.discard_batch(proposal["result"]["batch_id"], actor, f"Reverted agent action {proposal_id}", path)
    with store.transaction(path) as state:
        stored = state["agents"]["proposals"][proposal_id]
        if stored["action"] == "dq.open_task":
            task = state["agents"]["dq_tasks"].get(stored["payload"]["key"])
            if task:
                task.update(status="CLOSED", closed_by=actor, closed_at=store.now_iso())
        elif stored["action"] == "gaps.set_priorities":
            state["agents"]["priorities"] = []
        stored.update(status="REVERTED", decided_by=actor, decided_at=store.now_iso())
        store.audit(state, actor, "agent.action_reverted", "agent_proposal", proposal_id, previous="AUTO_EXECUTED", new="REVERTED",
                    trigger="human", details=stored["title"])
        return stored


def set_tier(tier: str, enabled: bool, actor: str, reason: str = "", path: Path | None = None) -> dict:
    if tier not in TIERS:
        raise NotFound(f"Unknown tier {tier}.")
    with store.transaction(path) as state:
        previous = state["agents"]["enabled"].get(tier, True)
        state["agents"]["enabled"][tier] = bool(enabled)
        store.audit(state, actor, "agent.tier_switched", "agent_tier", tier, previous="on" if previous else "off",
                    new="on" if enabled else "off", trigger="kill switch", details=reason)
        return dict(state["agents"]["enabled"])


def view(path: Path | None = None) -> dict:
    state = store.load(path)
    proposals = sorted(state["agents"]["proposals"].values(), key=lambda p: p["proposal_id"], reverse=True)
    last_runs = {}
    for run in state["agents"]["runs"]:
        last_runs[run["agent"]] = run
    agents = [{"key": spec.key, "name": spec.name, "tier": spec.tier, "purpose": spec.purpose, "reads": list(spec.reads),
               "actions": [ACTIONS[a].label for a in spec.actions], "escalates_to": list(spec.escalates_to),
               "last_run": last_runs.get(spec.key), "open": sum(p["agent"] == spec.key and p["status"] in OPEN_STATUSES for p in proposals)}
              for spec, _ in AGENTS.values()]
    stats = {}
    for proposal in proposals:
        stats[proposal["status"]] = stats.get(proposal["status"], 0) + 1
    return {"tiers": {key: {"label": tier.label, "summary": tier.summary, "executes": tier.executes,
                            "enabled": state["agents"]["enabled"].get(key, True)} for key, tier in TIERS.items()},
            "agents": agents, "proposals": proposals, "stats": stats, "priorities": state["agents"]["priorities"],
            "dq_tasks": sorted(state["agents"]["dq_tasks"].values(), key=lambda t: t["opened_at"], reverse=True),
            "runs": list(reversed(state["agents"]["runs"][-50:]))}
