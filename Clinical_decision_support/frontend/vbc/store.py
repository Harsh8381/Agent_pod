"""Persisted workflow state for the value-based care workspace, with a hash-chained audit log.

Everything a person does — orders, preferences, exclusion decisions, evidence validation,
supplemental batches, outreach attempts, approvals — lives here, never in the clinical record.
The audit log is append-only: each event carries the hash of the previous one, so any edit
or deletion breaks the chain and `verify_chain` reports where.
"""

from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCHEMA = 1
_LOCK = threading.RLock()
AUDIT_FAMILIES = {
    "WRK": "Work action",
    "EVD": "Evidence decision",
    "EXC": "Exclusion review",
    "SUP": "Supplemental data",
    "OUT": "Outreach",
    "SRV": "Survey integrity",
    "RBAC": "Permission check",
    "AUD": "Audit readiness",
    "VER": "Version change",
    "TRC": "Care transitions",
    "ABS": "Abstraction",
    "PAY": "Payer reconciliation",
}


def empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "seeded": False,
        "revision": 0,
        "counters": {},
        "actions": [],
        "assignments": {},
        "deferred": {},
        "exclusion_decisions": {},
        "evidence_decisions": {},
        "posted_facts": [],
        "cpt2_proposals": [],
        "mapping_tasks": [],
        "batches": [],
        "disputes": [],
        "outreach": [],
        "campaigns": {},
        "transitions": {},
        "manual_adt": [],
        "fmc_tasks": {},
        "abstractions": {},
        "change_requests": {},
        "versions": [{"version": "v2026.1", "published": "2026-01-01", "by": "seed", "note": "MP 2026 base specification"}],
        "snapshots": {},
        "survey_routes": {},
        "person_prefs": {},
        "consent_withdrawn": {},
        "outreach_queue": [],
        "record_searches": {},
        "quarantine": {},
        "audit": [],
    }


def _default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(f"Not serializable: {type(value)!r}")


def load_state(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    state = empty_state()
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            state.update(loaded)
    for key, value in empty_state().items():
        state.setdefault(key, value)
    return state


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    path = Path(path)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        state["revision"] = int(state.get("revision", 0)) + 1
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=1, default=_default), encoding="utf-8")
        temp.replace(path)


def next_id(state: dict[str, Any], prefix: str, start: int = 1) -> str:
    counters = state.setdefault("counters", {})
    counters[prefix] = int(counters.get(prefix, start - 1)) + 1
    return f"{prefix}-{counters[prefix]:04d}"


def _event_hash(event: dict[str, Any]) -> str:
    body = {key: value for key, value in event.items() if key != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=_default).encode()).hexdigest()


def audit(
    state: dict[str, Any],
    family: str,
    actor: str,
    action: str,
    detail: str = "",
    outcome: str = "Recorded",
    refs: dict | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Append one event; each event's hash covers its content and the previous hash."""
    log = state.setdefault("audit", [])
    event = {
        "seq": len(log) + 1,
        "at": (at or datetime.now()).isoformat(timespec="seconds"),
        "family": family,
        "actor": actor or "system",
        "action": action,
        "detail": detail,
        "outcome": outcome,
        "refs": refs or {},
        "prev": log[-1]["hash"] if log else "GENESIS",
    }
    event["hash"] = _event_hash(event)
    log.append(event)
    return event


def verify_chain(state: dict[str, Any]) -> tuple[bool, int | None]:
    previous = "GENESIS"
    for index, event in enumerate(state.get("audit", [])):
        if event.get("prev") != previous or event.get("hash") != _event_hash(event):
            return False, index + 1
        previous = event["hash"]
    return True, None


def latest_action(state: dict[str, Any], person_id: str, measure: str, kinds: tuple) -> dict | None:
    matches = [
        action for action in state.get("actions", [])
        if action.get("person_id") == person_id and action.get("measure") == measure and action.get("kind") in kinds
        and not action.get("superseded")
    ]
    return matches[-1] if matches else None


def add_action(state: dict[str, Any], actor: str, person_id: str, measure: str, kind: str, detail: str, **extra) -> dict:
    action = {
        "id": next_id(state, "ACT"),
        "at": datetime.now().isoformat(timespec="seconds"),
        "by": actor,
        "person_id": person_id,
        "measure": measure,
        "kind": kind,
        "detail": detail,
        **extra,
    }
    state.setdefault("actions", []).append(action)
    return action


def snapshot_copy(state: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(state)
