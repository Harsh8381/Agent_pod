"""Oversight dashboard: every patient across documentation, coding and billing.

`build_admin_workflow_rows` is pure (no Streamlit) so it can be unit-tested; the page
renders its output with the shared UI components.
"""

from datetime import datetime, timedelta
from html import escape

import streamlit as st

from frontend.ui.components import badge, card_title, initials, page_title, render_banner, render_kpi_card, render_tiles
from frontend.ui.data import fetch_ehr_export
from frontend.ui.shell import render_page
from frontend.ui.state import ACCESS, open_record

STAGES = ("Ambient Scribe", "Clinical Review", "Coding", "Billed", "EHR record")
STAGE_TONES = {"Ambient Scribe": "gray", "Clinical Review": "amber", "Coding": "violet", "Billed": "green", "EHR record": "blue"}
PENDING_ITEMS = {
    "Ambient Scribe": "SOAP draft not generated yet",
    "Clinical Review": "Clinical note awaiting sign-off",
    "Coding": "Codes awaiting validation and billing",
    "Billed": "Claim draft sent to RCM",
    "EHR record": "No consultation in this workspace",
}
DATE_WINDOWS = ("All time", "Today", "Last 7 days", "Last 30 days")
QUEUE_PAGE_SIZE = 12


def _text(value, fallback=""):
    text = str(value or "").strip()
    return text or fallback


def _parse_datetime(date_value, time_value=""):
    date_text, time_text = _text(date_value), _text(time_value) or "00:00"
    for pattern in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_text[:10]} {time_text}", pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(date_text)
    except ValueError:
        return None


def _consultation_stage(record):
    if record.get("bill_generated_at"):
        return "Billed"
    if record.get("status") == "Approved":
        return "Coding"
    if record.get("summary") or record.get("patient_summary"):
        return "Clinical Review"
    return "Ambient Scribe"


def _patient_name(patient):
    """Legal name, else first + last name (patients registered from the app have no legal name)."""
    full_name = " ".join(part for part in (_text(patient.get("First_Name")), _text(patient.get("Last_Name"))) if part)
    return _text(patient.get("Legal_Name"), full_name or "Unknown patient")


def _sheet_records(payload, sheet):
    return ((payload or {}).get("sheets", {}).get(sheet) or {}).get("records") or []


def build_admin_workflow_rows(local_records, ehr_payload, user_names):
    """One row per patient: consultations first, then EHR patients without a consultation.

    `ehr_payload` is the EHR export ({"sheets": {"Patient_Master": {"records": [...]}, ...}});
    `user_names` maps usernames to display names for the owner column.
    """
    patients = {
        _text(row.get("Patient_ID")): row for row in _sheet_records(ehr_payload, "Patient_Master") if row.get("Patient_ID")
    }
    latest_encounters = {}
    for encounter in _sheet_records(ehr_payload, "Encounters"):
        patient_id = _text(encounter.get("Patient_ID"))
        current = latest_encounters.get(patient_id)
        if current is None or _text(encounter.get("Date")) > _text(current.get("Date")):
            latest_encounters[patient_id] = encounter

    rows, linked = [], set()
    for record in local_records or []:
        patient_id = _text(record.get("ehr_patient_id") or record.get("patient_id"))
        patient = patients.get(patient_id, {})
        linked.add(patient_id)
        stage = _consultation_stage(record)
        assignee = record.get("assigned_to")
        rows.append(
            {
                "Patient": _text(record.get("name"), _patient_name(patient)),
                "MRN": _text(record.get("mrn") or patient.get("MRN"), "Not recorded"),
                "Patient ID": patient_id or "Local only",
                "Encounter": _text(record.get("ehr_encounter_id") or record.get("id")),
                "Workflow": "IPD journey"
                if _text(record.get("ehr_encounter_type")).lower().startswith("inp")
                else "Consultation",
                "Stage": stage,
                "Owner": (user_names or {}).get(assignee, assignee) if assignee else "Unassigned",
                "Updated": _parse_datetime(record.get("approval_date") or record.get("date"), record.get("time")),
                "Pending item": PENDING_ITEMS[stage],
                "Priority": "Needs attention"
                if (not assignee and stage != "Billed") or record.get("rerouted_from")
                else ("Routine" if stage == "Billed" else "In progress"),
                "_origin": "consultation",
                "_record": record,
            }
        )
    for patient_id, patient in patients.items():
        if patient_id in linked:
            continue
        encounter = latest_encounters.get(patient_id, {})
        rows.append(
            {
                "Patient": _patient_name(patient),
                "MRN": _text(patient.get("MRN"), "Not recorded"),
                "Patient ID": patient_id,
                "Encounter": _text(encounter.get("Encounter_ID"), "—"),
                "Workflow": _text(encounter.get("Type"), "EHR record"),
                "Stage": "EHR record",
                "Owner": _text(encounter.get("Provider"), "—"),
                "Updated": _parse_datetime(encounter.get("Date")),
                "Pending item": PENDING_ITEMS["EHR record"],
                "Priority": "Routine",
                "_origin": "ehr",
                "_record": None,
            }
        )
    rows.sort(key=lambda row: row["Updated"] or datetime.min, reverse=True)
    return rows


def _filter_rows(rows, query, workflow, stage, window, now):
    matches = []
    for row in rows:
        haystack = " ".join((row["Patient"], row["MRN"], row["Patient ID"], row["Encounter"])).lower()
        if query and query not in haystack:
            continue
        if workflow != "All workflows" and row["Workflow"] != workflow:
            continue
        if stage != "All stages" and row["Stage"] != stage:
            continue
        if window != "All time":
            updated = row["Updated"]
            if updated is None:
                continue
            if window == "Today" and updated.date() != now.date():
                continue
            if window == "Last 7 days" and not timedelta(0) <= now - updated <= timedelta(days=7):
                continue
            if window == "Last 30 days" and not timedelta(0) <= now - updated <= timedelta(days=30):
                continue
        matches.append(row)
    return matches


def _select_row(key):
    st.session_state.admin_selected_row = key


def _row_key(row):
    return f"{row['_origin']}:{row['Patient ID']}:{row['Encounter']}"


def _render_queue(rows, offset=0):
    widths = [2.3, 1.2, 1.3, 1.3, 1.3, 0.9]
    for col, label in zip(st.columns(widths), ("Patient", "MRN", "Workflow", "Stage", "Updated", ""), strict=True):
        col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
    selected = st.session_state.get("admin_selected_row")
    for index, row in enumerate(rows, start=offset):
        key = _row_key(row)
        with st.container(key=f"encrow_{'sel' if key == selected else 'off'}_adm{index}"):
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].markdown(
                f'<div class="nc-patient"><div class="nc-initials">{escape(initials(row["Patient"]))}</div>'
                f"<div><b>{escape(row['Patient'])}</b><small>{escape(row['Patient ID'])} · {escape(row['Encounter'])}</small></div></div>",
                unsafe_allow_html=True,
            )
            cols[1].markdown(f'<div class="nc-cell muted">{escape(row["MRN"])}</div>', unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell">{escape(row["Workflow"])}</div>', unsafe_allow_html=True)
            cols[3].markdown(badge(row["Stage"], STAGE_TONES[row["Stage"]]), unsafe_allow_html=True)
            updated = row["Updated"].strftime("%d %b %Y") if row["Updated"] else "—"
            cols[4].markdown(f'<div class="nc-cell muted">{updated}</div>', unsafe_allow_html=True)
            cols[5].button("View", key=f"admin_pick_{index}", type="tertiary", on_click=_select_row, args=(key,))


def _render_detail(row):
    with st.container(border=True):
        tone = "red" if row["Priority"] == "Needs attention" else "green" if row["Priority"] == "Routine" else "amber"
        card_title("Selected patient", "Active workflow details", trailing=badge(row["Priority"], tone))
        st.markdown(
            f'<div class="nc-patient" style="margin-bottom:.6rem"><div class="nc-initials lg">{escape(initials(row["Patient"]))}</div>'
            f'<div><b style="font-size:.95rem">{escape(row["Patient"])}</b><small>{escape(row["MRN"])} · {escape(row["Workflow"])}</small></div></div>',
            unsafe_allow_html=True,
        )
        render_tiles(
            (
                ("Stage", row["Stage"]),
                ("Encounter", row["Encounter"]),
                ("Owner", row["Owner"]),
                ("Updated", row["Updated"].strftime("%d %b %Y %H:%M") if row["Updated"] else "—"),
            )
        )
        st.markdown(
            f'<div class="nc-meta" style="margin:.6rem 0">Next: {escape(row["Pending item"])}</div>', unsafe_allow_html=True
        )
        record = row["_record"]
        if record is None:
            st.caption("This patient has no consultation in the workspace yet.")
            return
        destination = {"Coding": "codes", "Billed": "billing"}.get(row["Stage"], "review")
        st.button(
            "Open workflow →",
            key="admin_open_workflow",
            type="primary",
            width="stretch",
            on_click=open_record,
            args=(record, destination),
        )


def show_admin_dashboard():
    render_page("admin_dashboard")
    page_title("Oversight", "Oversight Dashboard", "Every patient across documentation, coding and billing")
    ehr_payload = fetch_ehr_export()
    if ehr_payload is None:
        render_banner(
            "EHR backend offline — showing workspace consultations only",
            "Start it with: python -m app",
            tone="warn",
            icon="&#9888;",
        )
    user_names = {user["username"]: user["name"] for user in ACCESS["store"]["users"]}
    rows = build_admin_workflow_rows(st.session_state.records, ehr_payload or {}, user_names)

    search_col, workflow_col, stage_col, window_col = st.columns([3.6, 1.8, 1.8, 1.8], vertical_alignment="bottom")
    query = (
        search_col.text_input("Search", placeholder="Patient name / MRN / encounter ID", key="admin_patient_search")
        .strip()
        .lower()
    )
    workflow = workflow_col.selectbox(
        "Workflow", ["All workflows", *sorted({row["Workflow"] for row in rows})], key="admin_workflow_filter"
    )
    stage = stage_col.selectbox(
        "Stage", ["All stages", *[s for s in STAGES if any(row["Stage"] == s for row in rows)]], key="admin_stage_filter"
    )
    window = window_col.selectbox("Updated", DATE_WINDOWS, key="admin_date_filter")
    filtered = _filter_rows(rows, query, workflow, stage, window, datetime.now())

    consultations = [row for row in filtered if row["_origin"] == "consultation"]
    for column, kpi in zip(
        st.columns(4),
        (
            ("Patients", len(filtered), "lav", f"{len(consultations)} with a consultation"),
            ("In progress", sum(row["Priority"] == "In progress" for row in filtered), "sky", "Moving through the workflow"),
            (
                "Needs attention",
                sum(row["Priority"] == "Needs attention" for row in filtered),
                "rose",
                "Unassigned or rerouted work",
            ),
            ("Billed", sum(row["Stage"] == "Billed" for row in filtered), "mint", "Claim drafts sent to RCM"),
        ),
        strict=True,
    ):
        with column:
            render_kpi_card(*kpi)
    st.markdown(
        '<div class="nc-badge-row">'
        + "".join(badge(f"{name} · {sum(row['Stage'] == name for row in filtered)}", STAGE_TONES[name]) for name in STAGES)
        + "</div>",
        unsafe_allow_html=True,
    )

    list_col, detail_col = st.columns([6.8, 3.2], gap="medium")
    with list_col:
        with st.container(border=True):
            card_title(
                "All patients",
                "Select a patient to inspect the active workflow",
                trailing=badge(f"{len(filtered)} shown", "violet"),
            )
            if filtered:
                pages = max(1, -(-len(filtered) // QUEUE_PAGE_SIZE))
                page = min(max(st.session_state.get("admin_queue_page", 1), 1), pages)
                start = (page - 1) * QUEUE_PAGE_SIZE
                _render_queue(filtered[start : start + QUEUE_PAGE_SIZE], offset=start)
                if pages > 1:
                    prev_col, info_col, next_col = st.columns([1, 2, 1], vertical_alignment="center")
                    prev_col.button(
                        "Previous",
                        key="admin_queue_prev",
                        disabled=page == 1,
                        width="stretch",
                        on_click=st.session_state.update,
                        kwargs={"admin_queue_page": page - 1},
                    )
                    info_col.markdown(
                        f'<div class="pagination-label">Page {page} of {pages} · {len(filtered)} patients</div>',
                        unsafe_allow_html=True,
                    )
                    next_col.button(
                        "Next",
                        key="admin_queue_next",
                        disabled=page == pages,
                        width="stretch",
                        on_click=st.session_state.update,
                        kwargs={"admin_queue_page": page + 1},
                    )
            else:
                st.info("No patients match the selected filters.")
    with detail_col:
        selected = next((row for row in filtered if _row_key(row) == st.session_state.get("admin_selected_row")), None)
        if selected is None and filtered:
            selected = filtered[0]
            st.session_state.admin_selected_row = _row_key(selected)
        if selected:
            _render_detail(selected)
        else:
            with st.container(border=True):
                card_title("Selected patient")
                st.caption("Select a patient after changing the filters.")
