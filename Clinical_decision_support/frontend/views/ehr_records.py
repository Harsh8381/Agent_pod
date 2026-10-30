"""EHR Records: browse and maintain the synthetic EHR through the FastAPI backend."""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from frontend.api_client import HealthcareApiClient
from frontend.settings import BILLING_NOTICE, EHR_DATA_FILE
from frontend.ui.components import badge, card_title, page_title, render_banner, render_tiles
from frontend.ui.data import BACKEND_ERRORS, backend_error_message, fetch_ehr_patients, fetch_patient_chart, refresh_ehr_cache
from frontend.ui.shell import render_page
from frontend.ui.state import can, log_action

PATIENT_FIELDS = (
    ("First_Name", "First name"),
    ("Last_Name", "Last name"),
    ("Legal_Name", "Legal name"),
    ("MRN", "MRN"),
    ("City", "City"),
    ("State", "State"),
    ("Email", "Email"),
    ("Mobile", "Mobile"),
)


@st.cache_data(show_spinner=False)
def load_ehr_demo_workbook(file_path):
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"EHR demo workbook was not found: {path}")
    workbook = pd.ExcelFile(path, engine="openpyxl")
    loaded = {}
    for sheet_name in workbook.sheet_names:
        try:
            loaded[sheet_name] = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
        except Exception as error:
            loaded[sheet_name] = pd.DataFrame({"error": [f"Could not load sheet: {error}"]})
    return loaded


def _flash(message):
    st.session_state.ehr_flash = message


def _after_write(message, action, detail):
    refresh_ehr_cache()
    log_action(action, detail)
    _flash(message)
    st.rerun()


def _render_chart_tab(patient_id, chart):
    patient = chart.get("patient") or {}
    render_tiles(
        (
            ("Patient ID", patient_id),
            ("MRN", patient.get("MRN") or "—"),
            ("Date of birth", str(patient.get("DOB") or "—").split("T")[0]),
            ("Sex", patient.get("Sex_at_Birth") or "—"),
            ("PCP", patient.get("PCP") or "—"),
            ("City", f"{patient.get('City') or '—'}, {patient.get('State') or ''}".strip(", ")),
        )
    )
    sections = list(chart.get("records") or {})
    if not sections:
        st.info("No related clinical records are available for this patient.")
        return
    counts = chart.get("record_counts") or {}
    section = (
        st.segmented_control(
            "Chart section",
            sections,
            default=sections[0],
            key=f"ehr_chart_sheet_{patient_id}",
            format_func=lambda name: f"{name.replace('_', ' ')} · {counts.get(name, 0)}",
        )
        or sections[0]
    )
    st.dataframe(pd.DataFrame(chart["records"].get(section, [])), width="stretch", hide_index=True)


def _render_edit_tab(client, patient_id, chart):
    current = chart.get("patient") or {}
    with st.form(f"ehr_patient_edit_{patient_id}"):
        values = {}
        columns = st.columns(2)
        for index, (field, label) in enumerate(PATIENT_FIELDS):
            values[field] = columns[index % 2].text_input(label, value=str(current.get(field) or ""))
        if st.form_submit_button("Save patient changes", type="primary"):
            values["Legal_Name"] = values["Legal_Name"] or f"{values['First_Name']} {values['Last_Name']}".strip()
            try:
                updated = client.update_patient(patient_id, values)
            except BACKEND_ERRORS as error:
                st.error(f"Patient update failed: {backend_error_message(error)}")
            else:
                _after_write(f"Updated {updated.get('Legal_Name') or patient_id}.", "ehr.patient_update", patient_id)
    with st.container(border=True):
        card_title("Delete patient", "Permanently removes this synthetic patient and every linked record")
        confirm = st.checkbox("I understand this cannot be undone.", key=f"ehr_delete_confirm_{patient_id}")
        if st.button(
            "Delete patient",
            key=f"danger_ehr_delete_{patient_id}",
            type="secondary",
            disabled=not confirm,
            icon=":material/delete:",
        ):
            try:
                client.delete_patient(patient_id)
            except BACKEND_ERRORS as error:
                st.error(f"Patient deletion failed: {backend_error_message(error)}")
            else:
                _after_write("Patient and linked records deleted.", "ehr.patient_delete", patient_id)


def _render_records_tab(client, patient_id, chart):
    chart_records = chart.get("records") or {}
    if not chart_records:
        st.info("This patient has no chart records to edit.")
        return
    sheet_name = st.selectbox(
        "Record sheet", list(chart_records), key=f"ehr_edit_sheet_{patient_id}", format_func=lambda name: name.replace("_", " ")
    )
    edited = st.data_editor(
        pd.DataFrame(chart_records.get(sheet_name, [])),
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        key=f"ehr_records_{patient_id}_{sheet_name}",
    )
    if st.button("Save record changes", key=f"ehr_save_records_{patient_id}_{sheet_name}", type="primary"):
        try:
            client.replace_patient_records(patient_id, sheet_name, edited.to_dict("records"))
        except BACKEND_ERRORS as error:
            st.error(f"Record update failed: {backend_error_message(error)}")
        else:
            _after_write(f"Saved {sheet_name.replace('_', ' ')} records.", "ehr.records_update", f"{patient_id}: {sheet_name}")
    with st.expander("Add a record as JSON"):
        record_json = st.text_area("Record JSON", value="{}", key=f"ehr_new_record_json_{patient_id}_{sheet_name}")
        if st.button("Add record", key=f"ehr_add_record_{patient_id}_{sheet_name}"):
            try:
                payload = json.loads(record_json)
                if not isinstance(payload, dict):
                    raise ValueError("Record JSON must be an object.")
                client.create_patient_record(patient_id, sheet_name, payload)
            except json.JSONDecodeError as error:
                st.error(f"Invalid JSON: {error}")
            except BACKEND_ERRORS as error:
                st.error(f"Record creation failed: {backend_error_message(error)}")
            else:
                _after_write("Record added.", "ehr.record_add", f"{patient_id}: {sheet_name}")


def _render_bills_tab(client, patient_id):
    try:
        bills = client.list_bills(patient_id).get("bills", [])
    except BACKEND_ERRORS as error:
        st.error(f"Bill history could not be loaded: {backend_error_message(error)}")
        return
    if not bills:
        st.info("No saved draft bills for this patient.")
        return
    labels = [f"{item.get('Bill_ID') or item.get('bill_id')} · {item.get('Status') or item.get('status')}" for item in bills]
    chosen = st.selectbox(
        "Saved draft bill", range(len(bills)), format_func=lambda index: labels[index], key=f"ehr_bill_history_{patient_id}"
    )
    st.json(bills[chosen])
    st.caption(BILLING_NOTICE)


def _render_create_form(client):
    with st.form("ehr_patient_create", clear_on_submit=True):
        first_col, last_col = st.columns(2)
        first_name = first_col.text_input("First name")
        last_name = last_col.text_input("Last name")
        mrn_col, city_col = st.columns(2)
        mrn = mrn_col.text_input("MRN (optional)")
        city = city_col.text_input("City")
        if st.form_submit_button("Create patient", type="primary"):
            if not first_name.strip() or not last_name.strip():
                st.error("First and last name are required.")
                return
            try:
                created = client.create_patient(
                    {
                        "First_Name": first_name.strip(),
                        "Last_Name": last_name.strip(),
                        "Legal_Name": f"{first_name.strip()} {last_name.strip()}",
                        "MRN": mrn.strip() or None,
                        "City": city.strip() or None,
                    }
                )
            except BACKEND_ERRORS as error:
                st.error(f"Patient creation failed: {backend_error_message(error)}")
            else:
                _after_write(
                    f"Created {created.get('Legal_Name')} · {created.get('Patient_ID')}.",
                    "ehr.patient_create",
                    str(created.get("Patient_ID")),
                )


def _render_offline_workbook():
    render_banner(
        "EHR backend unavailable — read-only workbook view",
        "Start it with: python -m app to edit records.",
        tone="warn",
        icon="&#9888;",
    )
    if not EHR_DATA_FILE.is_file():
        render_banner("The fallback workbook was not found", str(EHR_DATA_FILE), tone="danger", icon="&#9888;")
        return
    workbook = load_ehr_demo_workbook(str(EHR_DATA_FILE))
    sheets = list(workbook)
    if not sheets:
        st.info("No workbook sheets were loaded.")
        return
    with st.container(border=True):
        sheet = st.selectbox("Workbook sheet", sheets, index=sheets.index("Patient_Master") if "Patient_Master" in sheets else 0)
        frame = workbook.get(sheet, pd.DataFrame())
        card_title(sheet.replace("_", " "), f"{len(frame)} row(s) · {EHR_DATA_FILE.name}")
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            st.dataframe(frame, width="stretch", hide_index=True)
        else:
            st.info("This sheet has no tabular rows to display.")


def show_ehr_demo_data():
    render_page("ehr_data")
    page_title("Administration", "EHR Records", "Synthetic patients, chart records and saved bills from the EHR backend")
    flash = st.session_state.pop("ehr_flash", None)
    if flash:
        st.success(flash)
    patients = fetch_ehr_patients()
    if patients is None:
        _render_offline_workbook()
        return

    can_edit = can("EHR Demo Data", "Edit")
    client = HealthcareApiClient()
    options = {
        str(
            item.get("patient_id") or item.get("Patient_ID")
        ): f"{item.get('display_name') or item.get('Legal_Name')} · {item.get('patient_id') or item.get('Patient_ID')}"
        for item in patients
    }
    select_col, status_col = st.columns([3, 2], vertical_alignment="bottom")
    patient_id = select_col.selectbox(
        "Patient",
        list(options),
        format_func=options.get,
        index=None if not options else 0,
        placeholder="Select a patient",
        key="ehr_patient_selection",
    )
    status_col.markdown(
        '<div class="nc-badge-row" style="justify-content:flex-end;padding-bottom:.45rem">'
        + badge(f"EHR connected · {len(patients)} patients", "green")
        + (badge("Read-only role", "gray") if not can_edit else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    chart = fetch_patient_chart(patient_id) if patient_id else {}

    tab_names = ["Patient chart", "Bill history"] + (["Edit patient", "Clinical records", "Create patient"] if can_edit else [])
    tabs = dict(zip(tab_names, st.tabs(tab_names), strict=True))
    with tabs["Patient chart"]:
        if chart.get("patient"):
            _render_chart_tab(patient_id, chart)
        else:
            st.info("Select a patient to view their chart.")
    with tabs["Bill history"]:
        if patient_id:
            _render_bills_tab(client, patient_id)
        else:
            st.info("Select a patient to view saved bills.")
    if not can_edit:
        st.caption("Editing EHR records requires EHR Demo Data · Edit.")
        return
    with tabs["Edit patient"]:
        if chart.get("patient"):
            _render_edit_tab(client, patient_id, chart)
        else:
            st.info("Select a patient to edit or delete.")
    with tabs["Clinical records"]:
        if patient_id:
            _render_records_tab(client, patient_id, chart)
        else:
            st.info("Select a patient to edit their records.")
    with tabs["Create patient"]:
        _render_create_form(client)
