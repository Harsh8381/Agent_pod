"""Quality loop POC — care-gap worklist and gap detail (detection to closure)."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

import streamlit as st

from app.vbc_poc import catalog
from app.vbc_poc import service as vbc
from app.vbc_poc.careplan import CHANNELS
from frontend.ui.components import badge, card_title, render_html_table, render_tiles
from frontend.ui.shell import render_page
from frontend.views.vbc_poc.common import (
    ACTION_LABELS,
    WORKFLOW_TONES,
    actor,
    can_poc,
    flag_disabled_notice,
    fmt_date,
    guard,
    header,
    open_patient,
    risk_badge,
    run,
    stepper,
)
from frontend.views.vbc_poc.dashboard import OWNERS

PRIORITY_TONES = {"High": "red", "Medium": "amber", "Low": "green"}
VERDICT_TONES = {"accepted": "green", "rejected": "red", "review": "amber", "invalid": "violet", "duplicate": "gray"}


def show_worklist():
    render_page("vbcpoc_gaps")
    if flag_disabled_notice():
        return
    header("Care-gap worklist", "Detected → reviewed → assigned → outreach → scheduled → completed → verified → closed")
    f1, f2, f3, f4, f5 = st.columns([1.3, 1.3, 1, 1.3, 1.3], vertical_alignment="bottom")
    status = f1.selectbox("Status", ("All open",) + catalog.WORKFLOW, key="poc_f_status")
    measure = f2.selectbox("Measure", ("All",) + tuple(catalog.MEASURES), key="poc_f_measure",
                           format_func=lambda code: code if code == "All" else f"{code} · {catalog.MEASURES[code].short}")
    priority = f3.selectbox("Priority", ("All", "High", "Medium", "Low"), key="poc_f_priority")
    owner = f4.selectbox("Owner", ("Anyone", "Unassigned") + OWNERS, key="poc_f_owner")
    overdue = f5.toggle("Overdue only", key="poc_f_overdue")
    rows = vbc.list_gaps(include_closed=status in ("All open", "CLOSED"), status=None if status == "All open" else status,
                         measure_id=None if measure == "All" else measure, priority=None if priority == "All" else priority,
                         overdue_only=overdue)
    if status == "All open":
        rows = [row for row in rows if row["status"] != "CLOSED"]
    if owner == "Unassigned":
        rows = [row for row in rows if not row["owner"]]
    elif owner != "Anyone":
        rows = [row for row in rows if row["owner"] == owner]

    list_col, detail_col = st.columns([1, 2.1], gap="medium")
    with list_col, st.container(border=True):
        card_title(f"{len(rows)} gap(s)", "Sorted by priority score, then overdue days")
        if not rows:
            st.info("No gaps match these filters.")
            return
        ids = [row["gap_id"] for row in rows]
        by_id = {row["gap_id"]: row for row in rows}
        current = st.session_state.get("vbcpoc_gap")
        index = ids.index(current) if current in ids else 0
        chosen = st.radio(
            "Gap", ids, index=index, key=f"poc_gap_pick_{current}", label_visibility="collapsed",
            format_func=lambda gid: f"{by_id[gid]['patient_name']} · {by_id[gid]['measure_short']}",
            captions=[f"{by_id[gid]['priority']} · {by_id[gid]['status'].title()} · {by_id[gid]['gap_type_label']} · due {fmt_date(by_id[gid]['due_date'])}"
                      + (f" · {by_id[gid]['overdue_days']} d overdue" if by_id[gid]["overdue_days"] else "") for gid in ids],
        )
        st.session_state.vbcpoc_gap = chosen
    with detail_col:
        _detail(chosen)


def _detail(gap_id: str) -> None:
    detail = vbc.gap_detail(gap_id)
    gap, result, patient = detail["gap"], detail["result"], detail["patient"]
    with st.container(border=True):
        title_col, link_col = st.columns([4, 1], vertical_alignment="center")
        with title_col:
            card_title(f"{gap_id} · {patient['name']} · {catalog.MEASURES[gap['measure_id']].short}",
                       f"{catalog.GAP_TYPES[gap['gap_type']]} · line of work: {gap['line'].title()} · period {gap['period']}")
        link_col.button("Patient 360 →", key=f"poc_d_360_{gap_id}", type="tertiary", on_click=open_patient, args=(gap["patient_id"],))
        st.markdown(
            '<div class="nc-badge-row">'
            f'{badge("Measure · " + catalog.MEASURE_STATES[result["state"]].label, catalog.MEASURE_STATES[result["state"]].tone)}'
            f'{badge("Workflow · " + gap["status"].title(), WORKFLOW_TONES.get(gap["status"], "gray"))}'
            f'{badge("Evidence · " + detail["evidence_badge"], "gray")}'
            f'{badge("Priority " + gap["priority"] + f" ({gap["priority_score"]})", PRIORITY_TONES[gap["priority"]])}{risk_badge(detail["risk"])}'
            + (badge("Disposition · " + gap["disposition"].replace("_", " ").title(), "gray") if gap["disposition"] else "")
            + (badge(f"Reopened ×{gap['reopened']}", "red") if gap["reopened"] else "") + "</div>",
            unsafe_allow_html=True,
        )
        stepper(gap["status"])
        if detail["linked_gap"]:
            linked = detail["linked_gap"]
            st.caption(f"Linked to {linked['gap_id']} ({catalog.GAP_TYPES[linked['gap_type']]}, {linked['status'].title()}).")

    why_col, action_col = st.columns([1.25, 1], gap="medium")
    with why_col, st.container(border=True):
        card_title("Why flagged", "Eligibility, denominator and the missing or uncontrolled element, in plain language")
        st.markdown(f'<div class="nc-cell"><b>{escape(result["reason"])}</b></div>', unsafe_allow_html=True)
        render_tiles((("Population", result["population"]["reason"] or "—"), ("Denominator", result["denominator"]["reason"] or "—"),
                      ("Outcome", f"{result['outcome'].get('status', '—')} {result['outcome'].get('value') or ''}".strip()),
                      ("Data quality", result["data_quality"]["status"])))
        if result["data_quality"]["flags"]:
            st.caption("Data quality: " + "; ".join(result["data_quality"]["flags"]))
    with action_col, st.container(border=True):
        card_title("Recommended next action", "Order, appointment, documentation request or clinical follow-up")
        st.markdown(f'<div class="vbc-big" style="font-size:1.15rem">{escape(gap["recommended_action"])}</div>', unsafe_allow_html=True)
        render_tiles((("Owner role", gap["owner_role"]), ("Owner", gap["owner"] or "Unassigned"), ("Due", fmt_date(gap["due_date"])),
                      ("Due-date rule", f"{catalog.DUE_DAYS[gap['gap_type']]} days ({catalog.HIGH_RISK_DUE_DAYS} if high risk)")))

    _evidence(detail)
    work_col, outreach_col = st.columns([1.15, 1], gap="medium")
    with work_col:
        _workflow(detail)
    with outreach_col:
        _outreach(detail)
    _closure(detail)
    with st.container(border=True):
        card_title("History", "Every transition with actor, trigger and evidence — nothing is overwritten")
        render_html_table(["When", "Actor", "From → to", "Trigger", "Evidence", "Note"], [
            [escape(item["at"].replace("T", " ")), escape(item["actor"]), escape(f"{(item['from'] or '—').title()} → {item['to'].title()}"),
             escape(item["trigger"]), escape(", ".join(item["evidence_ids"]) or "—"), escape(item["note"])]
            for item in reversed(gap["history"])
        ])


def _evidence(detail: dict) -> None:
    gap, result = detail["gap"], detail["result"]
    used_col, not_col = st.columns(2, gap="medium")
    with used_col, st.container(border=True):
        card_title("Evidence used", "Accepted, structured, dated events with source and record ID")
        if not detail["evidence_used"]:
            st.caption("No accepted evidence yet.")
        for item in detail["evidence_used"]:
            _evidence_row(item, gap, allow_invalidate=True)
    with not_col, st.container(border=True):
        card_title("Evidence not accepted", "Conflicting, stale, unverified, invalid or unsupported records — and why")
        if not detail["evidence_not_accepted"]:
            st.caption("Nothing rejected or pending.")
        for item in detail["evidence_not_accepted"]:
            _evidence_row(item, gap)
    if result["state"] == "EVIDENCE_REVIEW" and result["outcome"].get("status") == "conflicting":
        st.info("Two same-day results disagree. Reject the one that is wrong; the other is then used automatically.")


def _evidence_row(item: dict, gap: dict, allow_invalidate: bool = False) -> None:
    value = f"{item['value']:g}" if item["value"] is not None else ""
    if item["value2"] is not None:
        value += f"/{item['value2']:g}"
    value = (value + f" {item['unit']}").strip() if value else (item["result_flag"] or "")
    st.markdown(
        f'<div class="poc-evidence"><div><b>{escape(item["display"])}</b> {badge(item["verdict"], VERDICT_TONES[item["verdict"]])}'
        f'<div class="nc-meta">{fmt_date(item["date"]) if item["date"] else "undated"}{" · " + escape(value) if value else ""} · '
        f'{escape(item["source_label"])} · {escape(item["source_record_id"])}</div>'
        f'<div class="nc-meta">{escape(item["reason"])}' + (f" · duplicate of {item['duplicate_of']}" if item["duplicate_of"] else "")
        + (f" · {escape(item['note'])}" if item["note"] else "") + "</div></div></div>",
        unsafe_allow_html=True,
    )
    if gap["status"] == "CLOSED" and not allow_invalidate:
        return
    reviewable = item["verdict"] == "review" and item["source"] != "narrative"
    if not (reviewable or allow_invalidate or item["verdict"] == "invalid"):
        return
    key = f"{gap['gap_id']}_{item['event_id']}"
    with st.container(horizontal=True, gap="small"):
        if reviewable and st.button("Verify", key=f"poc_ver_{key}", type="primary", disabled=not can_poc("Approve")) \
                and guard("Approve", "verify evidence"):
            run(vbc.verify_evidence, item["event_id"], actor(), "accepted", "Patient, date and value confirmed against the source",
                success="Evidence verified — measure re-evaluated")
            st.rerun()
        if (reviewable or item["verdict"] == "invalid") and st.button("Reject", key=f"poc_rej_{key}", type="secondary",
                                                                     disabled=not can_poc("Approve")) and guard("Approve", "reject evidence"):
            run(vbc.verify_evidence, item["event_id"], actor(), "rejected", "Rejected at review", success="Evidence rejected — measure re-evaluated")
            st.rerun()
        if allow_invalidate and st.button("Invalidate", key=f"poc_inv_{key}", type="tertiary", disabled=not can_poc("Approve"),
                                          help="Use when accepted evidence turns out to be wrong; the gap reopens if needed") \
                and guard("Approve", "invalidate evidence"):
            run(vbc.verify_evidence, item["event_id"], actor(), "rejected", "Evidence invalidated after acceptance", success="Evidence invalidated")
            st.rerun()


def _workflow(detail: dict) -> None:
    gap = detail["gap"]
    with st.container(border=True):
        card_title("Workflow", "People move the work; only the engine closes a gap after accepted evidence")
        if gap["status"] == "CLOSED":
            st.success(f"Closed {fmt_date(gap['closed_at'])}: {gap['closure']['reason']}")
            return
        key = gap["gap_id"]
        owner_options = ("—",) + OWNERS
        owner = st.selectbox("Owner", owner_options, index=owner_options.index(gap["owner"]) if gap["owner"] in owner_options else 0,
                             key=f"poc_w_owner_{key}")
        due = st.date_input("Due date", value=date.fromisoformat(gap["due_date"]), key=f"poc_w_due_{key}")
        disposition = st.selectbox("Disposition (does not change the measure)", ("",) + catalog.DISPOSITIONS,
                                   index=(("",) + catalog.DISPOSITIONS).index(gap["disposition"] or ""),
                                   format_func=lambda value: value.replace("_", " ").title() if value else "None", key=f"poc_w_disp_{key}")
        note = st.text_input("Note", key=f"poc_w_note_{key}", placeholder="Optional — goes to the history and audit log")
        if st.button("Save owner, due date and disposition", key=f"poc_w_save_{key}", type="secondary", disabled=not can_poc("Edit")) \
                and guard("Edit", "update a gap"):
            run(vbc.update_gap, key, actor(), owner=None if owner == "—" else owner, due_date=due.isoformat(), disposition=disposition, note=note,
                success="Gap updated")
            st.rerun()
        allowed = list(detail["allowed_next"])
        if allowed:
            st.markdown('<div class="nc-label" style="margin-top:.6rem">Move to</div>', unsafe_allow_html=True)
            schedule = None
            if "SCHEDULED" in allowed:
                schedule = st.date_input("Appointment / order date (for scheduling)", value=vbc.today() + timedelta(days=7), key=f"poc_w_sched_{key}")
            with st.container(horizontal=True, gap="small"):
                for step in allowed:
                    label = ACTION_LABELS.get(step, step.title()) if step != "ASSIGNED" or gap["status"] == "REVIEWED" else "Return to assigned"
                    if st.button(label, key=f"poc_w_{step}_{key}", type="primary" if step != "ASSIGNED" or gap["status"] == "REVIEWED" else "secondary",
                                 disabled=not can_poc("Edit")) and guard("Edit", "move a gap"):
                        run(vbc.update_gap, key, actor(), status=step, owner=None if owner == "—" else owner,
                            schedule_date=schedule.isoformat() if step == "SCHEDULED" and schedule else None, note=note,
                            success=f"Moved to {step.title()}")
                        st.rerun()
        if gap.get("schedule"):
            st.caption(f"Scheduled {fmt_date(gap['schedule']['date'])} · {gap['schedule']['ref']} — the gap stays open until the service is completed and verified.")


def _outreach(detail: dict) -> None:
    gap, patient = detail["gap"], detail["patient"]
    with st.container(border=True):
        card_title("Outreach", "Plain-language drafts, minimum necessary content, sent only after a person approves")
        if gap["gap_type"] == "DATA_CORRECTION":
            st.caption("Data corrections are internal work — no patient outreach.")
            return
        key = gap["gap_id"]
        if gap["status"] != "CLOSED":
            channel_col, button_col = st.columns([1.2, 1], vertical_alignment="bottom")
            channel = channel_col.selectbox("Channel", CHANNELS, index=CHANNELS.index(patient["preferred_channel"])
                                            if patient["preferred_channel"] in CHANNELS else 0, key=f"poc_o_ch_{key}")
            if button_col.button("Draft message", key=f"poc_o_draft_{key}", type="secondary", disabled=not can_poc("Edit")) \
                    and guard("Edit", "draft outreach"):
                run(vbc.draft_outreach, key, channel, actor(), success="Draft created — awaiting approval")
                st.rerun()
        for item in reversed(detail["outreach"]):
            tone = {"draft": "amber", "approved": "blue", "sent": "violet", "closed": "green"}[item["status"]]
            st.markdown(f'<div class="nc-cell"><b>{item["outreach_id"]} · {item["channel"]}</b> {badge(item["status"].title(), tone)}'
                        + (f' {badge(item["disposition"].replace("_", " ").title(), "gray")}' if item["disposition"] else "")
                        + f'<div class="nc-meta">{escape(item["generator"])} · drafted by {escape(item["created_by"])}'
                        + (f" · approved by {escape(item['approved_by'])}" if item["approved_by"] else "") + "</div></div>", unsafe_allow_html=True)
            oid = item["outreach_id"]
            if item["status"] == "draft":
                text = st.text_area("Message", value=item["text"], key=f"poc_o_text_{oid}", height=110)
                if st.button("Approve message", key=f"poc_o_ok_{oid}", type="primary", disabled=not can_poc("Approve")) \
                        and guard("Approve", "approve outreach"):
                    run(vbc.approve_outreach, oid, actor(), text, success="Message approved")
                    st.rerun()
            else:
                st.markdown(f'<div class="vbc-doc" style="white-space:pre-wrap;font-family:inherit">{escape(item["text"])}</div>', unsafe_allow_html=True)
            if item["status"] == "approved" and st.button("Send (simulated)", key=f"poc_o_send_{oid}", type="primary",
                                                          disabled=not can_poc("Edit")) and guard("Edit", "send outreach"):
                run(vbc.send_outreach, oid, actor(), success="Sent (simulated)")
                st.rerun()
            if item["status"] == "sent":
                outcome = st.selectbox("Outcome", vbc.OUTREACH_OUTCOMES, key=f"poc_o_out_{oid}",
                                       format_func=lambda value: value.replace("_", " ").title())
                when = st.date_input("Booked for", value=vbc.today() + timedelta(days=10), key=f"poc_o_when_{oid}") if outcome == "SCHEDULED" else None
                if st.button("Record outcome", key=f"poc_o_rec_{oid}", type="secondary", disabled=not can_poc("Edit")) \
                        and guard("Edit", "record an outreach outcome"):
                    run(vbc.record_outreach_outcome, oid, actor(), outcome, when.isoformat() if when else None, success="Outcome recorded")
                    st.rerun()


def _closure(detail: dict) -> None:
    gap = detail["gap"]
    with st.container(border=True):
        card_title("Closure", "Attach completion evidence; it stays pending until a reviewer verifies it, then the measure re-runs")
        if gap["status"] == "CLOSED":
            closure = gap["closure"]
            render_tiles((("Closure reason", closure["reason"]), ("Closed on", fmt_date(closure["date"])),
                          ("Accepted evidence", ", ".join(closure["evidence_ids"]) or "—"), ("Result", f"{closure['result_id']} · {closure['measure_state']}")))
            return
        measure_id = gap["measure_id"]
        key = gap["gap_id"]
        with st.form(f"poc_attach_{key}", clear_on_submit=True):
            cols = st.columns(4)
            when = cols[0].date_input("Service date", value=vbc.today(), max_value=vbc.today())
            value = value2 = None
            code = None
            flag = ""
            if measure_id == "GSD-P":
                value = cols[1].number_input("HbA1c (%)", 3.0, 20.0, 7.4, step=0.1)
            elif measure_id == "BPD-P":
                value = cols[1].number_input("Systolic", 60, 300, 132)
                value2 = cols[2].number_input("Diastolic", 30, 200, 80)
            elif measure_id == "COL-P":
                code = cols[1].selectbox("Modality", list(vbc.COL_MODALITIES), format_func=vbc.COL_MODALITIES.get)
                flag = cols[2].selectbox("Result", ("negative", "positive", ""), format_func=lambda v: v.title() or "Not applicable")
            reference = cols[3].text_input("Document / message ID", placeholder="e.g. ORU-2026-1101")
            note = st.text_input("Note", placeholder="Where the evidence came from")
            submitted = st.form_submit_button("Attach completion evidence", type="primary", disabled=not can_poc("Edit"))
        if submitted and guard("Edit", "attach evidence"):
            run(vbc.attach_evidence, key, actor(), event_date=when.isoformat(), value=value, value2=value2, code=code, document_ref=reference,
                note=note, result_flag=flag, success="Evidence attached — pending verification")
            st.rerun()
        pending = [item for item in detail["evidence_not_accepted"] if item["source"] == "workflow_upload" and item["verdict"] == "review"]
        if pending:
            st.info(f"{len(pending)} attached item(s) are waiting for verification above (Evidence not accepted → Verify).")
