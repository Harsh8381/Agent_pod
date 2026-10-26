"""Shared UI helpers for the VBC POC pages (quality loop)."""

from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

from app.vbc_poc import catalog
from app.vbc_poc import repository as repo
from app.vbc_poc import service as vbc
from frontend.admin_service import POC_MODULE
from frontend.ui.components import badge, page_header, render_banner
from frontend.ui.state import can, current_user, go_to

STATE_TONES = {code: info.tone for code, info in catalog.MEASURE_STATES.items()}
WORKFLOW_TONES = {"DETECTED": "red", "REVIEWED": "amber", "ASSIGNED": "blue", "OUTREACH": "violet", "SCHEDULED": "blue",
                  "COMPLETED": "peach", "VERIFIED": "green", "CLOSED": "green"}
RISK_TONES = {"High": "red", "Medium": "amber", "Low": "green"}
ACTION_LABELS = {"REVIEWED": "Confirm finding", "ASSIGNED": "Assign", "OUTREACH": "Start outreach", "SCHEDULED": "Mark scheduled"}


def actor() -> str:
    user = current_user()
    return f"{user['name']} ({user['role']})" if user.get("role") else user["name"]


def can_poc(action: str = "View") -> bool:
    return can(POC_MODULE, action)


def guard(action: str, what: str) -> bool:
    """Every write re-checks permission; denials are written to the POC audit log."""
    if can_poc(action):
        return True
    with repo.transaction() as state:
        repo.audit(state, actor(), "rbac.denied", "permission", POC_MODULE, trigger=what, details=f"Requires {action}")
    st.error(f"Your role cannot {what}. The attempt was logged.")
    return False


def run(callable_, *args, success: str | None = None, **kwargs):
    """Call the service; workflow-rule violations are shown as messages, not stack traces."""
    try:
        result = callable_(*args, **kwargs)
    except vbc.VbcError as error:
        st.error(str(error))
        return None
    except TimeoutError as error:
        st.warning(str(error))
        return None
    if success:
        st.toast(success)
    return result


def header(title: str, subtitle: str, action_weight: float = 3.4):
    actions = page_header("Value-based care · Quality loop (POC)", title, subtitle, action_weight=action_weight)
    render_banner("Demonstration system — synthetic data", catalog.DISCLAIMER + f" Rule version {catalog.RULE_VERSION} · "
                  f"{catalog.PERIOD.period_id} ({catalog.PERIOD.start:%d %b %Y} – {catalog.PERIOD.end:%d %b %Y}) · as of {vbc.today():%d %b %Y}.",
                  tone="warn", icon="&#9888;")
    return actions


def state_badge(state: str) -> str:
    return badge(catalog.MEASURE_STATES[state].label, STATE_TONES.get(state, "gray"))


def workflow_badge(status: str | None) -> str:
    return badge(f"Workflow · {status.title()}", WORKFLOW_TONES.get(status, "gray")) if status else badge("No workflow", "gray")


def risk_badge(risk: dict | None) -> str:
    risk = risk or {"category": "Low", "score": 0}
    return badge(f"{risk['category']} risk · {risk['score']}", RISK_TONES[risk["category"]])


def stepper(status: str) -> None:
    current = catalog.WORKFLOW.index(status)
    parts = []
    for index, name in enumerate(catalog.WORKFLOW):
        css = "done" if index < current or status == "CLOSED" else "current" if index == current else ""
        mark = "&#10003;" if css == "done" else str(index + 1)
        parts.append(f'<div class="nc-step {css}"><i>{mark}</i>{escape(name.title())}</div>')
    st.markdown(f'<div class="nc-stepper poc-stepper">{"".join(parts)}</div>', unsafe_allow_html=True)


def open_patient(patient_id: str) -> None:
    st.session_state.vbcpoc_patient = patient_id
    go_to("vbcpoc_patient")


def open_gap(gap_id: str) -> None:
    st.session_state.vbcpoc_gap = gap_id
    go_to("vbcpoc_gaps")


def fmt_date(value) -> str:
    if not value:
        return "—"
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(value)


def flag_disabled_notice() -> bool:
    from frontend.access_control import poc_enabled

    if poc_enabled():
        return False
    st.info("The Value-Based Care POC is switched off (VBC_POC_ENABLED=0).")
    return True
