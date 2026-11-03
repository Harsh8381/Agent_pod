"""HEDIS registry: the authoritative member x measure ledger for the measurement period.

One row per (period, member, measure), built from the latest versioned measure result. Each row says
whether the member is in the denominator, excluded or compliant, which accepted records support the
numerator and which NCQA source class they fall into (administrative, standard or non-standard
supplemental). Snapshots freeze the registry with a content hash; locking the period needs a second
person (four eyes) and a registry that still matches its last snapshot.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from app.hedis import store
from app.hedis.config import (
    ADMINISTRATIVE,
    ENGINE_VERSION,
    NON_STANDARD_SUPPLEMENTAL,
    NOT_REPORTABLE,
    PAYERS,
    PRACTITIONERS,
    SOURCE_CLASS,
    SOURCE_PRIORITY,
    is_valid_npi,
    member_id_for,
    payer_for,
)
from app.hedis.errors import Conflict, HedisError, SegregationOfDuties
from app.vbc_poc import service as vbc
from app.vbc_poc.catalog import EXCLUDED, MEASURE_STATES, MEASURES, PERIOD, RULE_VERSION
from app.vbc_poc.dataset import PATIENTS_BY_ID

HASHED_FIELDS = ("key", "member_id", "payer", "measure_id", "state", "in_denominator", "numerator_met", "excluded",
                 "numerator_source", "compliant_date", "evidence_ids", "rule_version")


def _row(patient, measure_id: str, result: dict, events: dict) -> dict:
    payer = PAYERS[payer_for(patient.patient_id)]
    practitioner = PRACTITIONERS.get(patient.attributed_provider, {})
    evidence = []
    for item in result["evidence"]:
        if item["verdict"] != "accepted":
            continue
        event = events.get(item["event_id"])
        evidence.append({
            "event_id": item["event_id"], "source": item["source"], "source_class": SOURCE_CLASS.get(item["source"], NOT_REPORTABLE),
            "code_system": event.code_system if event else "", "code": event.code if event else "", "display": item["display"],
            "date": item["date"], "value": item["value"], "value2": item["value2"], "unit": item["unit"],
            "verification": item["verification"], "source_record_id": item["source_record_id"], "concept": item["concept"],
        })
    numerator_met = bool(result["numerator_met"])
    classes = {item["source_class"] for item in evidence}
    source = next((cls for cls in SOURCE_PRIORITY if cls in classes), None) if numerator_met else None
    dates = [item["date"] for item in evidence if item["date"]]
    return {
        "key": f"{PERIOD.period_id}|{member_id_for(patient.patient_id)}|{measure_id}",
        "period": PERIOD.period_id, "patient_id": patient.patient_id, "member_id": member_id_for(patient.patient_id),
        "name": patient.name, "dob": patient.dob.isoformat() if patient.dob else None, "sex": patient.sex,
        "payer": payer.key, "payer_name": payer.name, "product": payer.product,
        "provider": patient.attributed_provider, "provider_npi": practitioner.get("npi"),
        "measure_id": measure_id, "measure": MEASURES[measure_id].short,
        "state": result["state"], "state_label": MEASURE_STATES[result["state"]].label, "reason": result["reason"],
        "in_denominator": bool(result["in_denominator"]), "numerator_met": numerator_met,
        "excluded": result["state"] == EXCLUDED, "exclusion_reason": result["exclusion"].get("reason") if result["exclusion"].get("applied") else None,
        "exclusion_concept": result["exclusion"].get("concept") if result["exclusion"].get("applied") else None,
        "numerator_source": source, "compliant_date": max(dates) if numerator_met and dates else None,
        "evidence": evidence, "evidence_ids": sorted(item["event_id"] for item in evidence),
        "result_id": result["result_id"], "result_version": result["version"], "rule_version": result["rule_version"],
        "fingerprint": result["fingerprint"],
    }


def build_rows(poc_path: Path | None = None) -> list[dict]:
    """Current registry, recomputed from the latest stored measure results (never edited by hand)."""
    poc_state = vbc.ensure_initialized(poc_path)
    rows = []
    for (patient_id, measure_id), result in sorted(vbc.latest_results(poc_state).items()):
        patient = PATIENTS_BY_ID[patient_id]
        events = {event.event_id: event for event in vbc.events_for(poc_state, patient_id)}
        rows.append(_row(patient, measure_id, result, events))
    return rows


def content_hash(rows: list[dict]) -> str:
    body = [{field: row.get(field) for field in HASHED_FIELDS} for row in sorted(rows, key=lambda r: r["key"])]
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def totals(rows: list[dict]) -> dict:
    measures = {}
    for row in rows:
        entry = measures.setdefault(row["measure_id"], {"measure": row["measure"], "denominator": 0, "numerator": 0, "excluded": 0,
                                                        "by_source": {}, "by_payer": {}})
        payer = entry["by_payer"].setdefault(row["payer"], {"denominator": 0, "numerator": 0})
        if row["excluded"]:
            entry["excluded"] += 1
        if row["in_denominator"]:
            entry["denominator"] += 1
            payer["denominator"] += 1
        if row["numerator_met"]:
            entry["numerator"] += 1
            payer["numerator"] += 1
            entry["by_source"][row["numerator_source"]] = entry["by_source"].get(row["numerator_source"], 0) + 1
    for entry in measures.values():
        entry["rate"] = round(entry["numerator"] / entry["denominator"] * 100, 1) if entry["denominator"] else None
        for payer in entry["by_payer"].values():
            payer["rate"] = round(payer["numerator"] / payer["denominator"] * 100, 1) if payer["denominator"] else None
    denominator = sum(entry["denominator"] for entry in measures.values())
    numerator = sum(entry["numerator"] for entry in measures.values())
    return {"measures": measures, "denominator": denominator, "numerator": numerator,
            "rate": round(numerator / denominator * 100, 1) if denominator else None,
            "members": len({row["member_id"] for row in rows})}


def issues(rows: list[dict]) -> list[dict]:
    """Integrity checks run on every build. 'block' issues stop the period from being locked."""
    found = []
    seen = set()
    for row in rows:
        key = row["key"]
        if key in seen:
            found.append({"code": "REG-DUP", "severity": "block", "key": key, "detail": "Duplicate registry key"})
        seen.add(key)
        if row["numerator_met"] and not row["evidence"]:
            found.append({"code": "REG-NO-EVIDENCE", "severity": "block", "key": key, "detail": "Numerator met without an accepted record"})
        for item in row["evidence"]:
            if item["source_class"] == NON_STANDARD_SUPPLEMENTAL and item["verification"] != "verified":
                found.append({"code": "REG-PSV", "severity": "block", "key": key,
                              "detail": f"{item['event_id']} is non-standard supplemental without primary-source verification"})
        if row["in_denominator"] and not is_valid_npi(row["provider_npi"]):
            found.append({"code": "REG-NPI", "severity": "block", "key": key, "detail": f"Attributed provider {row['provider']} has no valid NPI"})
        if row["state"] == "DATA_QUALITY_ISSUE":
            found.append({"code": "REG-DQ", "severity": "warn", "key": key, "detail": row["reason"]})
        if row["state"] in ("EVIDENCE_REVIEW", "COMPLETED_PENDING_VERIFICATION"):
            found.append({"code": "REG-REVIEW", "severity": "warn", "key": key, "detail": f"Awaiting human verification: {row['reason']}"})
        if row["rule_version"] != RULE_VERSION:
            found.append({"code": "REG-RULE", "severity": "block", "key": key,
                          "detail": f"Result produced by rule {row['rule_version']}, current rule is {RULE_VERSION}"})
    return found


def _diff(previous: list[dict], current: list[dict]) -> dict:
    before = {row["key"]: row for row in previous}
    after = {row["key"]: row for row in current}
    changes = {"gained_compliance": [], "lost_compliance": [], "denominator_in": [], "denominator_out": [], "state_changed": [],
               "added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after))}
    for key in sorted(set(before) & set(after)):
        old, new = before[key], after[key]
        if not old["numerator_met"] and new["numerator_met"]:
            changes["gained_compliance"].append(key)
        if old["numerator_met"] and not new["numerator_met"]:
            changes["lost_compliance"].append(key)
        if not old["in_denominator"] and new["in_denominator"]:
            changes["denominator_in"].append(key)
        if old["in_denominator"] and not new["in_denominator"]:
            changes["denominator_out"].append(key)
        if old["state"] != new["state"]:
            changes["state_changed"].append({"key": key, "from": old["state"], "to": new["state"]})
    return changes


def latest_snapshot(state: dict) -> dict | None:
    snapshots = state["registry"]["snapshots"]
    return snapshots[-1] if snapshots else None


def publish_snapshot(actor: str, note: str = "", path: Path | None = None, poc_path: Path | None = None) -> dict:
    rows = build_rows(poc_path)
    with store.transaction(path) as state:
        if state["registry"]["locked"]:
            raise Conflict(f"Period {PERIOD.period_id} is locked; reopen it before publishing a new snapshot.")
        previous = latest_snapshot(state)
        digest = content_hash(rows)
        if previous and previous["content_hash"] == digest:
            raise Conflict(f"Registry unchanged since {previous['snapshot_id']} (hash {digest[:12]}).")
        snapshot = {
            "snapshot_id": store.next_id(state, "REG"), "at": store.now_iso(), "published_by": actor, "note": note,
            "period": PERIOD.period_id, "rule_version": RULE_VERSION, "engine_version": ENGINE_VERSION,
            "content_hash": digest, "row_count": len(rows), "totals": totals(rows), "issues": issues(rows),
            "diff": _diff(previous["rows"] if previous else [], rows), "previous": previous["snapshot_id"] if previous else None,
            "rows": rows,
        }
        state["registry"]["snapshots"].append(snapshot)
        del state["registry"]["snapshots"][:-24]
        store.audit(state, actor, "registry.snapshot_published", "registry_snapshot", snapshot["snapshot_id"],
                    previous=snapshot["previous"], new=digest[:16], trigger="publish",
                    details=f"{len(rows)} rows · {snapshot['totals']['numerator']}/{snapshot['totals']['denominator']} compliant · "
                            f"+{len(snapshot['diff']['gained_compliance'])} / -{len(snapshot['diff']['lost_compliance'])}")
        return _snapshot_meta(snapshot)


def lock_period(actor: str, path: Path | None = None, poc_path: Path | None = None) -> dict:
    """Freeze the period for reporting. Four eyes: the locker must not be the snapshot's publisher."""
    rows = build_rows(poc_path)
    with store.transaction(path) as state:
        snapshot = latest_snapshot(state)
        if state["registry"]["locked"]:
            raise Conflict(f"Period {PERIOD.period_id} is already locked.")
        if not snapshot:
            raise HedisError("Publish a registry snapshot before locking the period.")
        if snapshot["published_by"] == actor:
            raise SegregationOfDuties("The person who published the snapshot cannot also lock the period.")
        if content_hash(rows) != snapshot["content_hash"]:
            raise Conflict("The registry has changed since the last snapshot; publish a new snapshot first.")
        blocking = [issue for issue in issues(rows) if issue["severity"] == "block"]
        if blocking:
            raise Conflict(f"{len(blocking)} blocking registry issue(s) must be resolved before locking.")
        state["registry"]["locked"] = {"at": store.now_iso(), "by": actor, "snapshot_id": snapshot["snapshot_id"],
                                       "content_hash": snapshot["content_hash"]}
        store.audit(state, actor, "registry.period_locked", "period", PERIOD.period_id, previous="open", new="locked",
                    trigger="lock", details=f"Locked on {snapshot['snapshot_id']} ({snapshot['content_hash'][:12]})")
        return state["registry"]["locked"]


def reopen_period(actor: str, reason: str, path: Path | None = None) -> dict:
    if not reason.strip():
        raise HedisError("A reason is required to reopen a locked period.")
    with store.transaction(path) as state:
        locked = state["registry"]["locked"]
        if not locked:
            raise Conflict("The period is not locked.")
        state["registry"]["locked"] = None
        store.audit(state, actor, "registry.period_reopened", "period", PERIOD.period_id, previous="locked", new="open",
                    trigger="reopen", details=reason.strip())
        return {"reopened_by": actor, "reason": reason.strip(), "was": locked}


def _snapshot_meta(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key != "rows"}


PHI_FIELDS = ("name", "dob")


def mask(row: dict) -> dict:
    """Minimum necessary: roles without PHI rights see member IDs and measure data only."""
    masked = dict(row)
    masked["name"] = f"Member {row['member_id'][-4:]}"
    masked["dob"] = None
    return masked


def view(*, phi: bool = True, path: Path | None = None, poc_path: Path | None = None) -> dict:
    rows = build_rows(poc_path)
    state = store.load(path)
    snapshot = latest_snapshot(state)
    digest = content_hash(rows)
    return {
        "period": {"id": PERIOD.period_id, "start": PERIOD.start.isoformat(), "end": PERIOD.end.isoformat()},
        "rule_version": RULE_VERSION, "engine_version": ENGINE_VERSION, "as_of": vbc.today().isoformat(),
        "rows": rows if phi else [mask(row) for row in rows], "totals": totals(rows), "issues": issues(rows),
        "content_hash": digest, "locked": state["registry"]["locked"],
        "latest_snapshot": _snapshot_meta(snapshot) if snapshot else None,
        "drift": bool(snapshot and snapshot["content_hash"] != digest),
        "snapshots": [_snapshot_meta(item) for item in reversed(state["registry"]["snapshots"])],
    }


def snapshot_rows(snapshot_id: str, path: Path | None = None) -> list[dict]:
    state = store.load(path)
    for snapshot in state["registry"]["snapshots"]:
        if snapshot["snapshot_id"] == snapshot_id:
            return snapshot["rows"]
    from app.hedis.errors import NotFound

    raise NotFound(f"Registry snapshot {snapshot_id} not found.")


def measure_window(measure_id: str, concept: str) -> tuple[date, date]:
    """Service-date window in which a record can satisfy the numerator (lookback start to period end)."""
    measure = MEASURES[measure_id]
    return measure.lookbacks.get(concept, PERIOD.start), PERIOD.end


__all__ = ["ADMINISTRATIVE", "build_rows", "content_hash", "issues", "lock_period", "publish_snapshot", "reopen_period", "totals", "view"]
