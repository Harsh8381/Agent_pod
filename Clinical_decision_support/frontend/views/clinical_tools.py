"""Medication & RTBC, Prior Authorization and Knowledge Base pages."""

import re
from html import escape

import streamlit as st

from frontend.api_client import HealthcareApiClient
from frontend.clinical_text import guideline_passages
from frontend.ehr_chart import chart_rows, format_date, is_active_row, medication_safety_checks, primary_coverage
from frontend.ui.components import badge, card_title, page_title, render_banner, render_checklist, render_html_table, render_tiles
from frontend.ui.data import BACKEND_ERRORS, fetch_ehr_patients, fetch_patient_chart, record_patient_id
from frontend.ui.shell import render_page


def _select_ehr_patient(key):
    """Pick an EHR patient, defaulting to the current consultation's patient."""
    patients = fetch_ehr_patients()
    if patients is None:
        render_banner("EHR backend unavailable", "Start the FastAPI backend with: python -m app", tone="warn", icon="&#9888;")
        return None, {}
    options = {
        f"{p.get('display_name') or p.get('Legal_Name')} · {p.get('patient_id')}": str(p.get("patient_id")) for p in patients
    }
    labels = list(options)
    current_id = record_patient_id(st.session_state.current_record)
    default = next((index for index, value in enumerate(options.values()) if value == current_id), 0)
    if not labels:
        st.info("No EHR patients are available.")
        return None, {}
    label = st.selectbox("Patient", labels, index=default, key=key)
    patient_id = options[label]
    return patient_id, fetch_patient_chart(patient_id)


def show_medications():
    render_page("medications")
    page_title(
        "MEDICATION & RTBC",
        "Medication Review & Real-Time Benefit Check",
        "Active medications, allergy cross-checks and coverage context",
    )
    patient_id, chart = _select_ehr_patient("meds_patient")
    if not patient_id or not chart:
        return
    record = st.session_state.current_record if record_patient_id(st.session_state.current_record) == patient_id else None
    plan_text = f"{(record or {}).get('recommendations', '')}\n{(record or {}).get('summary', '')}"
    left, right = st.columns([6.4, 3.6], gap="medium")
    with left:
        with st.container(border=True):
            medications = chart_rows(chart, "Medications")
            card_title("Medication List", f"{sum(is_active_row(row) for row in medications)} active · from EHR MedicationRequest")
            if medications:
                render_html_table(
                    ["Medication", "Dose", "Route", "Frequency", "Indication", "Prescriber", "Status"],
                    [
                        [
                            escape(str(row.get("Medication", ""))),
                            escape(str(row.get("Dose", ""))),
                            escape(str(row.get("Route", ""))),
                            escape(str(row.get("Frequency", ""))),
                            escape(str(row.get("Indication_ICD_10", ""))),
                            escape(str(row.get("Prescriber", ""))),
                            badge(row.get("Status") or "—", "green" if is_active_row(row) else "gray"),
                        ]
                        for row in medications
                    ],
                )
            else:
                st.caption("No medications recorded.")
        with st.container(border=True):
            allergies = chart_rows(chart, "Allergies")
            card_title("Allergies & Intolerances", f"{len(allergies)} recorded")
            if allergies:
                render_html_table(
                    ["Substance", "Category", "Reaction", "Severity", "Status"],
                    [
                        [
                            escape(str(row.get("Substance", ""))),
                            escape(str(row.get("Category", ""))),
                            escape(str(row.get("Reaction", ""))),
                            escape(str(row.get("Severity", ""))),
                            badge(row.get("Clinical_Status") or "—", "red" if is_active_row(row) else "gray"),
                        ]
                        for row in allergies
                    ],
                )
            else:
                st.caption("No known drug allergies.")
    with right:
        with st.container(border=True):
            card_title("Medication Safety", "Deterministic checks")
            render_checklist(medication_safety_checks(chart, plan_text), "No checks available.")
        with st.container(border=True):
            coverage = primary_coverage(chart)
            card_title("Pharmacy Benefit", coverage.get("Payer_Name") or "No coverage on file")
            if coverage:
                render_tiles(
                    [
                        ("Plan", coverage.get("Plan_Type") or "—"),
                        ("Member ID", coverage.get("Member_ID") or "—"),
                        ("Copay", f"${coverage.get('Copay_USD')}" if coverage.get("Copay_USD") is not None else "—"),
                        (
                            "Deductible",
                            f"${coverage.get('Annual_Deductible_USD'):,}"
                            if isinstance(coverage.get("Annual_Deductible_USD"), (int, float))
                            else "—",
                        ),
                    ]
                )
            render_banner(
                "Real-time benefit check feed not connected",
                "Formulary tier, prior-auth flags and alternatives require a payer RTBC integration; plan copay is shown from the EHR coverage record.",
                icon="&#9432;",
            )


PRIOR_AUTH_DEMO_RULES = (
    (70450, 70498, "CT head / neck"),
    (70540, 70559, "MRI head / neck"),
    (71250, 71275, "CT chest"),
    (72125, 72159, "CT / MRI spine"),
    (73200, 73725, "CT / MRI extremity"),
    (74150, 74183, "CT / MRI abdomen"),
    (78459, 78816, "Nuclear medicine / PET"),
    (95810, 95811, "Polysomnography"),
)


def prior_auth_rule(code):
    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) != 5:
        return None
    value = int(digits)
    return next((label for low, high, label in PRIOR_AUTH_DEMO_RULES if low <= value <= high), None)


def show_prior_auth():
    render_page("prior_auth")
    page_title(
        "PRIOR AUTHORIZATION",
        "Prior Authorization Queue",
        "Orders screened against a demo prior-auth rule set (advanced imaging, nuclear medicine, sleep studies)",
    )
    patient_id, chart = _select_ehr_patient("pa_patient")
    if not patient_id or not chart:
        return
    coverage = primary_coverage(chart)
    orders = [
        {
            "source": "EHR order",
            "date": row.get("Order_Date"),
            "code": row.get("CPT_HCPCS"),
            "description": row.get("Order_Description"),
            "status": row.get("Status"),
        }
        for row in chart_rows(chart, "Orders_Procedures")
    ]
    record = st.session_state.current_record if record_patient_id(st.session_state.current_record) == patient_id else None
    for item in ((record or {}).get("code_results") or {}).get("cpt", []):
        orders.append(
            {
                "source": "Current encounter",
                "date": (record or {}).get("date"),
                "code": item.get("CPT/HCPCS Code"),
                "description": item.get("Matched Procedure/Service"),
                "status": "Coded",
            }
        )
    rows, flagged = [], 0
    for order in orders:
        rule = prior_auth_rule(order["code"])
        flagged += bool(rule)
        rows.append(
            [
                escape(order["source"]),
                escape(format_date(order["date"])),
                f"<code>{escape(str(order['code'] or ''))}</code>",
                escape(str(order["description"] or "")),
                escape(str(order["status"] or "")),
                badge(f"PA likely · {rule}", "amber") if rule else badge("No PA expected", "green"),
            ]
        )
    render_tiles(
        [
            ("Payer", coverage.get("Payer_Name") or "—"),
            ("Plan", coverage.get("Plan_Type") or "—"),
            ("Orders screened", len(orders)),
            ("PA likely", flagged),
        ]
    )
    with st.container(border=True):
        card_title(
            "Orders & Services", "Rule set is a demo screen, not a payer policy — confirm with the payer before scheduling"
        )
        if rows:
            render_html_table(["Source", "Date", "CPT", "Description", "Status", "Prior auth"], rows)
        else:
            st.caption("No orders on file for this patient.")


def show_knowledge_base():
    render_page("knowledge_base")
    page_title(
        "KNOWLEDGE BASE", "Clinical Guideline Search", "Search the local guideline knowledge base used by CDS (TF-IDF + ChromaDB)"
    )
    with st.container(border=True):
        query_col, k_col, go_col = st.columns([6.5, 1.5, 2], vertical_alignment="bottom")
        query = query_col.text_input("Query", placeholder="e.g. community-acquired pneumonia severity assessment", key="kb_query")
        top_k = k_col.number_input("Results", min_value=1, max_value=20, value=5, key="kb_top_k")
        if go_col.button("Search", type="primary", width="stretch", disabled=not query.strip(), key="kb_search"):
            try:
                with st.spinner("Searching the knowledge base..."):
                    st.session_state.kb_results = HealthcareApiClient().retrieve_knowledge(query.strip(), int(top_k))
            except BACKEND_ERRORS as error:
                st.error(f"Knowledge-base search failed: {error}")
    result = st.session_state.kb_results
    if result:
        passages = guideline_passages(result.get("context"))
        st.markdown(
            f'<div class="nc-meta">{len(passages)} passage(s) for “{escape(result.get("query", ""))}”</div>',
            unsafe_allow_html=True,
        )
        if not passages:
            st.info(result.get("context") or "No results.")
        for passage in passages:
            with st.container(border=True):
                st.markdown(
                    f'<div class="nc-rec-source" style="margin:0 0 .3rem">§ {escape(passage["source"])}</div><div class="nc-rec-body" style="color:var(--text)">{escape(passage["text"])}</div>',
                    unsafe_allow_html=True,
                )
