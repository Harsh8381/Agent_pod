"""Compact NuuCare administration pages.

Drop-in replacement for frontend/admin_console.py.
The public function signatures match the existing router:
"""

from datetime import datetime, timedelta
from html import escape
import re

import pandas as pd
import streamlit as st


ADMIN_CSS = r"""
<style>
/* Shared Admin-page defaults: retain breathing room in nested Streamlit blocks. */
[data-testid="stAppViewContainer"] .block-container {
    max-width: 1500px !important;
    padding: 3.55rem 1.25rem 1rem !important;
}
[data-testid="stAppViewContainer"] [data-testid="stVerticalBlock"] { gap: .55rem !important; }
[data-testid="stAppViewContainer"] [data-testid="stHorizontalBlock"] { gap: .65rem !important; }
[data-testid="stAppViewContainer"] [data-testid="stElementContainer"] { margin-bottom: .12rem !important; }
[data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] p {
    margin: 0 0 .3rem !important;
    line-height: 1.35 !important;
}
[data-testid="stAppViewContainer"] div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: .65rem !important;
    box-shadow: 0 1px 3px rgba(15, 23, 42, .05) !important;
}
[data-testid="stAppViewContainer"] div[data-testid="stVerticalBlockBorderWrapper"] > div {
    padding: .72rem .85rem !important;
}
[data-testid="stAppViewContainer"] h1 {
    font-size: 1.45rem !important;
    line-height: 1.15 !important;
    margin: 0 0 .12rem !important;
    padding: 0 !important;
}
[data-testid="stAppViewContainer"] h2 {
    font-size: 1.08rem !important;
    line-height: 1.2 !important;
    margin: 0 0 .22rem !important;
    padding: 0 !important;
}
[data-testid="stAppViewContainer"] h3 {
    font-size: .9rem !important;
    line-height: 1.2 !important;
    margin: 0 0 .18rem !important;
    padding: 0 !important;
}
[data-testid="stAppViewContainer"] .stTextInput label,
[data-testid="stAppViewContainer"] .stSelectbox label,
[data-testid="stAppViewContainer"] .stDateInput label {
    font-size: .66rem !important;
    margin-bottom: .08rem !important;
}
[data-testid="stAppViewContainer"] [data-baseweb="input"],
[data-testid="stAppViewContainer"] [data-baseweb="select"] > div {
    min-height: 2rem !important;
    border-radius: .45rem !important;
}
[data-testid="stAppViewContainer"] .stButton > button {
    min-height: 1.95rem !important;
    padding: .15rem .45rem !important;
    font-size: .72rem !important;
    line-height: 1.15 !important;
}
[data-testid="stAppViewContainer"] [data-testid="stCheckbox"] { min-height: 1.6rem !important; }
[data-testid="stAppViewContainer"] [data-testid="stCheckbox"] label { min-height: 1.6rem !important; padding: 0 !important; }

/* Sidebar density stays compact without collapsing nested navigation spacing. */
[data-testid="stSidebar"] > div:first-child { padding-top: .35rem !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .2rem !important; }
[data-testid="stSidebar"] [data-testid="stElementContainer"] { margin-bottom: .08rem !important; }
[data-testid="stSidebar"] .stButton > button {
    min-height: 1.9rem !important;
    padding: .08rem .45rem !important;
    font-size: .76rem !important;
    white-space: nowrap !important;
}
[data-testid="stSidebar"] details { margin: 0 !important; }
[data-testid="stSidebar"] details summary {
    min-height: 2rem !important;
    padding: .15rem .5rem !important;
}
[data-testid="stSidebar"] details [data-testid="stVerticalBlock"] { gap: .12rem !important; }
[data-testid="stSidebar"] .shell-logo { font-size: 1.35rem !important; line-height: 1.05 !important; }
[data-testid="stSidebar"] .shell-subtitle { margin: .02rem 0 .4rem !important; }
[data-testid="stSidebar"] .shell-section-label { margin: .25rem 0 .04rem !important; }
[data-testid="stSidebar"] .shell-divider { margin: .22rem 0 !important; }
[data-testid="stSidebar"] .nav-active-marker { margin: -.08rem .5rem 0 !important; }

/* Page header and filters. */
.page-kicker { margin: .18rem 0 .25rem !important; line-height: 1.3 !important; }
.admin-after-header-spacer { height: .75rem; min-height: .75rem; }

/* Dashboard KPI cards and patient list. */
.admin-kpi {
    min-height: 4.35rem;
    margin-bottom: .75rem !important;
    padding: .48rem .65rem;
    background: var(--surface, #fff);
    border: 1px solid var(--border, #e2e8f0);
    border-top: 3px solid var(--accent, #148277);
    border-radius: .62rem;
    box-shadow: 0 1px 3px rgba(15, 23, 42, .05);
}
.admin-kpi-label {
    color: var(--muted, #64748b);
    font-size: .62rem;
    font-weight: 800;
    letter-spacing: .02rem;
    text-transform: uppercase;
}
.admin-kpi-value {
    margin-top: .15rem;
    color: var(--text, #17212b);
    font-size: 1.35rem;
    line-height: 1;
    font-weight: 800;
}
.admin-kpi-lower-spacer { height: .75rem; min-height: .75rem; }
.admin-section-title {
    margin: 0 0 .22rem;
    padding-bottom: .12rem;
    color: var(--text, #17212b);
    font-size: .92rem;
    line-height: 1.25;
    font-weight: 780;
}
.admin-section-subtitle {
    margin: .08rem 0 .45rem;
    color: var(--muted, #64748b);
    font-size: .66rem;
    line-height: 1.3;
}
.admin-patient-heading-block { display: block; padding: .15rem .2rem .65rem; }
.admin-patient-heading-block .admin-section-title {
    margin: 0 0 .18rem !important;
    padding-bottom: 0;
    color: var(--text, #17212b);
    font-size: .92rem;
    line-height: 1.25 !important;
    font-weight: 780;
}
.admin-patient-heading-block .admin-section-subtitle {
    margin: 0 !important;
    color: var(--muted, #64748b);
    font-size: .66rem;
    line-height: 1.3 !important;
}
.admin-table-header-row {
    display: grid;
    grid-template-columns: minmax(0, 1.75fr) minmax(0, 1.1fr) minmax(0, 1.3fr) minmax(0, 1.4fr) minmax(0, 1.15fr) minmax(0, 1.15fr);
    align-items: center;
    min-height: 2.15rem;
    margin-bottom: .15rem;
    padding: .2rem .18rem;
    color: var(--muted, #64748b);
    border-bottom: 1px solid var(--border-strong, #cbd5e1);
    font-size: .58rem;
    font-weight: 800;
    line-height: 1.2;
    text-transform: uppercase;
}
.admin-table-header-row > div { min-width: 0; white-space: nowrap; }
.admin-table-header-row > div:last-child { padding-left: .15rem; }
.admin-cell {
    display: flex;
    min-height: 1.72rem;
    align-items: center;
    overflow-wrap: anywhere;
    padding: .08rem .18rem;
    color: var(--text, #17212b);
    font-size: .68rem;
    line-height: 1.2;
}
.admin-cell.two-line { min-height: 2.25rem; align-items: flex-start; justify-content: center; flex-direction: column; }
.admin-cell small { color: var(--muted, #64748b); font-size: .64rem; }
.admin-row-rule { height: 1px; margin: 0; background: var(--border, #e2e8f0); }
[class*="st-key-admin_pick_"] button { white-space: nowrap !important; padding-right: .25rem !important; padding-left: .25rem !important; }
.admin-stage {
    display: inline-flex;
    justify-content: center;
    min-width: 5.4rem;
    padding: .18rem .3rem;
    border-radius: .48rem;
    font-size: .6rem;
    font-weight: 780;
}
.stage-review { color: #b45309; background: #fff1dc; }
.stage-completed { color: #15803d; background: #e7f7ec; }
.stage-coding { color: #6d28d9; background: #ede9fe; }
.stage-rcm { color: #c2410c; background: #ffedd5; }
.stage-scribe { color: #1d4ed8; background: #dbeafe; }
.stage-pending { color: #475569; background: #e2e8f0; }

/* Selected-patient detail layout. */
.selected-patient-content { display: block; min-height: 0; padding: .2rem .25rem .15rem; }
.selected-patient-title { margin: 0; color: var(--text, #17212b); font-size: 1.02rem; line-height: 1.25; font-weight: 800; }
.selected-patient-subtitle { margin: .05rem 0 .48rem; color: var(--muted, #64748b); font-size: .7rem; line-height: 1.25; }
.selected-patient-name { margin: 0 0 .08rem; color: var(--text, #17212b); font-size: .98rem; line-height: 1.25; font-weight: 780; }
.selected-patient-meta { margin: 0 0 .65rem; color: var(--muted, #64748b); font-size: .74rem; line-height: 1.3; }
.selected-detail-list { display: flex; flex-direction: column; gap: .42rem !important; margin: 0 0 .75rem !important; }
.selected-detail-row {
    display: grid;
    grid-template-columns: 6.6rem minmax(0, 1fr);
    column-gap: .42rem;
    align-items: start;
    min-height: 1rem;
    font-size: .72rem;
    line-height: 1.35;
}
.selected-detail-label { color: var(--text, #17212b); font-weight: 760; white-space: nowrap; }
.selected-detail-value { min-width: 0; color: var(--text, #17212b); overflow-wrap: anywhere; }
.admin-priority { color: #c45b00; font-weight: 750; }
.selected-before-button-spacer { display: block; height: .75rem !important; min-height: .75rem !important; }
[data-testid="stVerticalBlockBorderWrapper"]:has(.selected-patient-content) .stButton > button {
    min-height: 2.45rem !important;
    height: 2.45rem !important;
    font-size: .76rem !important;
    white-space: nowrap !important;
}

/* Super Admin panels and controls inherit safe global gaps. */
.role-note { color: var(--muted, #64748b); font-size: .7rem; }

@media (max-width: 950px) {
    [data-testid="stAppViewContainer"] .block-container { padding: 3.45rem .8rem .8rem !important; }
    .selected-detail-row { grid-template-columns: 1fr; row-gap: .04rem; }
    .selected-detail-label { white-space: normal; }
}

</style>
"""


def _inject_admin_css():
    st.markdown(ADMIN_CSS, unsafe_allow_html=True)


def _safe(value, fallback="Not recorded"):
    text = str(value or "").strip()
    return text if text else fallback


def _parse_date(value):
    text = str(value or "").strip()
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%b %d, %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    parsed = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def _record_datetime(record):
    date_value = record.get("approval_date") or record.get("date") or ""
    time_value = record.get("time") or "00:00"
    for pattern in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(f"{date_value} {time_value}", pattern)
        except ValueError:
            pass
    parsed = _parse_date(date_value)
    return parsed or datetime.min


def _stage_for_record(record):
    explicit = record.get("current_stage") or record.get("stage")
    if explicit:
        return str(explicit)
    status = str(record.get("status") or "").strip().casefold()
    if status in {"approved", "completed", "finalized", "signed"}:
        return "Completed"
    if record.get("code_results") or record.get("cpt_results_history"):
        return "Coding"
    if record.get("summary") or record.get("patient_summary"):
        return "Clinical Review"
    if record.get("transcript"):
        return "Ambient Scribe"
    return "Pending"


def _workflow_for_record(record):
    explicit = record.get("workflow")
    if explicit:
        return str(explicit)
    encounter_type = str(record.get("ehr_encounter_type") or record.get("encounter_type") or "").casefold()
    return "IPD Journey" if encounter_type in {"ipd", "inpatient"} else "Consultation"


def _stage_class(stage):
    value = stage.casefold()
    if "complete" in value or "approved" in value:
        return "stage-completed"
    if "coding" in value or "code" in value:
        return "stage-coding"
    if "rcm" in value or "billing" in value:
        return "stage-rcm"
    if "scribe" in value or "ambient" in value:
        return "stage-scribe"
    if "review" in value:
        return "stage-review"
    return "stage-pending"


def _patient_row(record):
    updated_dt = _record_datetime(record)
    stage = _stage_for_record(record)
    priority = record.get("priority")
    if not priority:
        priority = "Routine" if stage == "Completed" else "Needs attention"
    pending_item = record.get("pending_item")
    if not pending_item:
        pending_item = {
            "Completed": "No pending item",
            "Coding": "Coding validation pending",
            "RCM Review": "Billing review pending",
            "Ambient Scribe": "Clinical note generation pending",
            "Clinical Review": "Clinical note awaiting review",
        }.get(stage, "Workflow action pending")
    return {
        "record": record,
        "name": _safe(record.get("name") or record.get("patient_name"), "Unknown patient"),
        "mrn": _safe(record.get("mrn") or record.get("MRN")),
        "patient_id": _safe(record.get("ehr_patient_id") or record.get("patient_id") or record.get("id")),
        "encounter_id": _safe(record.get("ehr_encounter_id") or record.get("encounter_id") or record.get("id")),
        "workflow": _workflow_for_record(record),
        "stage": stage,
        "updated_dt": updated_dt,
        "updated": updated_dt.strftime("%b %d, %Y %H:%M") if updated_dt != datetime.min else "Not recorded",
        "owner": _safe(record.get("stage_owner") or record.get("doctor"), "Unassigned"),
        "entered_stage": _safe(record.get("entered_stage") or record.get("updated_at") or (
            updated_dt.strftime("%b %d, %Y %H:%M") if updated_dt != datetime.min else ""
        )),
        "pending_item": pending_item,
        "priority": priority,
    }


def _load_ehr_rows(load_ehr_demo_workbook, ehr_data_file):
    """Read a small amount of EHR identity data without failing the Admin page."""
    try:
        workbook = load_ehr_demo_workbook(str(ehr_data_file))
    except Exception:
        return []
    patient_sheet = None
    for candidate in ("Patient_Master", "Patients", "Patient Master"):
        if candidate in workbook and isinstance(workbook[candidate], pd.DataFrame):
            patient_sheet = workbook[candidate]
            break
    if patient_sheet is None or patient_sheet.empty:
        return []
    rows = []
    for _, raw in patient_sheet.iterrows():
        patient_id = raw.get("Patient_ID", raw.get("Patient ID", ""))
        name = raw.get("Legal_Name", raw.get("Patient_Name", raw.get("Patient Name", "")))
        if not str(name or "").strip():
            first = raw.get("First_Name", raw.get("First Name", ""))
            last = raw.get("Last_Name", raw.get("Last Name", ""))
            name = f"{first} {last}".strip()
        rows.append({
            "id": str(patient_id or ""),
            "patient_id": str(patient_id or ""),
            "name": str(name or "Unknown patient"),
            "mrn": str(raw.get("MRN", "") or ""),
            "status": "Pending",
            "workflow": "Consultation",
            "current_stage": "Pending",
            "date": "",
            "time": "",
        })
    return rows


def _merge_records(records, ehr_rows):
    output = list(records or [])
    known = {
        str(item.get("ehr_patient_id") or item.get("patient_id") or item.get("id") or "").strip()
        for item in output
    }
    for row in ehr_rows:
        identity = str(row.get("patient_id") or row.get("id") or "").strip()
        if identity and identity not in known:
            output.append(row)
            known.add(identity)
    return output


def _kpi(label, value, accent):
    st.markdown(
        f'<div class="admin-kpi" style="--accent:{accent}">'
        f'<div class="admin-kpi-label">{escape(label)}</div>'
        f'<div class="admin-kpi-value">{value}</div></div>',
        unsafe_allow_html=True,
    )


def _render_patient_table(rows, open_record):
    header_labels = ("Patient", "MRN", "Workflow", "Current stage", "Updated", "Action")
    header_html = "".join(f"<div>{label}</div>" for label in header_labels)
    st.markdown(f'<div class="admin-table-header-row">{header_html}</div>', unsafe_allow_html=True)

    for index, item in enumerate(rows):
        columns = st.columns([1.75, 1.1, 1.3, 1.4, 1.15, 1.15], gap="small", vertical_alignment="center")
        columns[0].markdown(
            f'<div class="admin-cell two-line"><b>{escape(item["name"])}</b>'
            f'<small>{escape(item["patient_id"])}</small></div>',
            unsafe_allow_html=True,
        )
        columns[1].markdown(f'<div class="admin-cell">{escape(item["mrn"])}</div>', unsafe_allow_html=True)
        columns[2].markdown(f'<div class="admin-cell">{escape(item["workflow"])}</div>', unsafe_allow_html=True)
        columns[3].markdown(
            f'<div class="admin-cell"><span class="admin-stage {_stage_class(item["stage"])}">'
            f'{escape(item["stage"])}</span></div>',
            unsafe_allow_html=True,
        )
        columns[4].markdown(f'<div class="admin-cell">{escape(item["updated"])}</div>', unsafe_allow_html=True)
        if columns[5].button("Open", key=f'admin_pick_{index}_{item["encounter_id"]}', use_container_width=True):
            st.session_state.admin_selected_encounter = item["encounter_id"]
            st.rerun()
        st.markdown('<div class="admin-row-rule"></div>', unsafe_allow_html=True)


def _render_selected_patient(item, open_record):
    """Render details as one HTML block so Streamlit cannot collapse adjacent rows."""
    priority_class = (
        "admin-priority"
        if str(item["priority"]).casefold() != "routine"
        else ""
    )
    details = (
        ("Current stage", item["stage"], ""),
        ("Encounter ID", item["encounter_id"], ""),
        ("Stage owner", item["owner"], ""),
        ("Entered stage", item["entered_stage"], ""),
        ("Pending item", item["pending_item"], ""),
        ("Priority", item["priority"], priority_class),
    )
    rows = "".join(
        f'<div class="selected-detail-row">'
        f'<span class="selected-detail-label">{escape(label)}:</span>'
        f'<span class="selected-detail-value {value_class}">{escape(str(value))}</span>'
        f'</div>'
        for label, value, value_class in details
    )
    st.markdown(
        f'<div class="selected-patient-content">'
        f'<div class="selected-patient-title">Selected patient</div>'
        f'<div class="selected-patient-subtitle">Active workflow details</div>'
        f'<div class="selected-patient-name">{escape(item["name"])}</div>'
        f'<div class="selected-patient-meta">{escape(item["mrn"])} · {escape(item["workflow"])}</div>'
        f'<div class="selected-detail-list">{rows}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="selected-before-button-spacer"></div>', unsafe_allow_html=True)
    if st.button(
        "Open relevant workflow",
        key="admin_open_workflow",
        type="primary",
        use_container_width=True,
    ):
        destination = "codes" if "cod" in item["stage"].casefold() else "review"
        open_record(item["record"], destination=destination)
        st.rerun()


def show_admin_dashboard(
    records,
    load_ehr_demo_workbook,
    ehr_data_file,
    open_record,
    render_sidebar,
    render_header,
):
    render_sidebar("admin_dashboard")
    _inject_admin_css()
    render_header("Admin Dashboard", "All-patient overview with workflow-stage drill-down", show_new=False)
    st.markdown('<div class="admin-after-header-spacer"></div>', unsafe_allow_html=True)

    # Prefer consultation records and enrich with EHR-only patients when available.
    ehr_rows = _load_ehr_rows(load_ehr_demo_workbook, ehr_data_file)
    merged = _merge_records(records, ehr_rows)
    patients = [_patient_row(record) for record in merged]

    filter_columns = st.columns([3.8, 1.8, 1.8, 1.8], gap="small")
    search = filter_columns[0].text_input(
        "Search patient, MRN or encounter",
        placeholder="Patient name / MRN / encounter ID",
        key="admin_patient_search",
    ).strip().casefold()
    workflows = ["All workflows"] + sorted({item["workflow"] for item in patients})
    stages = ["All stages"] + sorted({item["stage"] for item in patients})
    workflow_filter = filter_columns[1].selectbox("Workflow", workflows, key="admin_workflow_filter")
    stage_filter = filter_columns[2].selectbox("Stage", stages, key="admin_stage_filter")
    date_filter = filter_columns[3].selectbox(
        "Date", ("Today", "Last 7 days", "Last 30 days", "All time"), index=2, key="admin_date_filter"
    )

    now = datetime.now()
    filtered = []
    for item in patients:
        haystack = " ".join((item["name"], item["mrn"], item["patient_id"], item["encounter_id"])).casefold()
        if search and search not in haystack:
            continue
        if workflow_filter != "All workflows" and item["workflow"] != workflow_filter:
            continue
        if stage_filter != "All stages" and item["stage"] != stage_filter:
            continue
        if item["updated_dt"] != datetime.min:
            elapsed = now - item["updated_dt"]
            if date_filter == "Today" and item["updated_dt"].date() != now.date():
                continue
            if date_filter == "Last 7 days" and not timedelta(0) <= elapsed <= timedelta(days=7):
                continue
            if date_filter == "Last 30 days" and not timedelta(0) <= elapsed <= timedelta(days=30):
                continue
        elif date_filter != "All time":
            continue
        filtered.append(item)

    filtered.sort(key=lambda item: item["updated_dt"], reverse=True)
    completed = sum(item["stage"].casefold() in {"completed", "approved"} for item in filtered)
    attention = sum(
        str(item["priority"]).casefold() in {"needs attention", "high", "urgent"}
        for item in filtered
    )
    in_progress = max(0, len(filtered) - completed - attention)
    cards = st.columns(4, gap="small")
    for column, payload in zip(cards, (
        ("Total patients", len(filtered), "#148277"),
        ("In progress", in_progress, "#2563eb"),
        ("Needs attention", attention, "#d97706"),
        ("Completed", completed, "#15803d"),
    )):
        with column:
            _kpi(*payload)
    st.markdown('<div class="admin-kpi-lower-spacer"></div>', unsafe_allow_html=True)

    list_col, detail_col = st.columns([6.8, 3.2], gap="medium")
    with list_col:
        with st.container(border=True):
            st.markdown(
                """
                <div class="admin-patient-heading-block">
                    <div class="admin-section-title">All patients</div>
                    <div class="admin-section-subtitle">
                        Select a patient to inspect the active workflow.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if filtered:
                _render_patient_table(filtered, open_record)
            else:
                st.info("No patients match the selected filters.")

    selected_id = st.session_state.get("admin_selected_encounter")
    selected = next((item for item in filtered if item["encounter_id"] == selected_id), None)
    if selected is None and filtered:
        selected = filtered[0]
        st.session_state.admin_selected_encounter = selected["encounter_id"]
    with detail_col:
        with st.container(border=True):
            if selected:
                _render_selected_patient(selected, open_record)
            else:
                st.markdown('<div class="admin-section-title">Selected patient</div>', unsafe_allow_html=True)
                st.caption("Select a patient after changing the filters.")


# -----------------------------------------------------------------------------
# Super Admin
# -----------------------------------------------------------------------------
ROLE_NAMES = (
    "Coding Specialist",
    "RCM Analyst",
    "Clinical Reviewer",
    "Operations Admin",
    "Super Admin",
)
MODULES = (
    "Clinical Overview",
    "Clinical Review",
    "Codes",
    "RCM Workspace",
    "IPD Billing",
    "Admin Dashboard",
    "Super Admin",
    "EHR Demo Data",
)
ACTIONS = ("View", "Edit", "Approve", "Admin")


def _default_permissions():
    empty = {module: {action: False for action in ACTIONS} for module in MODULES}
    output = {role: {module: values.copy() for module, values in empty.items()} for role in ROLE_NAMES}

    for module in ("Clinical Overview", "Clinical Review", "Codes", "EHR Demo Data"):
        output["Coding Specialist"][module]["View"] = True
    output["Coding Specialist"]["Codes"].update({"Edit": True, "Approve": True})

    for module in ("Clinical Overview", "RCM Workspace", "IPD Billing", "EHR Demo Data"):
        output["RCM Analyst"][module]["View"] = True
    output["RCM Analyst"]["RCM Workspace"].update({"Edit": True, "Approve": True})
    output["RCM Analyst"]["IPD Billing"]["Edit"] = True

    for module in ("Clinical Overview", "Clinical Review", "Codes", "EHR Demo Data"):
        output["Clinical Reviewer"][module]["View"] = True
    output["Clinical Reviewer"]["Clinical Review"].update({"Edit": True, "Approve": True})

    for module in MODULES:
        output["Operations Admin"][module]["View"] = True
    for module in ("Clinical Overview", "Clinical Review", "Codes", "RCM Workspace", "IPD Billing", "Admin Dashboard"):
        output["Operations Admin"][module]["Edit"] = True

    for module in MODULES:
        output["Super Admin"][module] = {action: True for action in ACTIONS}
    return output


def _initialize_admin_state():
    if "admin_permissions" not in st.session_state:
        st.session_state.admin_permissions = _default_permissions()
    if "admin_selected_role" not in st.session_state:
        st.session_state.admin_selected_role = ROLE_NAMES[0]
    if "admin_users" not in st.session_state:
        st.session_state.admin_users = []


def _role_button(role):
    selected = st.session_state.admin_selected_role == role
    if st.button(
        role,
        key=f"role_{re.sub(r'[^a-z0-9]+', '_', role.casefold())}",
        type="primary" if selected else "secondary",
        use_container_width=True,
    ):
        st.session_state.admin_selected_role = role
        st.rerun()


def _render_permission_matrix(role):
    """Compact editable permission grid with fixed widths and short rows."""
    permissions = st.session_state.admin_permissions[role]
    frame = pd.DataFrame([
        {
            "Module": module,
            "View": bool(permissions[module]["View"]),
            "Edit": bool(permissions[module]["Edit"]),
            "Approve": bool(permissions[module]["Approve"]),
            "Admin": bool(permissions[module]["Admin"]),
        }
        for module in MODULES
    ])
    edited = st.data_editor(
        frame,
        key=f"permission_editor_{role}",
        hide_index=True,
        width="stretch",
        height=326,
        row_height=32,
        disabled=True if role == "Super Admin" else ["Module"],
        column_config={
            "Module": st.column_config.TextColumn("Module", width=116),
            "View": st.column_config.CheckboxColumn("View", width=58),
            "Edit": st.column_config.CheckboxColumn("Edit", width=58),
            "Approve": st.column_config.CheckboxColumn("Approve", width=58),
            "Admin": st.column_config.CheckboxColumn("Admin", width=58),
        },
    )
    for row in edited.to_dict("records"):
        module = row["Module"]
        for action in ACTIONS:
            permissions[module][action] = True if role == "Super Admin" else bool(row[action])


def show_super_admin(render_sidebar, render_header):
    render_sidebar("super_admin")
    _inject_admin_css()
    _initialize_admin_state()
    render_header("Super Admin", "Role-based access configuration for the NuuCare workspace", show_new=False)
    st.markdown('<div class="super-admin-shell">', unsafe_allow_html=True)

    left, right = st.columns([2.35, 7.65], gap="medium")
    with left:
        with st.container(border=True):
            st.markdown('<div class="admin-section-title">Users &amp; roles</div>', unsafe_allow_html=True)
            st.markdown('<div class="admin-section-subtitle">Search and configure POC access.</div>', unsafe_allow_html=True)
            search = st.text_input("Search users or roles", placeholder="Search user or role", key="admin_role_search").strip().casefold()

            users = st.session_state.admin_users
            matching_users = [user for user in users if search in str(user.get("name", "")).casefold()] if search else users
            if matching_users:
                for user in matching_users[:5]:
                    st.caption(f'{user.get("name", "Unnamed user")} · {user.get("role", "No role")}')
            else:
                st.caption("No POC users have been added yet.")

            st.markdown("#### Configured roles")
            visible_roles = [role for role in ROLE_NAMES if not search or search in role.casefold()]
            for role in visible_roles:
                _role_button(role)

            with st.expander("Add user", expanded=False):
                new_name = st.text_input("User name", key="admin_new_user_name")
                new_role = st.selectbox("Assigned role", ROLE_NAMES, key="admin_new_user_role")
                if st.button("Add user", key="admin_add_user", use_container_width=True):
                    if new_name.strip():
                        st.session_state.admin_users.append({"name": new_name.strip(), "role": new_role})
                        st.success("POC user added.")
                        st.rerun()
                    else:
                        st.warning("Enter a user name.")

    with right:
        with st.container(border=True):
            role = st.session_state.admin_selected_role
            st.markdown(
                f'<div class="admin-section-title">Role permissions: {escape(role)}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="admin-section-subtitle">POC configuration only. Permissions are stored in session state.</div>',
                unsafe_allow_html=True,
            )
            _render_permission_matrix(role)
            save_col, reset_col, _ = st.columns([1.1, 1.1, 5.8], gap="small")
            if save_col.button("Save changes", type="primary", use_container_width=True):
                st.success(f"Permissions saved for {role}.")
            if reset_col.button("Reset role", use_container_width=True, disabled=role == "Super Admin"):
                st.session_state.admin_permissions[role] = _default_permissions()[role]
                for module in MODULES:
                    for action in ACTIONS:
                        widget_key = f"perm_{role}_{module}_{action}".replace(" ", "_").lower()
                        st.session_state.pop(widget_key, None)
                st.rerun()
            st.divider()
            st.markdown(
                '<div class="role-note">Access model · Module → action → data scope</div>',
                unsafe_allow_html=True,
            )
    st.markdown('</div>', unsafe_allow_html=True)
