"""Named permissions for the HEDIS engine, resolved through the shared role matrix."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from frontend import access_control as ac
from frontend.settings import ACCESS_STORE_PATH

ACCESS_STORE_ENV = "NUUCARE_ACCESS_STORE"

PERMISSIONS = {
    "registry:read": ("HEDIS Registry", "View"),
    "registry:publish": ("HEDIS Registry", "Edit"),
    "registry:lock": ("HEDIS Registry", "Approve"),
    "supplemental:read": ("Supplemental Submission", "View"),
    "supplemental:build": ("Supplemental Submission", "Edit"),
    "supplemental:sign": ("Supplemental Submission", "Approve"),
    "reconciliation:read": ("Payer Reconciliation", "View"),
    "reconciliation:run": ("Payer Reconciliation", "Edit"),
    "reconciliation:dispute": ("Payer Reconciliation", "Approve"),
    "agents:read": ("AI Agent Console", "View"),
    "agents:run": ("AI Agent Console", "Edit"),
    "agents:decide": ("AI Agent Console", "Approve"),
    "agents:admin": ("AI Agent Console", "Admin"),
    "ncqa:read": ("NCQA Readiness", "View"),
    "ncqa:assess": ("NCQA Readiness", "Edit"),
    "ncqa:attest": ("NCQA Readiness", "Approve"),
    "evidence:read": ("Evidence & Quality", "View"),
    "evidence:run": ("Evidence & Quality", "Edit"),
    "qa:signoff": ("Evidence & Quality", "Approve"),
    "workflow:read": ("Care Gap Workflow", "View"),
    "workflow:edit": ("Care Gap Workflow", "Edit"),
    "versions:read": ("Measure Versioning", "View"),
    "versions:draft": ("Measure Versioning", "Edit"),
    "versions:approve": ("Measure Versioning", "Approve"),
}
# Minimum necessary: these roles work with identified members; everyone else sees member IDs only.
PHI_ROLES = ("Super Admin", "HEDIS Analyst", "HEDIS Lead", "Auditor", "Care Manager", "Physician / APP", "Clinical Reviewer", "Abstractor")


@dataclass(frozen=True)
class Principal:
    username: str
    name: str
    role: str

    @property
    def actor(self) -> str:
        """How the person appears in audit trails: name and role, so reviewers can see the duty performed."""
        return f"{self.name} ({self.role})"


def store_path() -> Path:
    """The shared user and role store; NUUCARE_ACCESS_STORE overrides it (tests, deployments)."""
    return Path(os.environ.get(ACCESS_STORE_ENV) or ACCESS_STORE_PATH)


def load_store() -> dict:
    return ac.load_store(store_path())


def principal_for(username: str, store: dict | None = None) -> Principal | None:
    """The current account behind a token: deactivated, locked or unknown accounts get nothing."""
    store = store or load_store()
    user = ac.get_user(store, username)
    if not user or not user.get("active", True):
        return None
    locked_until = user.get("locked_until")
    if locked_until and datetime.strptime(locked_until, ac.TIME_FORMAT) > datetime.now():
        return None
    return Principal(user["username"], user.get("name") or user["username"], user.get("role", ""))


def allowed(principal: Principal, permission: str, store: dict | None = None) -> bool:
    if permission not in PERMISSIONS:
        raise KeyError(f"Unknown permission {permission}")
    module, action = PERMISSIONS[permission]
    store = store or load_store()
    return ac.can(store, ac.get_user(store, principal.username), module, action)


def module_allowed(principal: Principal, module: str, action: str, store: dict | None = None) -> bool:
    store = store or load_store()
    return ac.can(store, ac.get_user(store, principal.username), module, action)


def can_see_phi(principal: Principal) -> bool:
    return principal.role in PHI_ROLES
