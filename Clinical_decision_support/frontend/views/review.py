"""Clinical Review: consultation record, CDS workbench and SOAP note signing (steps 3-4)."""

from datetime import datetime
from html import escape

import pandas as pd
import requests
import streamlit as st

from frontend import access_control as ac
from frontend.api_client import HealthcareApiClient
from frontend.clinical_text import (
    append_selected_insights_to_note,
    append_to_plan,
    guideline_passages,
    recommendation_items,
    split_cds_sections,
)
from frontend.ehr_chart import care_gaps, medication_safety_checks, primary_coverage
from frontend.encounter_pipeline import MASTER_INSIGHTS, get_record_insights
from frontend.settings import ANALYSIS_VERSION
from frontend.ui.components import (
    badge,
    card_title,
    page_header,
    page_title,
    record_subtitle,
    render_banner,
    render_checklist,
    render_clinical_graph,
    render_context_strip,
    render_grouped_insights,
    render_patient_table,
    render_stepper,
    render_summary,
    render_transcript,
)
from frontend.ui.data import backend_error_message, record_chart
from frontend.ui.shell import render_page
from frontend.ui.state import (
    ACCESS,
    can,
    can_open,
    current_user,
    go_to,
    log_action,
    open_billing,
    open_record,
    persist_access,
    save_record,
    set_edit_summary,
    start_new_consultation,
    user_label,
)
from frontend.views.consultation import update_ehr_clinical_note
from frontend.views.ocr_widgets import render_document_ocr


def _log_history(record, action):
    history = record.setdefault("review_history", [])
    history.append({"at": datetime.now().strftime("%d %b %Y %H:%M"), "action": action})


def _save_note_draft(record):
    if record.get("status") == "Approved":
        return
    record["summary"] = st.session_state.edit_summary
    save_record(record)
    try:
        update_ehr_clinical_note(record)
    except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
        st.warning(f"Draft saved locally but EHR note sync failed: {error}")
    st.toast("Draft saved")


def _select_record_for_review():
    page_title("CLINICAL REVIEW & CDS", "Clinical Decision Support", "Select a consultation to review")
    records = st.session_state.records
    if not records:
        st.info("No consultations yet. Start one to generate a SOAP draft and CDS recommendations.")
        st.button("New Consultation", type="primary", on_click=start_new_consultation, key="review_empty_new")
        return
    with st.container(border=True):
        render_patient_table(records)


def _review_step_record(record, is_approved):
    with st.container(border=True):
        card_title("Source Conversation", "Transcript used for the SOAP draft and CDS")
        st.text_area(
            "Transcript",
            value=record.get("transcript", "No transcript available."),
            height=220,
            disabled=True,
            label_visibility="collapsed",
        )
        if not is_approved:
            if st.button(
                "Regenerate SOAP draft from transcript",
                key="review_regen_soap",
                disabled=not record.get("transcript", "").strip(),
            ):
                try:
                    with st.spinner("Generating SOAP note..."):
                        regenerated = HealthcareApiClient().regenerate_encounter(record["transcript"])
                    record["summary"] = regenerated.get("soap_note") or regenerated.get("patient_summary", "")
                    record["patient_summary"] = regenerated.get("patient_summary", "")
                    record["key_highlights"] = regenerated.get("key_highlights", [])
                    record["transcript"] = regenerated.get("transcript", record.get("transcript", ""))
                    record["summary"] = record["summary"] or record["patient_summary"]
                    _log_history(record, "SOAP draft regenerated")
                    save_record(record)
                    try:
                        update_ehr_clinical_note(record)
                    except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                        st.warning(f"SOAP note was saved locally but could not sync to the EHR: {error}")
                    set_edit_summary(record["summary"])
                    st.success("SOAP note generated from the stored transcript.")
                except (RuntimeError, requests.exceptions.HTTPError) as error:
                    st.error(f"SOAP regeneration failed: {backend_error_message(error)}")
    render_document_ocr(record, can("Clinical Review", "Edit"))
    render_transcript(record, read_only=True)
    _, next_col = st.columns([6.5, 3.5])
    if next_col.button("Proceed to Clinical Review & CDS →", key="review_record_next", type="primary", width="stretch"):
        st.session_state.review_step = "cds"
        st.rerun()


def _render_recommendation_card(record, index, item, is_approved):
    decisions = record.setdefault("cds_decisions", {})
    decision = decisions.get(str(index))
    tone = {"HIGH": "red", "MED": "amber", "ROUTINE": "gray"}[item["priority"]]
    css = {"HIGH": "high", "MED": "med", "ROUTINE": "low"}[item["priority"]]
    with st.container(key=f"rec_{css}_{index}"):
        _render_recommendation_body(record, index, item, is_approved, decisions, decision, tone)


def _render_recommendation_body(record, index, item, is_approved, decisions, decision, tone):
    decision_html = ""
    if decision:
        decision_html = f'<div class="nc-decision">{badge(decision["status"].title(), "green" if decision["status"] == "accepted" else "violet")} {escape(decision.get("note", ""))} · {escape(decision.get("at", ""))}</div>'
    st.markdown(
        f'<div class="nc-rec">{badge(item["priority"], tone)}'
        f'<div class="nc-rec-title">{escape(item["title"])}</div>'
        + (f'<div class="nc-rec-body">{escape(item["body"])}</div>' if item["body"] else "")
        + f"{decision_html}</div>",
        unsafe_allow_html=True,
    )
    if is_approved:
        return

    def decide(status, note=""):
        decisions[str(index)] = {
            "status": status,
            "note": note,
            "at": datetime.now().strftime("%d %b %H:%M"),
            "title": item["title"],
        }
        _log_history(record, f"Recommendation {status}: {item['title']}")
        save_record(record)

    def push_to_note():
        set_edit_summary(append_to_plan(st.session_state.edit_summary, item["title"]))
        record["summary"] = st.session_state.edit_summary
        _log_history(record, f"Pushed to plan: {item['title']}")
        save_record(record)
        st.toast("Added to the Plan section of the SOAP draft")

    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.button("✓ Accept", key=f"cds_accept_{index}", type="primary", on_click=decide, args=("accepted",), width="content")
        with st.popover("Modify"):
            st.text_area("Modified recommendation", value=item["title"], key=f"cds_modify_text_{index}")
            st.button(
                "Save modification",
                key=f"cds_modify_{index}",
                on_click=lambda: decide("modified", st.session_state.get(f"cds_modify_text_{index}", "").strip()),
            )
        with st.popover("Override…"):
            reason = st.text_input("Override reason", key=f"cds_override_reason_{index}", placeholder="Clinical rationale")
            st.button(
                "Record override",
                key=f"cds_override_{index}",
                disabled=not reason.strip(),
                on_click=lambda: decide("overridden", st.session_state.get(f"cds_override_reason_{index}", "").strip()),
            )
        st.button("Push to note ↗", key=f"cds_push_{index}", type="tertiary", on_click=push_to_note, width="content")


def _review_step_cds(record, is_approved, chart):
    recommendation_text = record.get("recommendations") or ""
    sections = split_cds_sections(recommendation_text)
    items = recommendation_items(sections["Recommendation"])
    passages = guideline_passages(record.get("retrieved_guidelines"))
    if passages:
        render_banner(
            f"Evidence quality guard active · {len(passages)} guideline passage(s) attached",
            "Recommendations were generated only from the retrieved knowledge-base passages listed under Evidence.",
            icon="&#128737;",
        )
    else:
        render_banner(
            "Evidence quality guard: no relevant guideline passages retrieved",
            "The knowledge base returned no passage for this encounter — treat recommendations as unreferenced and verify clinically.",
            tone="warn",
            icon="&#128737;",
        )
    main_col, side_col = st.columns([6.3, 3.7], gap="medium")
    with main_col:
        with st.container(border=True):
            if not is_approved:
                with st.container(horizontal=True, horizontal_alignment="right"):
                    regenerate = st.button(
                        "↻ Regenerate CDS",
                        key="review_regen_cds",
                        type="tertiary",
                        disabled=not record.get("transcript", "").strip(),
                    )
                if regenerate:
                    try:
                        with st.spinner("Generating CDS recommendations..."):
                            regenerated = HealthcareApiClient().regenerate_cds(cds_payload(record))
                        recommendations = regenerated.get("recommendations")
                        if not recommendations or not str(recommendations).strip():
                            raise RuntimeError(
                                "The CDS service returned no recommendation. The existing recommendation was kept."
                            )
                        record["retrieved_guidelines"] = regenerated.get(
                            "retrieved_guidelines", record.get("retrieved_guidelines", "")
                        )
                        record["recommendations"] = recommendations
                        record["cds_requested"] = True
                        record["analysis_version"] = ANALYSIS_VERSION
                        record["cds_decisions"] = {}
                        _log_history(record, "CDS regenerated")
                        save_record(record)
                        st.rerun()
                    except (RuntimeError, requests.exceptions.HTTPError) as error:
                        st.error(f"CDS regeneration failed: {backend_error_message(error)}")
            rec_tab, reasoning_tab, evidence_tab, safety_tab, history_tab = st.tabs(
                [f"Recommendations ({len(items)})", "Reasoning", "Evidence", "Safety note", "History"]
            )
            with rec_tab:
                if items:
                    for index, item in enumerate(items):
                        _render_recommendation_card(record, index, item, is_approved)
                    st.caption(
                        "Priority is derived from the recommendation wording (e.g. avoid, urgent, allergy → HIGH). Clinician judgment governs."
                    )
                else:
                    st.markdown(
                        '<div class="code-empty">No CDS recommendations yet. Use “Regenerate CDS” to request them.</div>',
                        unsafe_allow_html=True,
                    )
            with reasoning_tab:
                st.markdown(sections["Reasoning"] or "_No reasoning provided._")
            with evidence_tab:
                if passages:
                    for passage in passages:
                        st.markdown(
                            f'<div class="nc-excerpt" style="margin-bottom:.45rem"><small>§ {escape(passage["source"])}</small><div class="nc-rec-body">{escape(passage["text"][:900])}</div></div>',
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("No guideline passages were retrieved for this encounter.")
            with safety_tab:
                st.markdown(sections["Safety Note"] or "_No safety note provided._")
            with history_tab:
                history = list(record.get("review_history") or [])
                history.insert(0, {"at": f"{record.get('date', '')} {record.get('time', '')}", "action": "Consultation created"})
                if record.get("approval_date"):
                    history.append({"at": record["approval_date"], "action": "Note signed"})
                st.dataframe(
                    pd.DataFrame(history).rename(columns={"at": "When", "action": "Event"}), hide_index=True, width="stretch"
                )
    with side_col:
        plan_text = f"{recommendation_text}\n{st.session_state.edit_summary}"
        with st.container(border=True):
            card_title("Medication Safety", "Deterministic checks over the EHR chart and plan")
            render_checklist(medication_safety_checks(chart, plan_text), "Link an EHR patient to run medication checks.")
        with st.container(border=True):
            coverage = primary_coverage(chart) if chart else {}
            card_title("Care Gaps · VBC", f"{coverage.get('Plan_Type') or 'Commercial'} measure set")
            render_checklist(care_gaps(chart), "No quality measure applies to the active problem list.")
        with st.container(border=True):
            card_title("Coding & Billing", "ICD-10 / CPT suggestions and the guarded draft bill")
            code_col, bill_col = st.columns(2)
            code_col.button(
                "ICD-10 / CPT codes →",
                key="cds_open_codes",
                type="secondary",
                width="stretch",
                disabled=not can_open("codes"),
                on_click=open_record,
                args=(record, "codes"),
            )
            bill_col.button(
                "Bill generation →",
                key="cds_open_billing",
                type="secondary",
                width="stretch",
                disabled=not can_open("billing"),
                on_click=open_billing,
                args=(record,),
            )
        if not is_approved:
            with st.container(border=True):
                card_title("Add Clinical Insight")
                for index in range(st.session_state.insight_count):
                    st.selectbox(f"Insight {index + 1}", MASTER_INSIGHTS, key=f"insight_{index}", label_visibility="collapsed")

                def push_selected_insights_to_note():
                    selected = [st.session_state.get(f"insight_{i}", "") for i in range(st.session_state.insight_count)]
                    manual_insights = record.setdefault("manual_insights", [])
                    for insight in selected:
                        if insight != "None" and insight not in manual_insights:
                            manual_insights.append(insight)
                    set_edit_summary(append_selected_insights_to_note(st.session_state.edit_summary, selected))
                    record["summary"] = st.session_state.edit_summary
                    st.session_state.detected_insights = get_record_insights(record)
                    save_record(record)
                    try:
                        update_ehr_clinical_note(record)
                    except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                        st.warning(f"Note was updated locally but could not sync to the EHR: {error}")
                    st.session_state.insight_count = 1

                add_col, push_col = st.columns(2)
                if add_col.button(
                    "Add Insight", width="stretch", disabled=st.session_state.insight_count >= 6, key="cds_add_insight"
                ):
                    st.session_state.insight_count += 1
                    st.rerun()
                push_col.button(
                    "Push to Note",
                    type="primary",
                    width="stretch",
                    on_click=push_selected_insights_to_note,
                    key="cds_push_insights",
                )
    back_col, _, next_col = st.columns([2, 5, 3])
    if back_col.button("← Back to Record", key="review_cds_back", width="stretch"):
        st.session_state.review_step = "record"
        st.rerun()
    if next_col.button("Proceed to SOAP Note →", key="review_cds_next", type="primary", width="stretch"):
        st.session_state.review_step = "soap"
        st.rerun()


def _review_step_soap(record, is_approved, can_edit=True, can_sign=True):
    editable = not is_approved and can_edit
    left, right = st.columns([6.2, 3.8], gap="medium")
    with left:
        with st.container(border=True):
            status_badge = badge("SIGNED · locked", "green") if is_approved else badge("SOAP draft", "amber")
            card_title("SOAP Note", "Signed notes are locked; drafts can be edited before signing", trailing=status_badge)
            editor_key = f"soap_editor_{record.get('id')}_{st.session_state.soap_rev}"
            if editable and editor_key in st.session_state:
                st.session_state.edit_summary = st.session_state[editor_key]
            render_summary(st.session_state.edit_summary)
            if editable:
                with st.expander("Edit SOAP draft", expanded=False):
                    st.text_area(
                        "Edit clinical summary",
                        value=st.session_state.edit_summary,
                        height=260,
                        key=editor_key,
                        label_visibility="collapsed",
                    )
        edited_df = render_transcript(record, read_only=not editable)
    with right:
        with st.container(border=True):
            card_title("Detected Insights", "Grouped clinical signals used for coding")
            insights = st.session_state.detected_insights
            if insights:
                render_grouped_insights(insights)
            else:
                st.caption("No insights detected in this transcript.")
        with st.container(border=True):
            card_title("Relationship Graph", "Clinical relationships in this encounter")
            render_clinical_graph(record.get("name", "Patient"), st.session_state.detected_insights)

    def persist(approved=False):
        if record.get("status") == "Approved":
            return False
        record["summary"] = st.session_state.edit_summary
        record["transcript_data"] = edited_df.to_dict("records")
        if approved:
            record["status"] = "Approved"
            record["approval_date"] = datetime.now().strftime("%d/%m/%Y")
            record["signed_by"] = current_user()["username"]
            _log_history(record, f"Note signed and locked by {current_user()['name']}")
            log_action("note.sign", f"{record.get('id')} {record.get('name', '')}")
        save_record(record)
        try:
            update_ehr_clinical_note(record, approved=approved)
        except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
            st.warning(f"Consultation saved locally but EHR note sync failed: {error}")
        return True

    back_col, codes_col, draft_col, final_col = st.columns([2.2, 3.6, 1.8, 2.4])
    if back_col.button("← Back to Clinical Review", key="review_soap_back", width="stretch"):
        st.session_state.review_step = "cds"
        st.rerun()
    codes_col.button(
        "View ICD-10 / CPT codes →",
        key="soap_open_codes",
        type="tertiary",
        disabled=not can_open("codes"),
        on_click=open_record,
        args=(record, "codes"),
    )
    if is_approved:
        draft_col.button(
            "Billing →",
            key="soap_open_billing",
            width="stretch",
            disabled=not can_open("billing"),
            on_click=open_billing,
            args=(record,),
        )
        final_col.button(
            "Proceed to Coding →",
            key="review_to_coding",
            type="primary",
            width="stretch",
            disabled=not can_open("codes"),
            on_click=open_record,
            args=(record, "codes"),
        )
    else:
        if draft_col.button("Save Draft", key="review_soap_draft", width="stretch", disabled=not can_edit):
            if persist(False):
                st.success("Draft and audit trail updated.")
        if final_col.button(
            "Sign & Finalize ✓",
            key="review_soap_sign",
            type="primary",
            width="stretch",
            disabled=not can_sign,
            help=None if can_sign else "Your role cannot sign clinical notes (Clinical Review · Approve).",
        ):
            if persist(True):
                st.session_state.code_record_id = None
                go_to("codes")
                st.rerun()


def render_assignment_control(container, record):
    """Assignee badge plus a reassign popover for admins and the current assignee."""
    assignee = record.get("assigned_to")
    covering = f" · covering for {user_label(record['rerouted_from'])}" if record.get("rerouted_from") else ""
    container.markdown(
        badge(f"Assigned: {user_label(assignee)}{covering}" if assignee else "Unassigned queue", "blue" if assignee else "amber"),
        unsafe_allow_html=True,
    )
    me = current_user()["username"]
    if not (can("Admin Dashboard", "Edit") or (assignee and assignee == me)):
        return
    store = ACCESS["store"]
    candidates = [user["username"] for user in store["users"] if user.get("active", True)]
    if not candidates:
        return
    with container.popover("Reassign"):

        def describe(username):
            user = ac.get_user(store, username)
            away = "" if ac.is_available(user) else " · out of office → routes to fallback"
            return f"{user['name']} · {user['role']}{away}"

        default = candidates.index(assignee) if assignee in candidates else 0
        target = st.selectbox("Assign to", candidates, index=default, format_func=describe, key=f"reassign_to_{record.get('id')}")
        reason = st.text_input("Reason", key=f"reassign_reason_{record.get('id')}", placeholder="e.g. workload balancing")
        if st.button("Reassign", key=f"reassign_go_{record.get('id')}", type="primary"):
            final = ac.assign_record(store, record, target, me, reason.strip() or "Manual reassignment")
            save_record(record)
            persist_access()
            st.toast(f"Assigned to {user_label(final)}" if final else "No available user — moved to the unassigned queue")
            st.rerun()


def show_review_note():
    render_page("review")
    record = st.session_state.current_record
    if not record:
        _select_record_for_review()
        return
    is_approved = record.get("status") == "Approved"
    if st.session_state.last_record_id != record.get("id"):
        st.session_state.edit_summary = record.get("summary") or record.get("patient_summary", "")
        st.session_state.last_record_id = record.get("id")
        st.session_state.soap_rev += 1
    step = st.session_state.review_step if st.session_state.review_step in {"record", "cds", "soap"} else "cds"
    titles = {
        "record": ("CLINICAL REVIEW & CDS", "Consultation Record"),
        "cds": ("CLINICAL REVIEW & CDS", "Clinical Decision Support"),
        "soap": ("SOAP NOTE & SIGN", "SOAP Note & Sign"),
    }
    eyebrow, title = titles[step]
    can_edit = can("Clinical Review", "Edit")
    locked = is_approved or not can_edit
    actions = page_header(eyebrow, title, record_subtitle(record), action_weight=4.2)
    render_assignment_control(actions, record)
    if not locked:
        if actions.button("Save Draft", key="review_header_save", type="tertiary"):
            _save_note_draft(record)
    elif is_approved:
        actions.markdown(badge("SIGNED", "green"), unsafe_allow_html=True)
    else:
        actions.markdown(badge("READ-ONLY", "gray"), unsafe_allow_html=True)
    actions.button(
        "All consultations",
        key="review_header_list",
        type="tertiary",
        on_click=lambda: st.session_state.update(current_record=None),
    )
    render_stepper({"record": 1, "cds": 2, "soap": 3}[step])
    chart = record_chart(record)
    if step in {"record", "cds"}:
        render_context_strip(chart, record)
    if step == "record":
        _review_step_record(record, locked)
    elif step == "cds":
        _review_step_cds(record, locked, chart)
    else:
        _review_step_soap(record, is_approved, can_edit=can_edit, can_sign=can("Clinical Review", "Approve"))


def cds_payload(record):
    """Encounter sent to CDS, with attached document text appended to the summary."""
    documents = record.get("documents") or []
    if not documents:
        return record
    extra = "\n\n".join(f"[{doc.get('type', 'Document')}: {doc['name']}]\n{doc['text']}" for doc in documents)
    return {
        **record,
        "patient_summary": f"{record.get('patient_summary') or record.get('summary', '')}\n\nAttached documents:\n{extra}",
    }
