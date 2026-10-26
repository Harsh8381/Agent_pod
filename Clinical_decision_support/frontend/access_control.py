"""User access management: accounts, hashed passwords, role permissions,
fallback (backup) users, work reassignment and an audit trail.

State lives in one JSON file (``data/access_control.json`` by default) so it
survives restarts. The functions here are pure apart from ``load_store`` and
``save_store``; callers decide when to persist.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from frontend.admin_service import (
        ACTIONS,
        DEFAULT_PERMISSIONS,
        MODULES,
        ROLES,
        POC_MODULE,
        VBC_SCREENS,
    )
except ModuleNotFoundError:
    from admin_service import ACTIONS, DEFAULT_PERMISSIONS, MODULES, POC_MODULE, ROLES, VBC_SCREENS

PBKDF2_ITERATIONS = 310_000
MIN_PASSWORD_LENGTH = 8
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
AUDIT_LIMIT = 2000
MAX_FALLBACK_DEPTH = 10
SUPER_ADMIN = "Super Admin"
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Which permission module guards each app page.
PAGE_MODULES = {
    "dashboard": "Clinical Overview",
    "new_consult": "Clinical Review",
    "review": "Clinical Review",
    "medications": "Clinical Review",
    "knowledge_base": "Clinical Review",
    "codes": "Codes",
    "billing": "RCM Workspace",
    "claim_generation": "RCM Workspace",
    "prior_auth": "RCM Workspace",
    "ipd_billing": "IPD Billing",
    "admin_dashboard": "Admin Dashboard",
    "super_admin": "Super Admin",
    "ehr_data": "EHR Demo Data",
    # value-based care workspace (one permission module per specification screen)
    "vbc_stars": VBC_SCREENS["S01"],
    "vbc_measures": VBC_SCREENS["S02"],
    "vbc_gaps": VBC_SCREENS["S03"],
    "vbc_poc": VBC_SCREENS["S04"],
    "vbc_person": VBC_SCREENS["S05"],
    "vbc_evidence": VBC_SCREENS["S06"],
    "vbc_exclusions": VBC_SCREENS["S07"],
    "vbc_supplemental": VBC_SCREENS["S08"],
    "vbc_payer": VBC_SCREENS["S09"],
    "vbc_transitions": VBC_SCREENS["S10"],
    "vbc_outreach": VBC_SCREENS["S11"],
    "vbc_versions": VBC_SCREENS["S12"],
    "vbc_abstraction": VBC_SCREENS["S13"],
    "vbc_surveys": VBC_SCREENS["S14"],
    "vbc_value": VBC_SCREENS["S15"],
    "vbc_governance": VBC_SCREENS["S16"],
    "vbcpoc_dashboard": POC_MODULE,
    "vbcpoc_patient": POC_MODULE,
    "vbcpoc_gaps": POC_MODULE,
    "vbcpoc_governance": POC_MODULE,
}


def poc_enabled() -> bool:
    """VBC_POC_ENABLED feature flag; the POC pages disappear when it is off."""
    try:
        from app.vbc_poc import enabled
    except ModuleNotFoundError:
        return False
    return enabled()


class AuthError(Exception):
    """Raised when sign-in fails; the message is safe to show to the user."""


def _now() -> datetime:
    return datetime.now()


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).strftime(TIME_FORMAT)


# ---------- Persistence ----------
def empty_store() -> dict[str, Any]:
    return {"version": 1, "users": [], "role_permissions": deepcopy(DEFAULT_PERMISSIONS), "audit": []}


def load_store(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return empty_store()
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_store()
    store.setdefault("users", [])
    store.setdefault("audit", [])
    permissions = store.setdefault("role_permissions", {})
    for role in ROLES:
        stored = permissions.setdefault(role, deepcopy(DEFAULT_PERMISSIONS[role]))
        # Modules added after this store was saved start from the role's defaults.
        for module in MODULES:
            if module not in stored:
                stored[module] = deepcopy(DEFAULT_PERMISSIONS[role][module])
    permissions[SUPER_ADMIN] = deepcopy(DEFAULT_PERMISSIONS[SUPER_ADMIN])
    return store


def save_store(path: str | Path, store: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(store, indent=2), encoding="utf-8")
    temp_path.replace(path)


def audit(store: dict[str, Any], actor: str, action: str, detail: str = "") -> None:
    store["audit"].append({"at": _stamp(), "actor": actor or "system", "action": action, "detail": detail})
    del store["audit"][:-AUDIT_LIMIT]


# ---------- Passwords ----------
def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must contain both letters and numbers.")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
    except (AttributeError, ValueError):
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


# ---------- Users ----------
def normalize_username(username: str) -> str:
    clean = str(username or "").strip().lower()
    if not USERNAME_PATTERN.match(clean):
        raise ValueError("Username must be 3-32 characters: letters, numbers, dot, dash or underscore.")
    return clean


def get_user(store: dict[str, Any], username: str | None) -> dict[str, Any] | None:
    key = str(username or "").strip().lower()
    return next((user for user in store["users"] if user["username"] == key), None)


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """User record without secrets, safe to keep in the UI session."""
    if user is None:
        return None
    return {key: value for key, value in user.items() if key not in {"password_hash"}}


def is_available(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("active", True) and not user.get("out_of_office"))


def needs_bootstrap(store: dict[str, Any]) -> bool:
    return not any(user.get("role") == SUPER_ADMIN and user.get("active", True) for user in store["users"])


def _active_super_admins(store: dict[str, Any]) -> list[dict[str, Any]]:
    return [user for user in store["users"] if user.get("role") == SUPER_ADMIN and user.get("active", True)]


def create_user(
    store: dict[str, Any],
    username: str,
    name: str,
    email: str,
    role: str,
    password: str,
    fallback: str | None = None,
    actor: str = "system",
    must_change_password: bool = False,
) -> dict[str, Any]:
    username = normalize_username(username)
    if get_user(store, username):
        raise ValueError(f"Username {username} is already taken.")
    if not str(name or "").strip():
        raise ValueError("Enter the user's full name.")
    if role not in ROLES:
        raise ValueError("Choose one of the configured roles.")
    validate_password(password)
    user = {
        "username": username,
        "name": str(name).strip(),
        "email": str(email or "").strip(),
        "role": role,
        "password_hash": hash_password(password),
        "active": True,
        "out_of_office": False,
        "fallback": None,
        "must_change_password": bool(must_change_password),
        "failed_attempts": 0,
        "locked_until": None,
        "created_at": _stamp(),
        "last_login": None,
    }
    store["users"].append(user)
    if fallback:
        set_fallback(store, username, fallback, actor=actor)
    audit(store, actor, "user.create", f"{username} ({role})")
    return user


def update_user(store: dict[str, Any], username: str, actor: str, **changes: Any) -> dict[str, Any]:
    """Update name, email, role, active or out_of_office. Returns the updated user."""
    user = get_user(store, username)
    if user is None:
        raise ValueError(f"Unknown user {username}.")
    if "role" in changes and changes["role"] not in ROLES:
        raise ValueError("Choose one of the configured roles.")
    losing_admin = user.get("role") == SUPER_ADMIN and user.get("active", True) and (
        changes.get("role", SUPER_ADMIN) != SUPER_ADMIN or changes.get("active", True) is False
    )
    if losing_admin and len(_active_super_admins(store)) <= 1:
        raise ValueError("At least one active Super Admin account is required.")
    if "name" in changes and not str(changes["name"] or "").strip():
        raise ValueError("Name cannot be empty.")
    for field in ("name", "email", "role", "active", "out_of_office"):
        if field in changes and changes[field] != user.get(field):
            audit(store, actor, f"user.{field}", f"{user['username']}: {user.get(field)} → {changes[field]}")
            user[field] = changes[field].strip() if isinstance(changes[field], str) else changes[field]
    return user


def set_password(store: dict[str, Any], username: str, new_password: str, actor: str, temporary: bool = False) -> None:
    """Set a password. ``temporary`` (an administrator reset) forces a change at next sign-in."""
    user = get_user(store, username)
    if user is None:
        raise ValueError(f"Unknown user {username}.")
    validate_password(new_password)
    if not temporary and verify_password(new_password, user.get("password_hash", "")):
        raise ValueError("Choose a password different from the current one.")
    user["password_hash"] = hash_password(new_password)
    user["must_change_password"] = bool(temporary)
    user["failed_attempts"] = 0
    user["locked_until"] = None
    audit(store, actor, "user.password", user["username"])


def unlock_user(store: dict[str, Any], username: str, actor: str) -> None:
    user = get_user(store, username)
    if user:
        user["failed_attempts"] = 0
        user["locked_until"] = None
        audit(store, actor, "user.unlock", user["username"])


def authenticate(store: dict[str, Any], username: str, password: str, now: datetime | None = None) -> dict[str, Any]:
    """Verify credentials; tracks failures and locks the account after repeated misses."""
    now = now or _now()
    user = get_user(store, username)
    generic = "Invalid username or password."
    if user is None:
        audit(store, str(username or "").strip().lower() or "unknown", "auth.failed", "unknown user")
        raise AuthError(generic)
    locked_until = user.get("locked_until")
    if locked_until and now < datetime.strptime(locked_until, TIME_FORMAT):
        raise AuthError(f"Account locked after repeated failed sign-ins. Try again after {locked_until[11:16]} or ask an administrator.")
    if not verify_password(password, user.get("password_hash", "")):
        user["failed_attempts"] = int(user.get("failed_attempts") or 0) + 1
        detail = f"attempt {user['failed_attempts']}"
        if user["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
            user["locked_until"] = _stamp(now + timedelta(minutes=LOCKOUT_MINUTES))
            detail += " · account locked"
        audit(store, user["username"], "auth.failed", detail)
        raise AuthError(generic)
    if not user.get("active", True):
        audit(store, user["username"], "auth.denied", "inactive account")
        raise AuthError("This account is deactivated. Contact an administrator.")
    user["failed_attempts"] = 0
    user["locked_until"] = None
    user["last_login"] = _stamp(now)
    audit(store, user["username"], "auth.sign_in")
    return user


# ---------- Permissions ----------
def role_permissions(store: dict[str, Any], role: str) -> dict[str, dict[str, bool]]:
    return store["role_permissions"].get(role) or DEFAULT_PERMISSIONS.get(role) or {}


def can(store: dict[str, Any], user: dict[str, Any] | None, module: str, action: str = "View") -> bool:
    if not user or not user.get("active", True):
        return False
    if user.get("role") == SUPER_ADMIN:
        return True
    return bool(role_permissions(store, user.get("role", "")).get(module, {}).get(action, False))


def can_view_page(store: dict[str, Any], user: dict[str, Any] | None, page: str) -> bool:
    if page == "vbc_playbook":  # reference material for anyone working in the value-based care workspace
        return any(can(store, user, module, "View") for module in VBC_SCREENS.values())
    if page.startswith("vbcpoc_") and not poc_enabled():
        return False
    module = PAGE_MODULES.get(page)
    return module is None or can(store, user, module, "View")


def save_role_permissions(store: dict[str, Any], role: str, rows: list[dict[str, Any]], actor: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role}")
    if role == SUPER_ADMIN:
        raise ValueError("Super Admin always has full access and cannot be edited.")
    updated = {
        str(row.get("Module")): {action: bool(row.get(action, False)) for action in ACTIONS}
        for row in rows if str(row.get("Module")) in MODULES
    }
    before = store["role_permissions"].get(role, {})
    changes = [
        f"{module}.{action}={'on' if values[action] else 'off'}"
        for module, values in updated.items() for action in ACTIONS
        if bool(before.get(module, {}).get(action)) != values[action]
    ]
    store["role_permissions"][role] = {**deepcopy(before), **updated}
    if changes:
        audit(store, actor, "role.permissions", f"{role}: " + ", ".join(changes))


# ---------- Fallback users and work reassignment ----------
def fallback_chain(store: dict[str, Any], username: str) -> list[str]:
    """Follow fallback links from a user; stops at a repeat or MAX_FALLBACK_DEPTH."""
    chain, seen = [], {str(username).lower()}
    current = get_user(store, username)
    while current and current.get("fallback") and len(chain) < MAX_FALLBACK_DEPTH:
        nxt = current["fallback"]
        if nxt in seen:
            break
        chain.append(nxt)
        seen.add(nxt)
        current = get_user(store, nxt)
    return chain


def set_fallback(store: dict[str, Any], username: str, fallback: str | None, actor: str) -> None:
    user = get_user(store, username)
    if user is None:
        raise ValueError(f"Unknown user {username}.")
    fallback = str(fallback or "").strip().lower() or None
    if fallback:
        if fallback == user["username"]:
            raise ValueError("A user cannot be their own fallback.")
        if get_user(store, fallback) is None:
            raise ValueError(f"Unknown fallback user {fallback}.")
        if user["username"] in [fallback, *fallback_chain(store, fallback)]:
            raise ValueError(f"{fallback} already falls back to {user['username']}; that would create a loop.")
    if user.get("fallback") != fallback:
        audit(store, actor, "user.fallback", f"{user['username']}: {user.get('fallback')} → {fallback}")
        user["fallback"] = fallback


def resolve_assignee(store: dict[str, Any], username: str | None) -> str | None:
    """The user who should actually hold work for ``username``: themself if available,
    otherwise the first available user along their fallback chain (None if nobody is)."""
    user = get_user(store, username)
    if user is None:
        return None
    if is_available(user):
        return user["username"]
    for candidate in fallback_chain(store, user["username"]):
        if is_available(get_user(store, candidate)):
            return candidate
    return None


def _log_assignment(record: dict[str, Any], from_user: str | None, to_user: str | None, actor: str, reason: str) -> None:
    record.setdefault("assignment_history", []).append(
        {"at": _stamp(), "from": from_user, "to": to_user, "by": actor, "reason": reason}
    )


def assign_record(store: dict[str, Any], record: dict[str, Any], username: str, actor: str, reason: str = "Assigned") -> str | None:
    """Assign an encounter, routing to the fallback when the chosen user is unavailable."""
    target = resolve_assignee(store, username)
    previous = record.get("assigned_to")
    if target != username and target:
        record["rerouted_from"] = str(username).lower()
        reason = f"{reason} · {username} unavailable, routed to fallback"
    else:
        record.pop("rerouted_from", None)
    record["assigned_to"] = target
    _log_assignment(record, previous, target, actor, reason)
    audit(store, actor, "work.assign", f'{record.get("id")}: {previous} → {target}')
    return target


def reroute_assignments(store: dict[str, Any], records: list[dict[str, Any]], username: str, actor: str, reason: str) -> list[dict[str, Any]]:
    """Move work held by an unavailable user to their fallback. Returns changed records."""
    username = str(username).lower()
    target = resolve_assignee(store, username)
    if target == username:
        return []
    changed = []
    for record in records:
        if record.get("assigned_to") == username and record.get("status") != "Billed":
            record.setdefault("rerouted_from", username)
            record["assigned_to"] = target
            _log_assignment(record, username, target, actor, reason)
            changed.append(record)
    if changed:
        audit(store, actor, "work.reroute", f"{len(changed)} encounter(s) {username} → {target or 'unassigned queue'} ({reason})")
    return changed


def restore_assignments(store: dict[str, Any], records: list[dict[str, Any]], username: str, actor: str) -> list[dict[str, Any]]:
    """Return rerouted work to its original user once they are available again."""
    username = str(username).lower()
    if not is_available(get_user(store, username)):
        return []
    changed = []
    for record in records:
        if record.get("rerouted_from") == username:
            previous = record.get("assigned_to")
            record["assigned_to"] = username
            record.pop("rerouted_from", None)
            _log_assignment(record, previous, username, actor, f"{username} available again — work returned")
            changed.append(record)
    if changed:
        audit(store, actor, "work.restore", f"{len(changed)} encounter(s) returned to {username}")
    return changed
