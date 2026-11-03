"""Persistence for the HEDIS engine: same cross-process transaction and hash-chained audit as the POC store."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.hedis.config import state_path
from app.vbc_poc import repository as repo

SCHEMA = 1


def empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA, "revision": 0, "created_at": repo.now_iso(), "counters": {},
        "registry": {"snapshots": [], "locked": None},
        "supplemental": {"batches": {}, "ledger": {}, "overrides": {}},
        "reconciliation": {"runs": {}, "disputes": {}, "tasks": {}},
        "agents": {"enabled": {"L1": True, "L2": True, "L3": True}, "proposals": {}, "runs": [], "priorities": [], "dq_tasks": {}},
        "ncqa": {"attestations": {}, "assessments": []},
        "versions": {"specs": {}, "active": None},
        "quality": {"runs": [], "signoffs": {}},
        "workflow": {"gaps": {}, "seeded_from": None, "evidence": {}},
        "access_log": [], "audit": [],
    }


def load(path: Path | None = None) -> dict[str, Any]:
    """Read the state file; sections added in later versions start from their defaults."""
    path = path or state_path()
    state = empty_state()
    if not path.is_file():
        return state
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    for key, value in data.items():
        if isinstance(state.get(key), dict) and isinstance(value, dict):
            state[key] = {**state[key], **value}
        else:
            state[key] = value
    return state


@contextmanager
def transaction(path: Path | None = None):
    """Lock, fresh read, change, atomic replace — the shape every write in this package goes through."""
    path = path or state_path()
    with repo._LOCAL, repo._file_lock(path):
        state = load(path)
        yield state
        repo._save(path, state)


next_id = repo.next_id
now_iso = repo.now_iso
verify_chain = repo.verify_chain


def audit(state: dict, actor: str, action: str, object_type: str, object_id: str, **kwargs) -> dict:
    return repo.audit(state, actor, action, object_type, object_id, **kwargs)


def log_access(state: dict, actor: str, resource: str, detail: str = "") -> None:
    """Who read what (PHI-bearing reads); bounded, separate from the write audit chain."""
    log = state.setdefault("access_log", [])
    log.append({"at": repo.now_iso(), "actor": actor, "resource": resource, "detail": detail})
    del log[:-2000]
