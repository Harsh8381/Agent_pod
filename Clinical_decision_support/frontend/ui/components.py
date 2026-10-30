"""Reusable presentation components (HTML snippets styled by ui.theme)."""

import math
import re
from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st

from frontend.clinical_text import clean_clinical_text, get_insight_category, parse_transcript_entries
from frontend.ehr_chart import chart_context, format_date
from frontend.ui.data import record_patient_id
from frontend.ui.state import open_record, user_label

CONSULT_STEPS = ("Patient", "Record", "Clinical Review & CDS", "SOAP Note & Sign")


def badge(text, tone="gray"):
    return f'<span class="nc-badge nc-b-{tone}">{escape(str(text))}</span>'


def initials(name):
    parts = [part for part in re.split(r"\s+", str(name or "").strip()) if part]
    return "".join(part[0] for part in parts[:2]).upper() or "?"


def page_title(eyebrow, title, subtitle=""):
    st.markdown(
        f'<div class="nc-eyebrow">{escape(eyebrow)}</div>'
        f'<div class="page-heading">{escape(title)}</div>'
        + (f'<div class="page-kicker">{escape(subtitle)}</div>' if subtitle else ""),
        unsafe_allow_html=True,
    )


def page_header(eyebrow, title, subtitle="", action_weight=4.0):
    """Render the page title block and return a right-aligned container for actions."""
    left, right = st.columns([10 - action_weight, action_weight], vertical_alignment="center")
    with left:
        page_title(eyebrow, title, subtitle)
    with right:
        actions = st.container(horizontal=True, horizontal_alignment="right", gap="small")
    return actions


def render_stepper(current_index):
    parts = []
    for index, label in enumerate(CONSULT_STEPS):
        state = "done" if index < current_index else "current" if index == current_index else ""
        mark = "&#10003;" if index < current_index else str(index + 1)
        parts.append(f'<div class="nc-step {state}"><i>{mark}</i>{escape(label)}</div>')
    st.markdown(f'<div class="nc-stepper">{"".join(parts)}</div>', unsafe_allow_html=True)


def render_banner(title, detail="", tone="info", icon="&#9432;", trailing=""):
    tone_class = "" if tone == "info" else tone
    st.markdown(
        f'<div class="nc-banner {tone_class}"><span>{icon}</span><div class="nc-grow"><b>{escape(title)}</b>'
        + (f"<small>{escape(detail)}</small>" if detail else "")
        + f"</div>{trailing}</div>",
        unsafe_allow_html=True,
    )


def card_title(title, subtitle="", trailing=""):
    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:.6rem">'
        f'<div><div class="section-title">{escape(title)}</div>'
        + (f'<div class="section-subtitle">{escape(subtitle)}</div>' if subtitle else "")
        + f"</div><div>{trailing}</div></div>",
        unsafe_allow_html=True,
    )


def render_kpi_card(label, value, tone, context):
    """KPI tile; tone is a palette family: mint, sky, lav, peach, butter or rose."""
    st.markdown(
        f'<div class="kpi-card tone-{tone}">'
        f'<div class="kpi-title">{escape(label)}</div><div class="kpi-value">{value}</div>'
        f'<div class="kpi-context">{escape(context)}</div></div>',
        unsafe_allow_html=True,
    )


def render_status_badge(status):
    css_class = "status-approved" if status == "Approved" else "status-pending"
    label = "SIGNED" if status == "Approved" else "DRAFT"
    return f'<span class="status-pill {css_class}">{label}</span>'


def render_tiles(items):
    tiles = "".join(
        f'<div class="nc-tile"><small>{escape(label)}</small><b>{escape(str(value))}</b></div>' for label, value in items
    )
    st.markdown(f'<div class="nc-tiles">{tiles}</div>', unsafe_allow_html=True)


def render_html_table(headers, rows, row_classes=None):
    """rows hold pre-escaped HTML cell strings."""
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = []
    for index, row in enumerate(rows):
        css = f' class="{row_classes[index]}"' if row_classes and row_classes[index] else ""
        body.append(f"<tr{css}>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    st.markdown(
        f'<div class="nc-table-wrap"><table class="nc-table"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def render_context_strip(chart, record=None):
    if not chart or not chart.get("patient"):
        source = "No EHR patient is linked to this consultation, so chart context is unavailable."
        if record is not None and record_patient_id(record):
            source = "The EHR backend is unavailable; chart context could not be loaded."
        render_banner("Chart context unavailable", source, tone="warn", icon="&#9888;")
        return
    context = chart_context(chart)
    counts = chart.get("record_counts") or {}
    footer = " · ".join(
        f"{name.replace('_', ' ')} {count}"
        for name, count in counts.items()
        if name in {"Problems", "Medications", "Allergies", "Vitals", "Labs"}
    )
    st.markdown(
        '<div class="nc-context">'
        f"<div><small>Problems</small><b>{escape(context['problems'])}</b></div>"
        f"<div><small>Medications</small><b>{escape(context['medications'])}</b></div>"
        f"<div><small>Allergies</small><b>{escape(context['allergies'])}</b></div>"
        f"<div><small>Vitals</small><b>{escape(context['vitals'])}</b></div>"
        f"<div><small>Labs</small><b>{escape(context['labs'])}</b></div>"
        f'<div class="nc-context-foot">EHR chart · {escape(footer)} · refreshed {datetime.now().strftime("%d %b %Y %H:%M")}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_checklist(items, empty_message):
    if not items:
        st.markdown(f'<div class="code-empty">{escape(empty_message)}</div>', unsafe_allow_html=True)
        return
    rows = "".join(
        f'<div class="nc-check"><div><span class="nc-dot tone-{tone}"></span>{escape(label)}'
        f"<small>{escape(detail)}</small></div>{badge(status, tone)}</div>"
        for label, status, tone, detail in items
    )
    st.markdown(f'<div class="nc-checklist">{rows}</div>', unsafe_allow_html=True)


def render_grouped_insights(insights):
    grouped = {category: [] for category in ("Diagnoses", "Symptoms", "Risk Factors", "Tests/Procedures")}
    for insight in insights:
        category, css_class = get_insight_category(insight)
        grouped[category].append((insight, css_class))
    parts = ['<div class="insight-scroll">']
    for category, values in grouped.items():
        if not values:
            continue
        parts.append(
            f'<div class="insight-category"><div class="insight-category-heading">{escape(category)} ({len(values)})</div><div class="insight-category-items">'
        )
        for insight, css_class in values:
            parts.append(f'<div class="insight-chip {css_class}"><b>{escape(insight)}</b></div>')
        parts.append("</div></div>")
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_clinical_graph(patient_name, insights):
    categories = {"Diagnoses": [], "Symptoms": [], "Risk Factors": [], "Tests/Procedures": []}
    for insight in insights:
        category, _ = get_insight_category(insight)
        categories[category].append(insight)
    class_map = {"Diagnoses": "diagnosis", "Symptoms": "symptom", "Risk Factors": "risk", "Tests/Procedures": "procedure"}
    parts = [
        '<div class="clinical-graph-v2"><div class="graph-hierarchy-v2">',
        f'<div class="graph-patient-card">Patient: {escape(patient_name[:40])}</div>',
        '<div class="graph-connector-main"></div><div class="graph-categories">',
    ]
    for category, values in categories.items():
        header = "Tests" if category == "Tests/Procedures" else category
        parts.append(f'<div><div class="graph-category-header">{header}</div><div class="graph-category-content">')
        if values:
            for value in values:
                parts.append(f'<div class="graph-node-card graph-node-{class_map[category]}">{escape(value)}</div>')
        else:
            parts.append(f'<div class="graph-empty-state">No {category.lower()} detected</div>')
        parts.append("</div></div>")
    parts.append("</div></div></div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_summary(summary):
    text = clean_clinical_text(summary)
    if not text:
        st.caption("No clinical summary is available.")
        return
    headings = {
        "subjective",
        "objective",
        "chief complaint",
        "history of present illness",
        "past medical history",
        "current medications",
        "assessment",
        "plan",
    }
    fields, current_heading, current_lines = [], None, []
    for line in text.splitlines():
        inline_heading = re.match(r"^\s*(Subjective|Objective|Assessment|Plan)\s*:\s*(.*)$", line, re.IGNORECASE)
        if inline_heading:
            if current_heading:
                fields.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = inline_heading.group(1)
            current_lines = [inline_heading.group(2)] if inline_heading.group(2) else []
            continue
        stripped = line.strip().strip("#*- ").rstrip(":")
        if stripped.lower() in headings:
            if current_heading and current_lines:
                fields.append((current_heading, "\n".join(current_lines).strip()))
            current_heading, current_lines = stripped, []
        elif current_heading:
            current_lines.append(line)
    if current_heading and current_lines:
        fields.append((current_heading, "\n".join(current_lines).strip()))
    if not fields:
        st.markdown(f'<div class="summary-text">{escape(text)}</div>', unsafe_allow_html=True)
        return
    for heading, value in fields:
        st.markdown(
            f'<div><div class="nc-label" style="margin:.2rem 0 .25rem">{escape(heading.upper())}</div>'
            f'<div class="summary-text">{escape(value)}</div></div>',
            unsafe_allow_html=True,
        )


def render_code_results(values, empty_message):
    if not values:
        st.markdown(f'<div class="code-empty">{escape(empty_message)}</div>', unsafe_allow_html=True)
        return
    if isinstance(values, list) and values and isinstance(values[0], dict):
        st.dataframe(pd.DataFrame(values), hide_index=True, width="stretch")
    else:
        for value in values if isinstance(values, list) else [values]:
            st.markdown(f"- {escape(str(value))}")


PATIENT_TABLE_PAGE_SIZE = 6


def render_patient_table(records):
    """Paginated consultation queue with an Open/Review action per row."""
    per_page = PATIENT_TABLE_PAGE_SIZE
    state_key = "dash_page"
    total_pages = max(1, math.ceil(len(records) / per_page))
    st.session_state[state_key] = min(max(st.session_state[state_key], 1), total_pages)
    current_page = st.session_state[state_key]
    current_records = records[(current_page - 1) * per_page : current_page * per_page]
    st.markdown(
        '<div class="patient-table-header"><span>Patient</span><span>ID</span><span>Doctor / assignee</span>'
        "<span>Date</span><span>Note</span><span>Action</span></div>",
        unsafe_allow_html=True,
    )
    for row_index, record in enumerate(current_records):
        columns = st.columns([2.2, 1.15, 1.7, 1.55, 1.05, 1.15], vertical_alignment="center")
        columns[0].markdown(
            f'<div class="nc-patient"><div class="nc-initials">{escape(initials(record.get("name")))}</div><div><b>{escape(record.get("name", ""))}</b><small>{escape(str(record.get("age", "")))}y · {escape(record.get("gender", ""))}</small></div></div>',
            unsafe_allow_html=True,
        )
        columns[1].markdown(f'<span class="table-meta">{escape(record.get("id", ""))}</span>', unsafe_allow_html=True)
        assignee = record.get("assigned_to")
        assignee_text = f"→ {user_label(assignee)}" if assignee else "→ Unassigned queue"
        if record.get("rerouted_from"):
            assignee_text += f" (for {user_label(record['rerouted_from'])})"
        columns[2].markdown(
            f'<div class="table-two-line"><span class="table-meta">{escape(record.get("doctor", "N/A"))}</span><small>{escape(assignee_text)}</small></div>',
            unsafe_allow_html=True,
        )
        date_text = record.get("approval_date") if record.get("status") == "Approved" else record.get("date", "")
        columns[3].markdown(
            f'<div class="table-two-line"><span>{escape(date_text or "")}</span><small>{escape(record.get("time", ""))}</small></div>',
            unsafe_allow_html=True,
        )
        columns[4].markdown(render_status_badge(record.get("status", "Pending")), unsafe_allow_html=True)
        key_suffix = f"{record.get('id', 'record')}_{row_index}_{current_page}"
        columns[5].button(
            "View" if record.get("status") == "Approved" else "Review",
            key=f"record_{key_suffix}",
            on_click=open_record,
            args=(record,),
            width="stretch",
        )
        st.markdown('<div class="table-row-rule"></div>', unsafe_allow_html=True)
    if total_pages > 1:
        previous, info, next_col = st.columns([1, 2, 1], vertical_alignment="center")
        if previous.button("Previous", key=f"previous_{state_key}", disabled=current_page == 1, width="stretch"):
            st.session_state[state_key] -= 1
            st.rerun()
        info.markdown(f'<div class="pagination-label">Page {current_page} of {total_pages}</div>', unsafe_allow_html=True)
        if next_col.button("Next", key=f"next_{state_key}", disabled=current_page == total_pages, width="stretch"):
            st.session_state[state_key] += 1
            st.rerun()


def render_transcript(record, read_only=False):
    with st.expander("Transcript Audit Trail", expanded=False):
        doctor, patient = record.get("doctor", "Doctor"), record.get("name", "Patient")
        rows = record.get("transcript_data") or parse_transcript_entries(record.get("transcript", ""), doctor, patient)
        frame = pd.DataFrame(rows or [{"Speaker": doctor, "Text": "No transcript found."}])
        label = "Read-only clinical conversation" if read_only else "Editable clinical conversation"
        st.markdown(f'<div class="audit-note">{label} · {len(frame)} entries</div>', unsafe_allow_html=True)
        if read_only:
            st.dataframe(frame, width="stretch", hide_index=True)
            return frame
        return st.data_editor(
            frame,
            width="stretch",
            num_rows="dynamic",
            hide_index=True,
            column_config={
                "Speaker": st.column_config.SelectboxColumn("Speaker", options=[doctor, patient, "Unknown"], required=True),
                "Text": st.column_config.TextColumn("Spoken Text", width="large"),
            },
        )


def record_subtitle(record):
    parts = [
        record.get("name", ""),
        record.get("id", ""),
        record.get("ehr_encounter_id") or "",
        record.get("doctor", ""),
        format_date(record.get("date"), ""),
    ]
    return " · ".join(str(part) for part in parts if part)
