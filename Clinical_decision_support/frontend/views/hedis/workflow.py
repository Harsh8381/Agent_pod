"""Care-gap management — writable review-to-closure workflow, specialized by gap type."""

from __future__ import annotations

from datetime import date
from html import escape

import pandas as pd
import streamlit as st

from app.hedis import workflow
from frontend.ui.components import badge, card_title, render_html_table, render_kpi_card, render_tiles
from frontend.ui.shell import render_page
from frontend.views.hedis.common import actor, allowed, code, flag_disabled_notice, fmt_date, guard, header, phi_allowed, principal, run

MODULE = workflow.MODULE
TYPE_TONES = {"Clinical Opportunity": "blue", "Outcome Management": "amber", "Documentation Reconciliation": "violet"}
FIELD_LABELS = {"note": "Note", "assignee": "Assign to", "channel": "Channel", "schedule_date": "Scheduled date", "service_date": "Service date",
                "plan": "Clinical plan", "requested_from": "Requested from", "document_ref": "Document reference"}


def _permitted(module: str, action: str) -> bool:
    return allowed(module, action)


def show_hedis_workflow():
    render_page("hedis_workflow")
    if flag_disabled_notice():
        return
    header("Review-to-closure workflow", "Every care gap worked through the workflow for its type — closure only on a compliant recalculation "
           "or an approved disposition")
    summary = workflow.summary()
    cols = st.columns(4)
    for col, args in zip(cols, (
        ("Open gaps", str(summary["open"]), "lav", f"{summary['total']} in the extract"),
        ("Past SLA", str(summary["overdue"]), "rose" if summary["overdue"] else "mint", f"as of {fmt_date(summary['as_of'])}"),
        ("Closed", str(summary["closed"]), "mint", "Compliant recalculation or disposition"),
        ("Workflow evidence", f"{summary['verified_captures']} / {summary['captures']}", "sky", "verified / captured"),
    ), strict=True):
        with col:
            render_kpi_card(*args)
    left, right = st.columns([1.3, 1.1], gap="medium")
    with left:
        first = _worklist(summary)
    with right:
        gap_id = st.session_state.get("hwf_gap") or first  # open the top-priority gap until one is picked
        if gap_id:
            _detail(gap_id)
        else:
            st.info("Pick a gap from the worklist.")


def _worklist(summary: dict) -> str | None:
    """Render the worklist; returns the first gap shown so the detail panel is never empty."""
    with st.container(border=True):
        card_title("Worklist", "Priority, then days past SLA")
        c1, c2 = st.columns(2)
        gap_type = c1.selectbox("Workflow", ["All", *workflow.WORKFLOWS], key="hwf_type")
        view = c2.selectbox("Show", ["Open", "Past SLA", "Closed"], key="hwf_view")
        rows = workflow.list_gaps(gap_type=None if gap_type == "All" else gap_type, overdue_only=view == "Past SLA", include_closed=view == "Closed")
        if view == "Closed":
            rows = [r for r in rows if r["status"] == "CLOSED"]
        if not rows:
            st.caption("Nothing to show.")
            return None
        frame = pd.DataFrame([{"Gap": r["gap_id"], "Member": r["name"] if phi_allowed() else r["patient_id"], "Measure": r["measure_id"],
                               "State": r["status"].replace("_", " ").title(), "Priority": r["priority"], "Late (d)": r["overdue_days"]}
                              for r in rows[:300]])
        event = st.dataframe(frame, hide_index=True, width="stretch", height=430, on_select="rerun", selection_mode="single-row", key="hwf_table")
        selected = event.selection.rows if event and hasattr(event, "selection") else []
        if selected:
            st.session_state.hwf_gap = frame.iloc[selected[0]]["Gap"]
        st.caption(f"{len(rows)} gap(s){' (first 300 shown)' if len(rows) > 300 else ''}. Select a row to work it.")
        return rows[0]["gap_id"]


def _detail(gap_id: str) -> None:
    data = run(workflow.detail, gap_id)
    if not data:
        return
    gap, spec, calc = data["gap"], data["workflow"], data["calculated"]
    with st.container(border=True):
        card_title(f"{gap['gap_id']} · {gap['measure_id']}", f"{spec['label']} — {spec['purpose']}",
                   trailing=badge(gap["gap_type"], TYPE_TONES.get(gap["gap_type"], "gray")))
        states = spec["states"]
        current = states.index(gap["status"]) if gap["status"] in states else 0
        st.markdown('<div class="nc-stepper">' + "".join(
            f'<div class="nc-step {"done" if i < current or gap["status"] == "CLOSED" else "current" if i == current else ""}">'
            f'<i>{"&#10003;" if i < current or gap["status"] == "CLOSED" else i + 1}</i>{escape(name.replace("_", " ").title())}</div>'
            for i, name in enumerate(states)) + "</div>", unsafe_allow_html=True)
        member = data["member"]
        render_tiles([("Member", member.get("name") if phi_allowed() else gap["patient_id"]), ("Priority", gap["priority"]),
                      ("Owner", gap["assignee"] or gap["owner_role"]), ("SLA", f"{gap['overdue_days']} d late" if gap["overdue_days"] else "On time"),
                      ("Outreach attempts", gap["attempts"]), ("Calculated now", calc["label"])])
        st.caption(f"Recalculated with the active measure version and verified workflow evidence: {calc['reason']}.")
        if gap["status"] == "CLOSED":
            st.success(f"Closed{' — ' + gap['disposition'].replace('_', ' ').lower() if gap['disposition'] else ''}.")
        for move in data["allowed"]:
            _transition_form(gap, move, data["capture_spec"])
        if data["dispositions"]:
            with st.expander("Close with a disposition (approval)"):
                with st.form(f"hwf_disp_{gap['gap_id']}"):
                    disposition = st.selectbox("Disposition", list(data["dispositions"]), format_func=lambda k: data["dispositions"][k])
                    note = st.text_area("Documentation")
                    if st.form_submit_button("Close gap", disabled=not allowed(MODULE, "Approve")) and guard(MODULE, "Approve", "close with a disposition"):
                        if run(workflow.close_with_disposition, gap["gap_id"], disposition, actor(), _permitted, note, success="Gap closed"):
                            st.rerun()
        _history(gap, data["captures"])


def _transition_form(gap: dict, move: dict, capture: dict) -> None:
    role_note = f" · {', '.join(move['roles'])}" if move["roles"] else ""
    approve_note = " · needs Approve" if move["approve"] else ""
    with st.form(f"hwf_{gap['gap_id']}_{move['to']}_{gap['status']}"):
        st.markdown(f"**{escape(move['label'])}** → `{move['to']}`<span class='nc-meta'>{escape(role_note + approve_note)}</span>", unsafe_allow_html=True)
        fields = {}
        for name in move["fields"]:
            label = FIELD_LABELS.get(name, name)
            if name in ("schedule_date", "service_date"):
                fields[name] = st.date_input(label, value=date.fromisoformat(workflow._today().isoformat()), key=f"hwf_{gap['gap_id']}_{name}").isoformat()
            elif name == "channel":
                fields[name] = st.selectbox(label, ["Phone", "SMS", "Letter", "Portal"], key=f"hwf_{gap['gap_id']}_{name}")
            else:
                fields[name] = st.text_input(label, key=f"hwf_{gap['gap_id']}_{move['to']}_{name}")
        if move["captures"]:
            values = capture["values"]
            st.caption(f"Records {capture['concept']} ({capture['code_system']} {capture['code']}) as non-standard supplemental evidence; "
                       "it counts after a second person verifies it.")
            if "value" in values:
                fields["value"] = st.number_input("Systolic" if "value2" in values else f"Result ({capture['unit']})", min_value=0.0, step=0.1,
                                                  key=f"hwf_{gap['gap_id']}_value")
            if "value2" in values:
                fields["value2"] = st.number_input("Diastolic", min_value=0.0, step=1.0, key=f"hwf_{gap['gap_id']}_value2")
            if "result_flag" in values:
                fields["result_flag"] = st.selectbox("FIT result", ["negative", "positive"], key=f"hwf_{gap['gap_id']}_flag")
            if gap["measure_id"] == "KED":
                fields["component"] = st.selectbox("Component", ["egfr", "uacr"], key=f"hwf_{gap['gap_id']}_component")
            fields["document_ref"] = st.text_input("Source document reference", key=f"hwf_{gap['gap_id']}_docref")
        disabled = not allowed(MODULE, "Approve" if move["approve"] else "Edit") or bool(move["roles"] and principal().role not in move["roles"]
                                                                                          and principal().role != "Super Admin")
        if st.form_submit_button(move["label"], type="primary" if move["closes"] else "secondary", disabled=disabled):
            if guard(MODULE, "Approve" if move["approve"] else "Edit", move["label"].lower()):
                if run(workflow.transition, gap["gap_id"], move["to"], actor(), principal().role, _permitted, fields,
                       success=f"Moved to {move['to'].replace('_', ' ').title()}"):
                    st.rerun()


def _history(gap: dict, captures: list) -> None:
    if captures:
        render_html_table(["Capture", "Concept", "Service date", "Value", "Captured by", "Verified by"],
                          [[code(c["capture_id"]), escape(c["concept"]), escape(c["service_date"]),
                            escape(str(c.get("value", c.get("result_flag", "—")))), escape(c["captured_by"]),
                            escape(c["verified_by"] or "Pending")] for c in captures])
    render_html_table(["When", "Who", "From", "To", "Note"],
                      [[escape(h["at"].replace("T", " ")[:16]), escape(h["by"]), escape((h.get("from") or "—").replace("_", " ").title()),
                        escape(h["to"].replace("_", " ").title()), escape(h.get("note", ""))] for h in reversed(gap["history"])])
