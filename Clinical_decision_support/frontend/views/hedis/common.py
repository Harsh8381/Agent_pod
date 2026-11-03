"""Shared UI helpers for the HEDIS engine pages: identity, permission guard, error display, headers."""

from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

from app.hedis import store
from app.hedis.errors import HedisError
from app.security import rbac
from app.vbc_poc.catalog import PERIOD, RULE_VERSION
from frontend.ui.components import badge, page_header, render_banner
from frontend.ui.state import can, current_user

EYEBROW = "Value-based care · HEDIS engine"
DISCLAIMER = ("Synthetic data and HEDIS-inspired prototype rules. Registry, submissions and reconciliation run end to end, but rates are not "
              "reportable as HEDIS until licensed NCQA specifications are loaded and an NCQA-licensed auditor signs off.")
STATUS_TONES = {
    "PASS": "green", "ATTESTED": "green", "WARN": "amber", "ATTEST": "violet", "FAIL": "red",
    "DRAFT": "amber", "SIGNED": "blue", "SUBMITTED": "violet", "ACKNOWLEDGED": "green", "PARTIAL": "amber", "REJECTED": "red", "DISCARDED": "gray",
    "READY": "blue", "READY_RESUBMIT": "blue", "IN_DRAFT": "amber", "PENDING_ACK": "violet", "ACCEPTED": "green", "NEEDS_CORRECTION": "red",
    "PROPOSED": "amber", "ESCALATED": "red", "AUTO_EXECUTED": "green", "APPROVED": "green", "REVERTED": "gray", "FAILED": "red",
    "ACKNOWLEDGED_ESC": "blue", "RESOLVED": "green", "NOT_AN_ISSUE": "gray", "OPEN": "amber", "UPHELD": "green", "DENIED": "red", "WITHDRAWN": "gray",
    "EXECUTING": "blue",
}
TIER_TONES = {"L1": "green", "L2": "amber", "L3": "red"}


def principal() -> rbac.Principal:
    user = current_user()
    return rbac.Principal(user.get("username", ""), user.get("name", ""), user.get("role", ""))


def actor() -> str:
    return principal().actor


def phi_allowed() -> bool:
    return rbac.can_see_phi(principal())


def allowed(module: str, action: str = "View") -> bool:
    return can(module, action)


def guard(module: str, action: str, what: str) -> bool:
    """Every write re-checks permission; a denial is written to the HEDIS audit chain."""
    if can(module, action):
        return True
    with store.transaction() as state:
        store.audit(state, actor(), "rbac.denied", "permission", module, trigger=what, details=f"Requires {action}")
    st.error(f"Your role cannot {what}. The attempt was logged.")
    return False


def run(callable_, *args, success: str | None = None, **kwargs):
    """Call the engine; workflow-rule refusals become messages, never stack traces."""
    try:
        result = callable_(*args, **kwargs)
    except HedisError as error:
        st.error(str(error))
        return None
    except TimeoutError as error:
        st.warning(str(error))
        return None
    if success:
        st.toast(success)
    return result


def header(title: str, subtitle: str, action_weight: float = 3.6):
    actions = page_header(EYEBROW, title, subtitle, action_weight=action_weight)
    render_banner(f"{PERIOD.period_id} · rule {RULE_VERSION}", DISCLAIMER, tone="warn", icon="&#9888;")
    return actions


def status_badge(status: str | None, label: str | None = None) -> str:
    if not status:
        return badge("—", "gray")
    return badge(label or status.replace("_", " ").title(), STATUS_TONES.get(status, "gray"))


def tier_badge(tier: str) -> str:
    return badge(tier, TIER_TONES.get(tier, "gray"))


def fmt_date(value) -> str:
    if not value:
        return "—"
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(value)


def code(text) -> str:
    return f"<code>{escape(str(text))}</code>"


def bars(rows: list[tuple]) -> None:
    """rows: (label, sub, value 0-100 or None, display, tone)."""
    parts = []
    for label, sub, value, display, tone in rows:
        width = max(0.0, min(100.0, value or 0.0))
        parts.append(f'<div class="lbl">{escape(label)}<small>{escape(sub)}</small></div>'
                     f'<div class="vbc-track"><div class="vbc-fill tone-{tone}" style="width:{width}%"></div></div>'
                     f'<div class="val">{escape(display)}</div>')
    st.markdown(f'<div class="vbc-bars">{"".join(parts)}</div>', unsafe_allow_html=True)


def flag_disabled_notice() -> bool:
    from frontend.access_control import poc_enabled

    if poc_enabled():
        return False
    st.info("The HEDIS engine runs on the value-based care measure engine, which is switched off (VBC_POC_ENABLED=0).")
    return True
