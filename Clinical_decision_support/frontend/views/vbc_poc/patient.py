"""Quality loop POC — Patient 360."""

from __future__ import annotations

from html import escape

import streamlit as st

from app.vbc_poc import catalog
from app.vbc_poc import service as vbc
from app.vbc_poc.dataset import DEMO_PATIENT, PATIENTS
from frontend.ui.components import badge, card_title, render_html_table, render_tiles
from frontend.ui.shell import render_page
from frontend.views.vbc_poc.common import (
    WORKFLOW_TONES,
    actor,
    can_poc,
    flag_disabled_notice,
    fmt_date,
    guard,
    header,
    open_gap,
    risk_badge,
    run,
    state_badge,
    workflow_badge,
)

TYPE_TONES = {"encounter": "blue", "condition": "violet", "observation": "green", "procedure": "peach", "order": "amber", "document": "gray"}
VERDICT_TONES = {"accepted": "green", "rejected": "red", "review": "amber", "invalid": "violet", "duplicate": "gray"}


def show_patient_360():
    render_page("vbcpoc_patient")
    if flag_disabled_notice():
        return
    actions = header("Patient 360", "One patient's 12-month history, measure results, care gaps, care plan and audit trail")
    ids = [patient.patient_id for patient in PATIENTS]
    current = st.session_state.get("vbcpoc_patient") or DEMO_PATIENT
    labels = {patient.patient_id: f"{patient.name} · {patient.patient_id} · {patient.cohort}" for patient in PATIENTS}
    pick_col, _ = st.columns([2, 3])
    patient_id = pick_col.selectbox("Patient", ids, index=ids.index(current) if current in ids else 0, format_func=labels.get,
                                    key=f"poc_patient_{current}")
    st.session_state.vbcpoc_patient = patient_id
    with actions:
        if st.button("Re-evaluate patient", key="poc_eval_one", icon=":material/refresh:", type="primary",
                     disabled=not can_poc("Edit")) and guard("Edit", "re-evaluate a patient"):
            run(vbc.evaluate_patient, patient_id, actor(), success="All four measures re-evaluated")
            st.rerun()
    view = vbc.patient_360(patient_id)
    _header_card(view)
    _quality_panel(view)
    gap_col, plan_col = st.columns([1.35, 1], gap="medium")
    with gap_col:
        _gap_panel(view)
    with plan_col:
        _care_plan(view)
    _timeline(view)
    _audit_panel(view)


def _header_card(view: dict) -> None:
    patient, risk = view["patient"], view["risk"] or {"score": 0, "category": "Low", "factors": []}
    initials = "".join(part[0] for part in patient["name"].split()[:2])
    enrollment = "; ".join(f"{fmt_date(start)} – {fmt_date(end)}" for start, end in patient["enrollment"])
    st.markdown(
        '<div class="vbc-person">'
        f'<div class="nc-initials lg">{escape(initials)}</div>'
        f'<div class="who"><b>{escape(patient["name"])} · {patient["age"]} {escape(patient["sex"])}</b>'
        f'<small>{patient["patient_id"]} · DOB {fmt_date(patient["dob"])} · attributed to {escape(patient["attributed_provider"])} · '
        f'{escape(patient["program"])} · enrolled {escape(enrollment)} · language {escape(patient["language"])}</small></div>'
        f'<div class="vbc-grow"></div><div class="nc-badge-row">{risk_badge(risk)}{badge(patient["cohort"], "violet")}'
        f'{badge("Synthetic patient", "amber")}</div></div>',
        unsafe_allow_html=True,
    )
    story_col, risk_col = st.columns([1.3, 1], gap="medium")
    with story_col:
        st.markdown(f'<div class="vbc-callout story" style="margin-top:.6rem"><div><b>Scenario under test</b>{escape(patient["story"])}</div></div>',
                    unsafe_allow_html=True)
    with risk_col:
        factors = "".join(f'<div class="vbc-check"><span class="no">+{item["points"]}</span><span>{escape(item["label"])}</span>'
                          f'<small>{escape(item["detail"])}</small></div>' for item in risk["factors"])
        st.markdown(f'<div class="poc-risk"><div class="nc-label">Why {risk["category"].lower()} risk ({risk["score"]} pts)</div>'
                    f'{factors or "<div class=nc-meta>No risk signals.</div>"}<div class="nc-meta">{escape(risk.get("disclaimer", ""))}</div></div>',
                    unsafe_allow_html=True)


def _quality_panel(view: dict) -> None:
    with st.container(border=True):
        card_title("Quality panel", f"{view['period']} · rule {view['rule_version']} — measure state, workflow progress and evidence status are separate badges")
        cols = st.columns(2, gap="medium")
        for index, item in enumerate(view["measures"]):
            result = item["result"]
            with cols[index % 2]:
                st.markdown(
                    f'<div class="vbc-tile tone-{catalog.MEASURE_STATES[result["state"]].tone}"><div class="top"><b>{escape(item["short"])} '
                    f'<span class="nc-meta">{item["measure_id"]}</span></b></div>'
                    f'<div class="nc-badge-row" style="margin:.35rem 0">{state_badge(result["state"])}{workflow_badge(item["workflow"])}'
                    f'{badge("Evidence · " + item["evidence_badge"], "gray")}</div><p>{escape(result["reason"])}</p></div>',
                    unsafe_allow_html=True,
                )
                with st.expander(f"Evidence and logic trace · {item['measure_id']}"):
                    if result["evidence"]:
                        render_html_table(["Evidence", "Date", "Value", "Source", "Verdict"], [
                            [f"{escape(row['display'])}<div class='nc-meta'>{row['event_id']} · {escape(row['source_record_id'])}</div>",
                             fmt_date(row["date"]) if row["date"] else badge("undated", "violet"), _value(row),
                             escape(row["source_label"]), badge(row["verdict"], VERDICT_TONES[row["verdict"]])
                             + f"<div class='nc-meta'>{escape(row['reason'])}</div>"]
                            for row in result["evidence"]
                        ])
                    render_html_table(["#", "Stage", "Decision", "Detail"], [
                        [str(step["stage"]), escape(step["name"]), f"<b>{escape(str(step['decision']))}</b>", escape(step["detail"])]
                        for step in result["trace"]
                    ])
                    st.caption(f"Result {result['result_id']} v{result['version']} · trigger {result['trigger']} · evaluated {result['last_evaluated_at']}")
                    for gap_id in item["gap_ids"]:
                        st.button(f"Open gap {gap_id} →", key=f"poc_open_{gap_id}_{item['measure_id']}", type="tertiary", on_click=open_gap, args=(gap_id,))


def _value(row: dict) -> str:
    if row["concept"] == "bp":
        diastolic = "—" if row["value2"] is None else f"{row['value2']:g}"
        return f"{row['value']:g}/{diastolic} {row['unit']}" if row["value"] is not None else "—"
    if row["value"] is not None:
        return f"{row['value']:g} {row['unit']}".strip()
    return escape(row["result_flag"] or "—")


def _gap_panel(view: dict) -> None:
    with st.container(border=True):
        card_title("Care gaps", "Priority, owner, due date, status and next action — one gap per measure and line of work")
        if not view["gaps"]:
            st.success("No care gaps for this patient.")
        for gap in view["gaps"]:
            info_col, button_col = st.columns([4, 1], vertical_alignment="center")
            info_col.markdown(
                f'<div class="nc-cell"><b>{gap["gap_id"]} · {gap["measure_id"]} · {escape(catalog.GAP_TYPES[gap["gap_type"]])}</b> '
                f'{badge(gap["priority"], {"High": "red", "Medium": "amber", "Low": "green"}[gap["priority"]])} '
                f'{badge(gap["status"].title(), WORKFLOW_TONES.get(gap["status"], "gray"))}'
                + (f' {badge(gap["disposition"].replace("_", " ").title(), "gray")}' if gap["disposition"] else "")
                + f'<div class="nc-meta">{escape(gap["recommended_action"])} · owner {escape(gap["owner"] or "unassigned")} · due {fmt_date(gap["due_date"])}'
                + (f' · closed: {escape(gap["closure"]["reason"])}' if gap.get("closure") else "") + "</div></div>",
                unsafe_allow_html=True,
            )
            button_col.button("Open", key=f"poc_gap_btn_{gap['gap_id']}", type="tertiary", on_click=open_gap, args=(gap["gap_id"],))


def _care_plan(view: dict) -> None:
    plan = view["care_plan"]
    with st.container(border=True):
        tone = {"draft": "amber", "active": "green", "revision_pending": "red", "ready_to_complete": "blue", "completed": "gray"}
        card_title("Care plan", "Built from open gaps with templates; a clinician approves before it becomes active",
                   badge(plan["status"].replace("_", " ").title(), tone.get(plan["status"], "gray")) if plan else "")
        if not plan:
            st.caption("No care plan needed — no open gaps.")
            return
        for goal in plan.get("goals", []):
            st.markdown(f'<div class="nc-cell"><b>Goal:</b> {escape(goal["goal"])} <span class="nc-meta">{goal["measure_id"]}</span></div>',
                        unsafe_allow_html=True)
        if plan.get("interventions"):
            st.markdown('<div class="nc-label" style="margin-top:.5rem">Interventions</div>' + "".join(
                f'<div class="nc-meta">• {escape(item["text"])}</div>' for item in plan["interventions"]), unsafe_allow_html=True)
        if plan.get("tasks"):
            render_html_table(["Task", "Owner", "Due", "Status"], [
                [escape(task["action"]), escape(task["owner"] or task["owner_role"]), fmt_date(task["due_date"]), badge(task["status"].title(), "blue")]
                for task in plan["tasks"]
            ])
        meta = f"Version {plan['version']}"
        if plan.get("approved_by"):
            meta += f" · approved by {plan['approved_by']} · review {fmt_date(plan['review_date'])}"
        st.caption(meta)
        pid = view["patient"]["patient_id"]
        with st.container(horizontal=True, gap="small"):
            if plan["status"] in ("draft", "revision_pending") and st.button("Approve care plan", key=f"poc_plan_ok_{pid}", type="primary",
                                                                             disabled=not can_poc("Approve")) and guard("Approve", "approve a care plan"):
                run(vbc.approve_care_plan, pid, actor(), success="Care plan approved")
                st.rerun()
            if plan["status"] == "ready_to_complete" and st.button("Mark plan completed", key=f"poc_plan_done_{pid}", type="secondary",
                                                                   disabled=not can_poc("Approve")) and guard("Approve", "complete a care plan"):
                run(vbc.complete_care_plan, pid, actor(), success="Care plan completed")
                st.rerun()


def _timeline(view: dict) -> None:
    with st.container(border=True):
        card_title("12-month timeline", "Encounters, admissions, labs, procedures, claims, documents and workflow events — newest first, with provenance")
        kinds = sorted({row["event_type"] for row in view["timeline"]})
        chosen = st.multiselect("Show", kinds, default=kinds, key=f"poc_tl_{view['patient']['patient_id']}", label_visibility="collapsed")
        rows = [row for row in view["timeline"] if row["event_type"] in chosen]
        render_html_table(["Date", "Type", "Event", "Value", "Source", "Used by measures"], [
            [(fmt_date(row["event_date"]) if row["event_date"] else badge("undated", "violet")) + f"<div class='nc-meta'>recorded {fmt_date(row['recorded_at'])}</div>",
             badge(row["encounter_type"].replace("_", " ") if row["encounter_type"] else row["event_type"], TYPE_TONES.get(row["event_type"], "gray")),
             f"<b>{escape(row['display'])}</b><div class='nc-meta'>{escape(row['code_system'])} {escape(row['code'])}"
             + (f" · {escape(row['note'])}" if row["note"] else "") + "</div>",
             _timeline_value(row), f"{escape(row['source_label'])}<div class='nc-meta'>{escape(row['source_record_id'])}</div>",
             " ".join(badge(item, VERDICT_TONES.get(item.split(': ')[-1], "gray")) for item in row["used_by"]) or "—"]
            for row in rows
        ])


def _timeline_value(row: dict) -> str:
    if row["value"] is None:
        return escape(row["result_flag"] or (row["status"] if row["status"] not in ("final", "completed") else "—"))
    if row["code"] == "85354-9":
        return f"{row['value']:g}/{'—' if row['value2'] is None else format(row['value2'], 'g')} mmHg"
    return f"{row['value']:g} {escape(row['unit'])}"


def _audit_panel(view: dict) -> None:
    with st.expander("Audit panel — rule version, evaluation timestamps and provenance"):
        render_tiles((("Rule version", view["rule_version"]), ("Measurement period", view["period"]), ("Evaluated as of", fmt_date(view["as_of"])),
                      ("Audit events for patient", str(len(view["audit"])))))
        render_html_table(["#", "When", "Actor", "Action", "Object", "Previous → new", "Details"], [
            [str(event["seq"]), escape(event["at"].replace("T", " ")), escape(event["actor"]), escape(event["action"]),
             escape(event["object_id"]), escape(f"{event['previous_state'] or '—'} → {event['new_state'] or '—'}"), escape(event["details"][:120])]
            for event in reversed(view["audit"][-25:])
        ])
