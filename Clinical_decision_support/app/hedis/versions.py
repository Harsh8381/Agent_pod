"""Measure version control center (Governance workstream).

Measure logic is a versioned specification, not code. A change moves through

    DRAFT -> IN_REVIEW -> APPROVED -> ACTIVE -> SUPERSEDED        (or REJECTED)

with an impact analysis on the real population before approval, four eyes (approver != author), an
effective date on activation and a rollback that re-activates a superseded version under approval.
Every result the calculator produces is stamped with the version that produced it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from pathlib import Path

from app.hedis import calculator, normalizer, store
from app.hedis.errors import Conflict, HedisError, NotFound, SegregationOfDuties

BASELINE_ID = "2026.1"
BASELINE_SPEC = {
    "measures": {
        "GSD": {"name": "Glycemic Status Assessment for Patients With Diabetes",
                "population": {"condition": "diabetes", "age_min": 18, "age_max": 75, "sex": None},
                "numerator": {"concept": "hba1c", "target_lt": 8.0}},
        "BPD-E": {"name": "Blood Pressure Control for Patients With Diabetes",
                  "population": {"condition": "diabetes", "age_min": 18, "age_max": 75, "sex": None},
                  "numerator": {"concept": "bp", "systolic_lt": 140, "diastolic_lt": 90, "same_day_rule": "review"}},
        "KED": {"name": "Kidney Health Evaluation for Patients With Diabetes",
                "population": {"condition": "diabetes", "age_min": 18, "age_max": 75, "sex": None},
                "numerator": {"components": ["egfr", "uacr"]}},
        "BCS-E": {"name": "Breast Cancer Screening",
                  "population": {"condition": None, "age_min": 50, "age_max": 74, "sex": "F"},
                  "numerator": {"concept": "mammogram", "lookback_months": 27, "report_required": True}},
        "COL-E": {"name": "Colorectal Cancer Screening",
                  "population": {"condition": None, "age_min": 45, "age_max": 75, "sex": None},
                  "numerator": {"colonoscopy_years": 10, "positive_fit_requires_followup": True}},
        "TRC": {"name": "Transitions of Care",
                "population": {"condition": None, "age_min": 18, "age_max": None, "sex": None, "encounter": "inpatient_stay"},
                "numerator": {"discharge_summary_required": True, "followup_days": 30}},
    }
}
# What each parameter may be set to; drafts are validated against this, so a typo never reaches production.
PARAMETER_RULES = {
    "target_lt": (float, 5.0, 12.0), "systolic_lt": (int, 100, 180), "diastolic_lt": (int, 60, 110),
    "same_day_rule": (str, ("review", "lowest")), "lookback_months": (int, 12, 36), "report_required": (bool,),
    "colonoscopy_years": (int, 5, 10), "positive_fit_requires_followup": (bool,), "discharge_summary_required": (bool,),
    "followup_days": (int, 7, 60), "age_min": (int, 0, 120), "age_max": (int, 0, 120),
}
STATUSES = ("DRAFT", "IN_REVIEW", "APPROVED", "ACTIVE", "SUPERSEDED", "REJECTED")


def _digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()


def _ensure(state: dict) -> dict:
    versions = state.setdefault("versions", {"specs": {}, "active": None})
    if not versions["specs"]:
        versions["specs"][BASELINE_ID] = {
            "version_id": BASELINE_ID, "status": "ACTIVE", "spec": copy.deepcopy(BASELINE_SPEC), "spec_sha256": _digest(BASELINE_SPEC),
            "based_on": None, "changes": [], "rationale": "Baseline specification matching the supplied extract's documented rules.",
            "author": "system (baseline)", "created_at": store.now_iso(), "approved_by": "system (baseline)", "approved_at": store.now_iso(),
            "activated_by": "system (baseline)", "activated_at": store.now_iso(), "effective_date": "2025-10-23", "impact": None, "history": [],
        }
        versions["active"] = BASELINE_ID
    return versions


def _load(path: Path | None):
    state = store.load(path)
    return state, _ensure(state)


def active_spec(path: Path | None = None) -> tuple[str, dict]:
    _, versions = _load(path)
    return versions["active"], versions["specs"][versions["active"]]["spec"]


def _get(versions: dict, version_id: str) -> dict:
    version = versions["specs"].get(version_id)
    if not version:
        raise NotFound(f"Measure version {version_id} not found.")
    return version


def _next_id(versions: dict) -> str:
    year = date.today().year
    taken = [int(v.split(".")[1]) for v in versions["specs"] if v.startswith(f"{year}.")]
    return f"{year}.{max(taken, default=0) + 1}"


def _set(spec: dict, path_: str, raw):
    """Apply one change 'MEASURE.section.parameter' with type and range validation."""
    parts = path_.split(".")
    if len(parts) != 3:
        raise HedisError(f"{path_}: use MEASURE.section.parameter, e.g. BPD-E.numerator.same_day_rule")
    measure, section, name = parts
    if measure not in spec["measures"] or section not in spec["measures"][measure] or name not in spec["measures"][measure][section]:
        raise HedisError(f"{path_} is not a parameter of the specification.")
    rule = PARAMETER_RULES.get(name)
    if not rule:
        raise HedisError(f"{name} is not a governed parameter.")
    kind = rule[0]
    try:
        if kind is bool:
            value = raw if isinstance(raw, bool) else str(raw).strip().lower() in ("true", "1", "yes")
        else:
            value = kind(raw)
    except (TypeError, ValueError) as error:
        raise HedisError(f"{path_} must be {kind.__name__}.") from error
    if kind is str and value not in rule[1]:
        raise HedisError(f"{path_} must be one of {', '.join(rule[1])}.")
    if kind in (int, float) and not rule[1] <= value <= rule[2]:
        raise HedisError(f"{path_} must be between {rule[1]} and {rule[2]}.")
    previous = spec["measures"][measure][section][name]
    spec["measures"][measure][section][name] = value
    return {"path": path_, "from": previous, "to": value}


def create_draft(based_on: str, changes: dict, author: str, rationale: str, path: Path | None = None) -> dict:
    if not changes:
        raise HedisError("A draft needs at least one parameter change.")
    if len(rationale.strip()) < 10:
        raise HedisError("Explain why the measure is changing (at least 10 characters).")
    with store.transaction(path) as state:
        versions = _ensure(state)
        base = _get(versions, based_on)
        spec = copy.deepcopy(base["spec"])
        applied = [_set(spec, key, value) for key, value in changes.items()]
        applied = [change for change in applied if change["from"] != change["to"]]
        if not applied:
            raise Conflict("Every change matches the base version; nothing to draft.")
        version_id = _next_id(versions)
        version = {"version_id": version_id, "status": "DRAFT", "spec": spec, "spec_sha256": _digest(spec), "based_on": based_on,
                   "changes": applied, "rationale": rationale.strip(), "author": author, "created_at": store.now_iso(),
                   "approved_by": None, "approved_at": None, "activated_by": None, "activated_at": None, "effective_date": None,
                   "impact": None, "history": [{"at": store.now_iso(), "by": author, "to": "DRAFT"}]}
        versions["specs"][version_id] = version
        store.audit(state, author, "version.drafted", "measure_version", version_id, new="DRAFT", trigger="draft",
                    details="; ".join(f"{c['path']}: {c['from']} → {c['to']}" for c in applied))
        return version


def _move(version_id: str, to: str, actor: str, allowed_from: tuple, path: Path | None, note: str = "", **updates) -> dict:
    with store.transaction(path) as state:
        versions = _ensure(state)
        version = _get(versions, version_id)
        if version["status"] not in allowed_from:
            raise Conflict(f"{version_id} is {version['status']}; it cannot move to {to}.")
        previous = version["status"]
        version.update(status=to, **updates)
        version["history"].append({"at": store.now_iso(), "by": actor, "to": to, "note": note})
        store.audit(state, actor, f"version.{to.lower()}", "measure_version", version_id, previous=previous, new=to, trigger="lifecycle",
                    details=note)
        return version


def submit(version_id: str, actor: str, path: Path | None = None) -> dict:
    return _move(version_id, "IN_REVIEW", actor, ("DRAFT",), path)


def analyze_impact(version_id: str, actor: str, path: Path | None = None) -> dict:
    """Run the candidate and the active specification over the same normalized evidence and diff the results."""
    state, versions = _load(path)
    candidate = _get(versions, version_id)
    active_id = versions["active"]
    normalized = normalizer.normalize(_workflow_evidence(state))
    members = normalizer.patient_demographics()
    before = calculator.calculate(versions["specs"][active_id]["spec"], normalized, members)
    after = calculator.calculate(candidate["spec"], normalized, members)
    changed = [{"patient_id": pid, "measure_id": mid, "from": before[(pid, mid)]["state"], "to": after[(pid, mid)]["state"],
                "reason": after[(pid, mid)]["reason"]} for (pid, mid) in sorted(after) if before[(pid, mid)]["state"] != after[(pid, mid)]["state"]]
    rates_before, rates_after = calculator.rates(before), calculator.rates(after)
    measures = {mid: {"before": rates_before[mid], "after": rates_after[mid],
                      "rate_delta": None if rates_before[mid]["rate"] is None or rates_after[mid]["rate"] is None
                      else round(rates_after[mid]["rate"] - rates_before[mid]["rate"], 1),
                      "members_changed": sum(c["measure_id"] == mid for c in changed)} for mid in rates_after}
    impact = {"compared_with": active_id, "at": store.now_iso(), "by": actor, "dataset_sha256": normalized["dataset_sha256"],
              "members_changed": len(changed), "measures": measures, "changes": changed[:500]}
    with store.transaction(path) as state:
        version = _get(_ensure(state), version_id)
        version["impact"] = impact
        store.audit(state, actor, "version.impact_analyzed", "measure_version", version_id, trigger="impact",
                    details=f"{len(changed)} member-measure result(s) change vs {active_id}: " +
                            ", ".join(f"{m} {v['rate_delta']:+g} pts" for m, v in measures.items() if v["rate_delta"]))
    return impact


def approve(version_id: str, actor: str, note: str = "", path: Path | None = None) -> dict:
    state, versions = _load(path)
    version = _get(versions, version_id)
    if actor == version["author"] or actor.startswith("agent:"):
        raise SegregationOfDuties("The author of a measure version cannot approve it (four eyes).")
    if not version["impact"]:
        raise Conflict("Run the impact analysis before approving.")
    if version["impact"]["compared_with"] != versions["active"]:
        raise Conflict("The active version changed since the impact analysis; run it again.")
    return _move(version_id, "APPROVED", actor, ("IN_REVIEW",), path, note, approved_by=actor, approved_at=store.now_iso())


def reject(version_id: str, actor: str, note: str, path: Path | None = None) -> dict:
    if not note.strip():
        raise HedisError("Say why the version is rejected.")
    return _move(version_id, "REJECTED", actor, ("DRAFT", "IN_REVIEW", "APPROVED"), path, note)


def activate(version_id: str, actor: str, effective_date: str | None = None, path: Path | None = None) -> dict:
    effective = effective_date or date.today().isoformat()
    with store.transaction(path) as state:
        versions = _ensure(state)
        version = _get(versions, version_id)
        if version["status"] != "APPROVED":
            raise Conflict(f"{version_id} is {version['status']}; only an approved version can be activated.")
        previous = versions["active"]
        if previous:
            old = versions["specs"][previous]
            old["status"] = "SUPERSEDED"
            old["history"].append({"at": store.now_iso(), "by": actor, "to": "SUPERSEDED", "note": f"Replaced by {version_id}"})
        version.update(status="ACTIVE", activated_by=actor, activated_at=store.now_iso(), effective_date=effective)
        version["history"].append({"at": store.now_iso(), "by": actor, "to": "ACTIVE"})
        versions["active"] = version_id
        store.audit(state, actor, "version.activated", "measure_version", version_id, previous=previous, new=version_id, trigger="activate",
                    details=f"Effective {effective}; supersedes {previous}")
        return version


def rollback(to_version_id: str, actor: str, reason: str, path: Path | None = None) -> dict:
    """Re-activate a superseded version. Needs a reason; the rolled-back version keeps its original approval."""
    if len(reason.strip()) < 10:
        raise HedisError("Explain the rollback (at least 10 characters).")
    with store.transaction(path) as state:
        versions = _ensure(state)
        target = _get(versions, to_version_id)
        if target["status"] != "SUPERSEDED":
            raise Conflict("Only a superseded version can be rolled back to.")
        current = versions["specs"][versions["active"]]
        current["status"] = "SUPERSEDED"
        current["history"].append({"at": store.now_iso(), "by": actor, "to": "SUPERSEDED", "note": f"Rolled back to {to_version_id}: {reason}"})
        target["status"] = "ACTIVE"
        target["history"].append({"at": store.now_iso(), "by": actor, "to": "ACTIVE", "note": f"Rollback: {reason}"})
        store.audit(state, actor, "version.rolled_back", "measure_version", to_version_id, previous=current["version_id"], new=to_version_id,
                    trigger="rollback", details=reason.strip())
        versions["active"] = to_version_id
        return target


def diff(a: str, b: str, path: Path | None = None) -> list[dict]:
    _, versions = _load(path)
    left, right = _get(versions, a)["spec"]["measures"], _get(versions, b)["spec"]["measures"]
    out = []
    for measure in sorted(set(left) | set(right)):
        for section in ("population", "numerator"):
            keys = set(left.get(measure, {}).get(section, {})) | set(right.get(measure, {}).get(section, {}))
            for key in sorted(keys):
                old, new = left.get(measure, {}).get(section, {}).get(key), right.get(measure, {}).get(section, {}).get(key)
                if old != new:
                    out.append({"path": f"{measure}.{section}.{key}", a: old, b: new})
    return out


def _workflow_evidence(state: dict) -> list[dict]:
    return list(state.get("workflow", {}).get("evidence", {}).values())


def view(path: Path | None = None) -> dict:
    from app.vbc_poc.catalog import RULE_CHANGE_LOG, RULE_VERSION

    state, versions = _load(path)
    ordered = sorted(versions["specs"].values(), key=lambda v: [int(x) for x in v["version_id"].split(".")], reverse=True)
    return {"active": versions["active"], "versions": ordered, "parameter_rules": {k: [str(x) if isinstance(x, type) else x for x in v]
                                                                                    for k, v in PARAMETER_RULES.items()},
            "engine_rules": {"current": RULE_VERSION, "log": [list(item) for item in RULE_CHANGE_LOG]},
            "events": [e for e in state["audit"] if e["action"].startswith("version.")][-40:]}
