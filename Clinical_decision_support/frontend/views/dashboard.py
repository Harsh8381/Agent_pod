"""Clinical Overview: KPIs, activity chart and the consultation queue."""

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from frontend.ui.components import card_title, page_header, render_banner, render_kpi_card, render_patient_table
from frontend.ui.data import fetch_ehr_patients
from frontend.ui.shell import render_page
from frontend.ui.state import current_user, start_new_consultation
from frontend.ui.theme import CHART_COLORS, PALETTE

DATE_FILTERS = ("All Time", "Today", "Last 7 Days", "Last 30 Days", "Custom Date Range")
STATUS_FILTERS = ("All Statuses", "Pending", "Approved")


def _record_date(record):
    try:
        return datetime.strptime(record.get("date", ""), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def filter_records(records, query, date_filter, status_filter, custom_range=None, assignee=None, today=None):
    """Return records matching the search text, date window, status and (optionally) assignee."""
    today = today or datetime.now().date()
    matches = []
    for record in records:
        if assignee and record.get("assigned_to") != assignee:
            continue
        searchable = (
            f"{record.get('name', '')} {record.get('id', '')} {record.get('mrn', '')} {record.get('ehr_patient_id', '')}".lower()
        )
        if query and query not in searchable:
            continue
        if status_filter != "All Statuses" and record.get("status") != status_filter:
            continue
        record_date = _record_date(record)
        if record_date is None:
            continue
        age_days = (today - record_date).days
        if date_filter == "Today" and record_date != today:
            continue
        if date_filter == "Last 7 Days" and not 0 <= age_days <= 7:
            continue
        if date_filter == "Last 30 Days" and not 0 <= age_days <= 30:
            continue
        if date_filter == "Custom Date Range":
            if not (
                isinstance(custom_range, tuple) and len(custom_range) == 2 and custom_range[0] <= record_date <= custom_range[1]
            ):
                continue
        matches.append(record)
    return matches


def _activity_chart(records):
    events = []
    for record in records:
        events.append({"Date": record.get("date"), "Metric": "New consults"})
        if record.get("status") == "Approved":
            events.append({"Date": record.get("approval_date", record.get("date")), "Metric": "Signed"})
    if not events:
        return None
    frame = pd.DataFrame(events)
    frame["Date"] = pd.to_datetime(frame["Date"], format="%d/%m/%Y", errors="coerce")
    counts = frame.dropna(subset=["Date"]).groupby(["Date", "Metric"]).size().reset_index(name="Count")
    return (
        alt.Chart(counts)
        .mark_line(point=alt.OverlayMarkDef(size=70, filled=True), strokeWidth=3, interpolate="monotone")
        .encode(
            x=alt.X(
                "yearmonthdate(Date):T",
                title=None,
                axis=alt.Axis(
                    format="%b %d",
                    tickCount={"interval": "day", "step": 1},
                    labelColor=PALETTE["muted"],
                    tickColor=PALETTE["border"],
                    domainColor=PALETTE["border"],
                ),
            ),
            y=alt.Y(
                "Count:Q",
                title="Records",
                axis=alt.Axis(
                    tickMinStep=1,
                    labelColor=PALETTE["muted"],
                    titleColor=PALETTE["muted"],
                    gridColor=PALETTE["border"],
                    domain=False,
                ),
            ),
            color=alt.Color(
                "Metric:N",
                legend=alt.Legend(title=None, orient="top", labelColor=PALETTE["text"]),
                scale=alt.Scale(range=CHART_COLORS[:2]),
            ),
            tooltip=[alt.Tooltip("Date:T", title="Date", format="%b %d, %Y"), "Metric:N", "Count:Q"],
        )
        .properties(height=220)
        .configure_view(strokeWidth=0)
        .configure(font="Inter, 'Segoe UI', system-ui, sans-serif")
    )


def show_dashboard():
    render_page("dashboard")
    actions = page_header(
        "Clinical overview", "Clinical Operations", "Consultation, documentation and coding throughput", action_weight=2.6
    )
    actions.button(
        "New Consultation",
        key="dashboard_new",
        type="primary",
        icon=":material/add:",
        on_click=start_new_consultation,
        width="content",
    )

    search_col, date_col, status_col, mine_col = st.columns([4.4, 2, 2.4, 1.6], vertical_alignment="bottom")
    query = search_col.text_input("Search patient or ID", placeholder="Search name / MRN / consultation ID").strip().lower()
    date_filter = date_col.selectbox("Date", DATE_FILTERS)
    status_filter = status_col.selectbox("Status", STATUS_FILTERS)
    mine_only = mine_col.toggle("My work only", key="dashboard_mine")
    custom_range = None
    if date_filter == "Custom Date Range":
        custom_range = st.date_input("Custom range", value=(datetime.now().date(), datetime.now().date()))
    today = datetime.now().date()
    filtered = filter_records(
        st.session_state.records,
        query,
        date_filter,
        status_filter,
        custom_range,
        assignee=current_user()["username"] if mine_only else None,
        today=today,
    )

    total = len(filtered)
    pending = sum(record.get("status") == "Pending" for record in filtered)
    approved = sum(record.get("status") == "Approved" for record in filtered)
    today_count = sum(_record_date(record) == today for record in filtered)
    coded = sum(
        bool((record.get("code_results") or {}).get("cpt") or (record.get("code_results") or {}).get("icd10"))
        for record in filtered
    )
    for column, kpi in zip(
        st.columns(5),
        (
            ("Total consultations", total, "lav", "All recorded consults"),
            ("Today's volume", today_count, "sky", "Created today"),
            ("Pending sign-off", pending, "butter", "SOAP drafts awaiting review"),
            ("Signed notes", approved, "mint", "Completed documentation"),
            ("Coded encounters", coded, "peach", "ICD-10 / CPT suggestions saved"),
        ),
        strict=True,
    ):
        with column:
            render_kpi_card(*kpi)

    chart_col, overview_col = st.columns([6.5, 3.5], gap="medium")
    with chart_col:
        with st.container(border=True):
            card_title("Consultation activity", "Daily consultation throughput and sign-off")
            chart = _activity_chart(filtered)
            if chart is None:
                st.info("No consultation activity is available for the selected filters.")
            else:
                st.altair_chart(chart, width="stretch")
    with overview_col:
        with st.container(border=True):
            approval_rate = round(approved / total * 100) if total else 0
            card_title("Operating position", "Current documentation backlog")
            st.markdown(
                f'<div class="overview-grid"><div><small>Pending sign-off</small><b class="amber-text">{pending}</b></div>'
                f'<div><small>Sign-off rate</small><b class="green-text">{approval_rate}%</b></div>'
                f'<div><small>Today</small><b class="blue-text">{today_count}</b></div></div>',
                unsafe_allow_html=True,
            )
            patients = fetch_ehr_patients()
            if patients is None:
                render_banner("EHR backend offline", "Start it with: python -m app", tone="warn", icon="&#9888;")
            else:
                render_banner(
                    f"EHR connected · {len(patients)} patients", "FastAPI JSON EHR at 127.0.0.1:8000", tone="ok", icon="&#10003;"
                )
    with st.container(border=True):
        card_title("Patient records", "Clinical documentation queue")
        if filtered:
            render_patient_table(filtered)
        else:
            st.info("No clinical records found.")
