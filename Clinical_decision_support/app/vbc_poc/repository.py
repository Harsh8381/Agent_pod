"""JSON-file persistence for the POC workflow state, shared safely by the API and the UI.

Every write happens inside `transaction()`: a cross-process lock, a fresh read, the change, an
atomic replace. The audit log is append-only and hash-chained, so any edit to history is detectable.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

STATE_ENV = "VBC_POC_STATE"
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "vbc_poc_state.json"
SCHEMA = 1
_LOCAL = threading.RLock()


def state_path() -> Path:
    return Path(os.environ.get(STATE_ENV) or DEFAULT_PATH)


def empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA, "revision": 0, "created_at": now_iso(), "counters": {},
        "added_events": [], "evidence_decisions": {}, "results": {}, "risk": {}, "gaps": {}, "gap_index": {},
        "care_plans": {}, "outreach": {}, "runs": [], "baseline": None, "audit": [],
    }


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(f"Not serializable: {type(value)}")


def load(path: Path | None = None) -> dict[str, Any]:
    path = path or state_path()
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    state = empty_state()
    state.update(data)
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["revision"] = int(state.get("revision", 0)) + 1
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=1, default=_default), encoding="utf-8")
    temp.replace(path)


@contextmanager
def _file_lock(path: Path, timeout: float = 10.0):
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30:  # stale lock from a crashed writer
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("VBC POC state is busy; try again.") from None
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(handle)
        lock.unlink(missing_ok=True)


@contextmanager
def transaction(path: Path | None = None):
    path = path or state_path()
    with _LOCAL, _file_lock(path):
        state = load(path)
        yield state
        _save(path, state)


def next_id(state: dict[str, Any], prefix: str) -> str:
    counters = state.setdefault("counters", {})
    counters[prefix] = int(counters.get(prefix, 0)) + 1
    return f"{prefix}-{counters[prefix]:04d}"


def _hash(event: dict[str, Any]) -> str:
    body = {key: value for key, value in event.items() if key != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=_default).encode("utf-8")).hexdigest()


def audit(state: dict[str, Any], actor: str, action: str, object_type: str, object_id: str, *, previous: str | None = None,
          new: str | None = None, trigger: str = "", evidence_ids: list | None = None, details: str = "") -> dict[str, Any]:
    log = state.setdefault("audit", [])
    event = {
        "seq": len(log) + 1, "at": now_iso(), "actor": actor or "system", "action": action, "object_type": object_type,
        "object_id": object_id, "previous_state": previous, "new_state": new, "trigger": trigger, "evidence_ids": evidence_ids or [],
        "details": details, "prev": log[-1]["hash"] if log else "GENESIS",
    }
    event["hash"] = _hash(event)
    log.append(event)
    return event


def verify_chain(state: dict[str, Any]) -> tuple[bool, int | None]:
    previous = "GENESIS"
    for index, event in enumerate(state.get("audit", [])):
        if event.get("prev") != previous or event.get("hash") != _hash(event):
            return False, index + 1
        previous = event["hash"]
    return True, None
