import io
import json
import math
import os
import re
import sys
import uuid
from datetime import datetime
from html import escape, unescape
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import altair as alt
import pandas as pd
import requests
import speech_recognition as sr
import streamlit as st

try:
    from frontend.api_client import HealthcareApiClient
except ModuleNotFoundError:
    from api_client import HealthcareApiClient

try:
    from frontend.chargemaster import build_cpt_bill_rows
except ModuleNotFoundError:
    from chargemaster import build_cpt_bill_rows

try:
    from cpt_coder import (
        classify_medical_item,
        classify_procedure_status,
        extract_performed_procedures,
        get_cpt_codes,
        get_cpt_candidate_sets,
        is_explicitly_performed,
    )
    from icd10_coder import get_icd10_candidate_sets, get_icd10_codes
except ModuleNotFoundError:
    try:
        from cpt_coder import (
            classify_medical_item,
            classify_procedure_status,
            extract_performed_procedures,
            get_cpt_codes,
            get_cpt_candidate_sets,
            is_explicitly_performed,
        )
        from icd10_coder import get_icd10_candidate_sets, get_icd10_codes
    except ModuleNotFoundError:
        classify_medical_item = None
        classify_procedure_status = None
        extract_performed_procedures = None
        get_cpt_candidate_sets = None
        get_cpt_codes = None
        get_icd10_candidate_sets = None
        get_icd10_codes = None
        is_explicitly_performed = None


# ============================================================
# 1. CONFIGURATION
# ============================================================
st.set_page_config(
    page_title="NuuCare | Clinical Intelligence Platform",
    page_icon="NC",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items=None,
)

DB_FILE = PROJECT_ROOT / "clinical_records.json"
EHR_DATA_FILE = PROJECT_ROOT / "Epic_Inspired_USA_50_Patient_Demo.xlsx"
ANALYSIS_VERSION = "2"
CODE_MATCHER_VERSION = "3"


# ============================================================
# 2. THEME AND LAYOUT
# ============================================================

theme_name = st.session_state.get("theme", "Light")
if theme_name == "Dark":
    theme_vars = """
        --bg: #111827;
        --surface: #1f2937;
        --surface-soft: #0f172a;
        --border: #374151;
        --border-strong: #4b5563;
        --text: #f3f4f6;
        --muted: #cbd5e1;
        --primary: #14b8a6;
        --primary-hover: #0f766e;
        --success: #4ade80;
        --success-soft: #14532d;
        --warning: #fbbf24;
        --warning-soft: #78350f;
        --danger: #f87171;
        --danger-soft: #7f1d1d;
        --info: #60a5fa;
        --info-soft: #1e3a8a;
        --purple: #c084fc;
        --sidebar-bg: #0f172a;
        --sidebar-text: #e5e7eb;
        --sidebar-muted: #94a3b8;
        --sidebar-border: #334155;
        --sidebar-hover: #1e293b;
    """
    header_background = "rgba(15, 23, 42, 0.96)"
else:
    theme_vars = """
        --bg: #f4f7f9;
        --surface: #ffffff;
        --surface-soft: #f8fafc;
        --border: #e2e8f0;
        --border-strong: #cbd5e1;
        --text: #17212b;
        --muted: #64748b;
        --primary: #148277;
        --primary-hover: #0f6f66;
        --success: #15803d;
        --success-soft: #dcfce7;
        --warning: #b45309;
        --warning-soft: #fef3c7;
        --danger: #b91c1c;
        --danger-soft: #fee2e2;
        --info: #2563eb;
        --info-soft: #dbeafe;
        --purple: #7c3aed;
        --sidebar-bg: #111827;
        --sidebar-text: #e5e7eb;
        --sidebar-muted: #94a3b8;
        --sidebar-border: #1f2937;
        --sidebar-hover: #164e63;
    """
    header_background = "rgba(244, 247, 249, .96)"

st.markdown(
    f"""
<style>
:root {{
    {theme_vars}
}}
#MainMenu, footer {{ visibility: hidden; }}
html {{ font-size: 15px; }}
.stApp {{
    background: var(--bg);
    color: var(--text);
    font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
header[data-testid="stHeader"] {{
    height: 3.25rem;
    background: {header_background};
    border-bottom: 1px solid var(--border);
    z-index: 1000;
}}
button[data-testid="stSidebarCollapsedControl"],
[data-testid="stSidebarCollapseButton"] button {{ z-index: 1100; }}
[data-testid="stAppViewContainer"] > .main {{ padding-top: 0; }}
.block-container {{ max-width: 1600px; padding: 4.25rem 1.5rem 1.25rem !important; }}
[data-testid="stVerticalBlock"] {{ gap: .7rem; }}
[data-testid="stHorizontalBlock"] {{ gap: .75rem; }}
[data-testid="stElementContainer"] {{ margin-bottom: 0; }}
hr {{ margin: .25rem 0 .65rem !important; border-color: var(--border) !important; }}

/* Sidebar */
[data-testid="stSidebar"] {{ background: var(--sidebar-bg); border-right: 1px solid var(--sidebar-border); }}
[data-testid="stSidebar"] > div:first-child {{ padding-top: .75rem; }}
[data-testid="stSidebar"] * {{ color: var(--sidebar-text) !important; }}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: .3rem; }}
[data-testid="stSidebar"] .stButton > button {{
    min-height: 2.45rem; justify-content: flex-start; background: transparent !important;
    border: 1px solid transparent !important; box-shadow: none !important;
}}
[data-testid="stSidebar"] .stButton > button:hover {{
    background: var(--sidebar-hover) !important; border-color: var(--primary) !important;
}}
.shell-logo {{ margin: .15rem 0 0; font-size: 1.55rem; font-weight: 800; color: var(--sidebar-text) !important; }}
.shell-logo span {{ color: #5eead4 !important; }}
.shell-subtitle {{ margin: .1rem 0 1.2rem; color: var(--sidebar-muted) !important; font-size: .75rem; }}
.shell-section-label {{ margin: .65rem 0 .25rem; color: var(--sidebar-muted) !important; font-size: .64rem; font-weight: 800; letter-spacing: .11rem; }}
.shell-divider {{ height: 1px; margin: .65rem 0; background: var(--sidebar-border); }}
.shell-user {{ display: flex; align-items: center; gap: .65rem; margin-top: 1rem; padding: .7rem; border-top: 1px solid var(--sidebar-border); }}
.shell-user b, .shell-user small {{ display: block; }}
.shell-user small {{ color: var(--sidebar-muted) !important; font-size: .67rem; }}
.user-avatar {{ display: grid; place-items: center; width: 2rem; height: 2rem; border-radius: 50%; background: #0f766e; color: #fff !important; font-size: .7rem; font-weight: 800; }}
.nav-active-marker {{ height: 2px; margin: -.36rem .65rem .15rem; border-radius: 2px; background: var(--primary); }}

/* Shared */
.page-heading {{ margin: 0; font-size: 1.65rem; line-height: 1.18; font-weight: 780; color: var(--text); }}
.page-kicker {{ margin: .2rem 0 0; font-size: .78rem; color: var(--muted); }}
.section-title {{ margin: 0; font-size: 1rem; line-height: 1.25; font-weight: 750; color: var(--text); }}
.section-subtitle {{ margin: .12rem 0 .55rem; font-size: .72rem; color: var(--muted); }}
.toolbar-label, .insight-heading {{ margin: .55rem 0 .2rem; color: var(--muted); font-size: .65rem; font-weight: 800; letter-spacing: .08rem; text-transform: uppercase; }}
.section-gap {{ height: .45rem; }}
.stButton > button {{ min-height: 2.4rem; border-radius: .48rem; font-weight: 650; }}
[data-testid="stMain"] .stButton > button,
[data-testid="stAppViewContainer"] .main .stButton > button {{
    background: var(--primary) !important; border: 1px solid var(--primary) !important;
    color: #fff !important; box-shadow: none !important;
}}
[data-testid="stMain"] .stButton > button:hover,
[data-testid="stAppViewContainer"] .main .stButton > button:hover {{
    background: var(--primary-hover) !important; border-color: var(--primary-hover) !important;
}}
[data-testid="stMain"] .stButton > button:disabled {{ background: #94bdb8 !important; border-color: #94bdb8 !important; opacity: .72 !important; }}
.stTextInput, .stSelectbox, .stDateInput, .stTextArea, .stRadio {{ margin-bottom: 0 !important; }}
[data-baseweb="input"], [data-baseweb="select"] > div, textarea {{ border-radius: .45rem !important; }}
div[data-testid="stVerticalBlockBorderWrapper"] {{ background: var(--surface); border-color: var(--border) !important; border-radius: .7rem !important; box-shadow: 0 1px 3px rgba(15,23,42,.05); }}
div[data-testid="stVerticalBlockBorderWrapper"] > div {{ padding: .85rem 1rem !important; }}

/* Dashboard */
.kpi-card {{ min-height: 7rem; padding: .85rem 1rem; background: var(--surface); border: 1px solid var(--border); border-top: 3px solid var(--primary); border-radius: .65rem; box-shadow: 0 1px 3px rgba(15,23,42,.05); }}
.kpi-title {{ color: var(--muted); font-size: .65rem; font-weight: 800; text-transform: uppercase; }}
.kpi-value {{ margin-top: .28rem; color: var(--text); font-size: 1.8rem; line-height: 1; font-weight: 790; }}
.kpi-context {{ margin-top: .45rem; color: var(--muted); font-size: .7rem; }}
.overview-grid {{ display: grid; grid-template-columns: repeat(3,1fr); gap: .65rem; padding: 1rem; background: var(--surface); border: 1px solid var(--border); border-radius: .65rem; }}
.overview-grid div {{ padding-left: .6rem; border-left: 2px solid var(--border-strong); }}
.overview-grid small {{ display:block; color:var(--muted); font-size:.66rem; }}
.overview-grid b {{ display:block; margin-top:.25rem; font-size:1.45rem; }}
.amber-text {{ color:var(--warning); }} .green-text {{ color:var(--success); }} .blue-text {{ color:var(--info); }}

/* Table */
.patient-table-header {{ display:grid; grid-template-columns:2.2fr 1.15fr 1.7fr 1.55fr 1.05fr 1.15fr; min-width:790px; padding:.45rem .5rem; border-bottom:1px solid var(--border-strong); color:var(--muted); font-size:.72rem; font-weight:800; text-transform:uppercase; }}
.patient-cell, .table-two-line {{ display:flex; min-height:2.45rem; flex-direction:column; justify-content:center; gap:.2rem; line-height:1.15; }}
.patient-cell b {{ overflow-wrap:anywhere; font-size:.86rem; }}
.patient-cell small, .table-two-line small, .table-meta {{ color:var(--muted); font-size:.71rem; }}
.status-pill {{ display:inline-flex; align-items:center; justify-content:center; padding:.28rem .52rem; border-radius:999px; font-size:.67rem; font-weight:800; }}
.status-pending {{ color:var(--warning); background:var(--warning-soft); }}
.status-approved {{ color:var(--success); background:var(--success-soft); }}
.table-row-rule {{ height:1px; background:var(--border); }}
.pagination-label {{ padding-top:.55rem; color:var(--muted); font-size:.7rem; text-align:center; }}

/* Review and graph */
.workflow-steps {{ display:flex; flex-wrap:wrap; margin:.25rem 0 .55rem; color:var(--muted); font-size:.72rem; }}
.workflow-steps span, .workflow-steps b {{ padding:.45rem 1rem; border-bottom:2px solid var(--border); }}
.workflow-steps b {{ color:var(--primary); border-color:var(--primary); }}
.recording-prompt {{ margin-top:.25rem; padding:.65rem; text-align:center; color:var(--muted); background:var(--surface-soft); border:1px dashed var(--border-strong); border-radius:.5rem; font-size:.72rem; }}
.audit-note {{ padding-bottom:.3rem; color:var(--muted); font-size:.68rem; }}
.summary-text {{ margin:.12rem 0 .55rem; white-space:pre-wrap; color:var(--text); font-size:.76rem; line-height:1.45; }}
.insight-scroll {{ max-height:16rem; overflow-y:auto; padding-right:.2rem; }}
.insight-category {{ margin:.55rem 0; }}
.insight-category-heading {{ margin-bottom:.28rem; color:var(--muted); font-size:.64rem; font-weight:800; text-transform:uppercase; }}
.insight-category-items {{ display:flex; flex-wrap:wrap; gap:.32rem; }}
.insight-chip {{ display:inline-flex; padding:.32rem .5rem; border:1px solid var(--border); border-radius:999px; background:transparent; }}
.insight-chip b {{ font-size:.68rem; }}
.insight-diagnosis {{ background:transparent; border-color:#fecaca; }}
.insight-symptom {{ background:transparent; border-color:#fcd34d; }}
.insight-risk {{ background:transparent; border-color:#fdba74; }}
.insight-procedure {{ background:transparent; border-color:#93c5fd; }}
.clinical-graph-v2 {{ padding:1rem; background:var(--surface-soft); border:1px solid var(--border); border-radius:.65rem; }}
.graph-hierarchy-v2 {{ display:flex; flex-direction:column; align-items:center; gap:.6rem; }}
.graph-patient-card {{ max-width:80%; padding:.48rem .8rem; border-radius:.48rem; background:var(--primary); color:#fff; font-size:.73rem; font-weight:750; text-align:center; }}
.graph-connector-main {{ width:2px; height:.8rem; background:var(--border-strong); }}
.graph-categories {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); width:100%; gap:.5rem; }}
.graph-category-header {{ margin-bottom:.35rem; color:var(--muted); font-size:.58rem; font-weight:800; text-align:center; text-transform:uppercase; }}
.graph-category-content {{ display:flex; flex-direction:column; gap:.3rem; }}
.graph-node-card, .graph-empty-state {{ padding:.52rem .4rem; border-radius:.42rem; font-size:.68rem; line-height:1.25; text-align:center; overflow-wrap:anywhere; }}
.graph-node-diagnosis {{ background:var(--purple); color:#fff; }}
.graph-node-symptom {{ background:var(--info); color:#fff; }}
.graph-node-risk {{ background:var(--warning); color:#fff; }}
.graph-empty-state {{ color:var(--muted); border:1px dashed var(--border-strong); }}

/* Codes */
.code-hero {{ padding:1rem 1.1rem; background:linear-gradient(135deg,#ecfdf5,#f0fdfa); border:1px solid #99f6e4; border-radius:.7rem; }}
.code-hero-title {{ font-size:1rem; font-weight:780; color:#115e59; }}
.code-hero-text {{ margin-top:.25rem; color:#47706d; font-size:.75rem; }}
.code-patient {{ margin-top:.65rem; font-size:.78rem; color:var(--text); }}
.code-empty {{ padding:1.2rem; text-align:center; color:var(--muted); background:var(--surface-soft); border:1px dashed var(--border-strong); border-radius:.55rem; }}

@media (max-width:760px) {{
    .block-container {{ padding:3.8rem .75rem .75rem !important; }}
    .page-heading {{ font-size:1.35rem; }}
    .overview-grid {{ grid-template-columns:1fr; }}
    .graph-node-card, .graph-empty-state {{ font-size:.56rem; }}
}}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# 3. DATABASE AND SESSION STATE
# ============================================================
def load_db():
    if not DB_FILE.exists():
        return []
    try:
        with DB_FILE.open("r", encoding="utf-8") as file:
            payload = json.load(file)
            return payload.get("records", []) if isinstance(payload, dict) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_db(records):
    temp_path = DB_FILE.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump({"records": records}, file, indent=4, ensure_ascii=False)
    temp_path.replace(DB_FILE)


def initialize_state():
    defaults = {
        "records": load_db(),
        "page": "dashboard",
        "current_record": None,
        "dash_page": 1,
        "chart_cases_page": 1,
        "scribe_result": None,
        "insight_count": 1,
        "detected_insights": [],
        "last_record_id": None,
        "edit_summary": "",
        "code_results": {"icd10": [], "cpt": []},
        "code_record_id": None,
        "code_results_signature": None,
        "code_matcher_version": None,
        "code_generation_error": "",
        "generated_bill": None,
        "generated_bill_record_id": None,
        "generated_bill_patient_id": None,
        "theme": "Light",
        "theme_defaults_version": "light-default-v3",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


initialize_state()

MASTER_INSIGHTS = [
    "None", "Diabetes (Type 2)", "Hypertension", "Hyperlipidemia", "Asthma",
    "Osteoarthritis", "Obesity", "Smoking History", "Alcohol Use", "Anxiety",
    "Depression", "Fever", "Chronic Cough", "Fatigue", "Headache", "Chest Pain",
    "Nausea", "Color Blindness", "Complete Blood Count", "A1C Test", "Chest X-ray",
    "CT Scan", "MRI", "Ultrasound", "Blood Glucose Test",
]


def get_detected_insights(transcript_text):
    text = (transcript_text or "").lower()
    keyword_map = {
        "diabetes": "Diabetes (Type 2)", "hypertension": "Hypertension", "high bp": "Hypertension",
        "cholesterol": "Hyperlipidemia", "asthma": "Asthma", "arthritis": "Osteoarthritis",
        "obesity": "Obesity", "smok": "Smoking History", "alcohol": "Alcohol Use",
        "anxiety": "Anxiety", "depression": "Depression", "fever": "Fever",
        "cough": "Chronic Cough", "fatigue": "Fatigue", "headache": "Headache",
        "chest pain": "Chest Pain", "nausea": "Nausea", "color blind": "Color Blindness",
        "complete blood count": "Complete Blood Count", "cbc": "Complete Blood Count",
        "a1c": "A1C Test", "hba1c": "A1C Test", "chest x-ray": "Chest X-ray",
        "chest x ray": "Chest X-ray", "chest xray": "Chest X-ray", "xray": "Chest X-ray",
        "x-ray": "Chest X-ray", "x ray": "Chest X-ray", "ct scan": "CT Scan", "mri": "MRI",
        "ultrasound": "Ultrasound", "blood glucose": "Blood Glucose Test",
        "physical therapy": "Physical Therapy", "physiotherapy": "Physical Therapy",
        "injection": "Injection", "injections": "Injection",
    }
    return list(dict.fromkeys(value for keyword, value in keyword_map.items() if keyword in text))


def get_record_insights(record):
    transcript_insights = get_detected_insights(record.get("transcript", ""))
    manual_insights = record.get("manual_insights", [])
    return list(dict.fromkeys(
        transcript_insights
        + [item for item in manual_insights if item != "None"]
        + ["Established Patient Office Visit"]
    ))


def get_code_suggestions(insights, documentation=None):
    """Return separate ICD/CPT suggestions, using documentation context when supplied."""
    st.session_state.code_generation_error = ""

    if classify_medical_item is None or get_icd10_codes is None or get_cpt_codes is None:
        st.session_state.code_generation_error = (
            "code_matcher.py could not be imported. Keep code_matcher.py either in the "
            "frontend package or in the same directory as this Streamlit file."
        )
        return {"icd10": [], "cpt": []}

    candidates = list(dict.fromkeys(
        str(item).strip() for item in insights if item and item != "None"
    ))
    if not candidates:
        st.session_state.code_generation_error = "No codable clinical insights were detected."
        return {"icd10": [], "cpt": []}

    icd_items = []
    cpt_items = []
    classification_errors = []

    procedure_terms = (
        "test", "x-ray", "x ray", "scan", "mri", "ultrasound",
        "blood", "cbc", "a1c", "hba1c", "procedure", "office visit"
    )

    for item in candidates:
        try:
            item_type = str(classify_medical_item(item) or "").strip().lower()
        except Exception as error:
            classification_errors.append(f"{item}: {error}")
            item_type = ""

        if item_type in {"cpt", "hcpcs", "procedure"}:
            cpt_items.append(item)
        elif item_type in {"icd10", "icd-10", "icd", "diagnosis"}:
            icd_items.append(item)
        elif any(term in item.lower() for term in procedure_terms):
            cpt_items.append(item)
        else:
            # Diagnoses, symptoms, and risk factors should be evaluated for ICD-10.
            icd_items.append(item)

    try:
        if documentation is not None and get_icd10_candidate_sets and get_cpt_candidate_sets:
            evidence = {item: documentation for item in icd_items}
            icd_details = get_icd10_candidate_sets(icd_items, threshold=80, evidence_by_term=evidence)
            icd_results = [
                {
                    "Extracted Condition": item["term"],
                    "Matched Disease/Injury": item["selected_description"],
                    "ICD-10 Code": item["selected_code"],
                }
                for item in icd_details
                if item["status"] == "suggested"
            ]
            performed_cpt_items = [
                item for item in cpt_items
                if is_explicitly_performed and is_explicitly_performed(item, documentation)
            ]
            cpt_details = get_cpt_candidate_sets(performed_cpt_items, documentation, threshold=80)
            cpt_results = [
                {
                    "Extracted Procedure": item["term"],
                    "Matched Procedure/Service": item["selected_description"],
                    "CPT/HCPCS Code": item["selected_code"],
                }
                for item in cpt_details
                if item["status"] == "suggested"
            ]
        else:
            icd_results = get_icd10_codes(icd_items, threshold=80) if icd_items else []
            cpt_results = get_cpt_codes(cpt_items, threshold=80) if cpt_items else []
    except Exception as error:
        st.session_state.code_generation_error = f"Code matching failed: {error}"
        return {"icd10": [], "cpt": []}

    if classification_errors:
        st.session_state.code_generation_error = (
            "Some items could not be classified automatically: "
            + "; ".join(classification_errors)
        )
    elif not icd_results and not cpt_results:
        st.session_state.code_generation_error = (
            "The matcher loaded correctly, but no entries met the 80% similarity threshold. "
            "Check the terms and code datasets loaded by code_matcher.py."
        )

    return {"icd10": icd_results or [], "cpt": cpt_results or []}


def get_backend_code_suggestions(insights, documentation=""):
    """Use the backend coding contract, falling back to the local matcher offline."""
    try:
        procedures = []
        conditions = []
        procedure_terms = (
            "test", "x-ray", "x ray", "scan", "mri", "ultrasound", "blood",
            "cbc", "a1c", "hba1c", "procedure", "office visit", "therapy",
            "injection", "surgery", "biopsy", "endoscopy", "evaluation",
        )
        for item in insights:
            item_type = str(classify_medical_item(item) or "").lower()
            if item_type in {"cpt", "hcpcs", "procedure"} or any(term in item.lower() for term in procedure_terms):
                procedures.append(item)
            else:
                conditions.append(item)
        documentation_lower = documentation.lower()
        if "ct scan" in documentation_lower and "head" in documentation_lower:
            procedures.append("CT scan of head without contrast")
        if "chest x-ray" in documentation_lower and "two view" in documentation_lower:
            procedures.append("Chest x ray 2 views")
        return HealthcareApiClient().match_codes(conditions, procedures, documentation)
    except (RuntimeError, requests.exceptions.HTTPError):
        return get_code_suggestions(insights, documentation)


def get_code_results_signature(insights, transcript):
    """Key cached suggestions on both the detected insights and the transcript text."""
    normalized_insights = tuple(
        str(item).strip() for item in (insights or []) if item and item != "None"
    )
    normalized_transcript = (transcript or "").strip()
    return normalized_insights, normalized_transcript


def filter_billing_eligible_cpt_results(cpt_results, documentation):
    """Keep only API/local CPT candidates explicitly performed in this encounter."""
    billing_eligible_cpt_results = []
    excluded_nonperformed_count = 0
    unverifiable_count = 0

    for candidate in cpt_results or []:
        if not isinstance(candidate, dict):
            unverifiable_count += 1
            continue

        source_procedure = candidate.get("Extracted Procedure")
        if not isinstance(source_procedure, str) or not source_procedure.strip():
            unverifiable_count += 1
            continue

        status = (
            classify_procedure_status(source_procedure, documentation)
            if classify_procedure_status
            else "unknown"
        )
        if status == "unknown":
            unverifiable_count += 1
        elif is_explicitly_performed and is_explicitly_performed(source_procedure, documentation):
            billing_eligible_cpt_results.append(candidate)
        else:
            excluded_nonperformed_count += 1

    return (
        billing_eligible_cpt_results,
        excluded_nonperformed_count,
        unverifiable_count,
    )


def prepare_billing_lines(cpt_results, documentation, encounter_date=None):
    billing_eligible_cpt_results, excluded_count, unverifiable_count = (
        filter_billing_eligible_cpt_results(cpt_results, documentation)
    )
    billing_rows = (
        build_cpt_bill_rows(billing_eligible_cpt_results, encounter_date)
        if billing_eligible_cpt_results
        else []
    )
    return billing_eligible_cpt_results, excluded_count, unverifiable_count, billing_rows



# ============================================================
# 4. REUSABLE COMPONENTS
# ============================================================
def go_to(page):
    st.session_state.page = page


def start_new_consultation():
    st.session_state.scribe_result = None
    st.session_state.page = "new_consult"


def open_record(record, destination="review"):
    st.session_state.current_record = record
    st.session_state.detected_insights = get_record_insights(record)
    st.session_state.insight_count = 1
    go_to(destination)


def render_sidebar(active_page):
    with st.sidebar:
        st.markdown(
            '<div class="shell-logo">Nuu<span>Care</span></div>'
            '<div class="shell-subtitle">Clinical Intelligence Platform</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="shell-section-label">WORKSPACE</div>', unsafe_allow_html=True)
        navigation = [
            ("dashboard", "Overview"),
            ("new_consult", "Consultations"),
            ("ehr_data", "EHR Demo Data"),
            ("review", "Clinical Review"),
            ("codes", "Codes"),
        ]
        for page_key, label in navigation:
            if st.button(label, key=f"nav_{page_key}", use_container_width=True):
                if page_key == "review" and not st.session_state.current_record:
                    go_to("dashboard")
                else:
                    go_to(page_key)
                st.rerun()
            if active_page == page_key:
                st.markdown('<div class="nav-active-marker"></div>', unsafe_allow_html=True)

        st.markdown('<div class="shell-divider"></div><div class="shell-section-label">UTILITY</div>', unsafe_allow_html=True)
        theme_value = st.selectbox("Theme", ["Light", "Dark"], index=0 if st.session_state.get("theme", "Light") == "Light" else 1, key="theme_selector")
        if st.session_state.get("theme") != theme_value:
            st.session_state["theme"] = theme_value
            st.rerun()
        st.button("Settings", key="nav_settings", use_container_width=True)
        st.button("Help", key="nav_help", use_container_width=True)
        st.markdown(
            '<div class="shell-user"><div class="user-avatar">NC</div>'
            '<div><b>NuuCare User</b><small>Clinical Operations</small></div></div>',
            unsafe_allow_html=True,
        )


def render_header(title, subtitle, show_new=True):
    left, action = st.columns([8, 2], gap="medium", vertical_alignment="center")
    left.markdown(
        f'<div class="page-heading">{escape(title)}</div>'
        f'<div class="page-kicker">{escape(subtitle)}</div>',
        unsafe_allow_html=True,
    )
    if show_new and action.button("New Consultation", key=f"header_new_{st.session_state.page}", use_container_width=True, type="primary"):
        start_new_consultation()
        st.rerun()


def render_kpi_card(label, value, accent, context):
    st.markdown(
        f'<div class="kpi-card" style="border-top-color:{accent}">'
        f'<div class="kpi-title">{escape(label)}</div><div class="kpi-value">{value}</div>'
        f'<div class="kpi-context">{escape(context)}</div></div>', unsafe_allow_html=True,
    )


def render_status_badge(status):
    css_class = "status-approved" if status == "Approved" else "status-pending"
    return f'<span class="status-pill {css_class}">{escape(status.upper())}</span>'


def parse_transcript_entries(transcript, doctor_name="Doctor", patient_name="Patient"):
    text = (transcript or "").strip()
    if not text:
        return []
    entries, current_speaker, current_text = [], doctor_name, ""

    def flush():
        nonlocal current_text
        if current_text.strip():
            entries.append({"Speaker": current_speaker, "Text": current_text.strip()})
        current_text = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(doctor|patient)\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if match:
            flush()
            current_speaker = doctor_name if match.group(1).lower() == "doctor" else patient_name
            current_text = match.group(2).strip()
        else:
            current_text = f"{current_text} {line}".strip()
    flush()
    return entries or [{"Speaker": doctor_name, "Text": text}]


def append_selected_insights_to_note(current_text, selected_insights):
    updated = current_text or ""
    values = [value for value in selected_insights if value and value != "None"]
    if not values:
        return updated
    additions = "\n".join(f"• {value}" for value in values)
    heading = "MAJOR DIAGNOSIS / ISSUES"
    if "Awaiting manual input." in updated:
        return updated.replace("Awaiting manual input.", additions, 1)
    if heading in updated:
        return f"{updated.rstrip()}\n{additions}"
    return f"{updated.rstrip()}\n\n{heading}\n{additions}".strip()


def render_patient_table(records, selected_date=None):
    if selected_date:
        records = [r for r in records if selected_date in (r.get("date"), r.get("approval_date"))]
        st.caption(f"Cases for {selected_date} ({len(records)})")
    per_page = 3 if selected_date else 5
    state_key = "chart_cases_page" if selected_date else "dash_page"
    total_pages = max(1, math.ceil(len(records) / per_page))
    st.session_state[state_key] = min(max(st.session_state[state_key], 1), total_pages)
    current_page = st.session_state[state_key]
    current_records = records[(current_page - 1) * per_page: current_page * per_page]
    st.markdown(
        '<div class="patient-table-header"><span>Patient</span><span>ID</span><span>Doctor</span>'
        '<span>Date</span><span>Status</span><span>Action</span></div>', unsafe_allow_html=True,
    )
    for row_index, record in enumerate(current_records):
        columns = st.columns([2.2, 1.15, 1.7, 1.55, 1.05, 1.15], vertical_alignment="center")
        columns[0].markdown(f'<div class="patient-cell"><b>{escape(record.get("name", ""))}</b><small>{escape(str(record.get("age", "")))}y · {escape(record.get("gender", ""))}</small></div>', unsafe_allow_html=True)
        columns[1].markdown(f'<span class="table-meta">{escape(record.get("id", ""))}</span>', unsafe_allow_html=True)
        columns[2].markdown(f'<span class="table-meta">{escape(record.get("doctor", "N/A"))}</span>', unsafe_allow_html=True)
        date_text = record.get("approval_date") if record.get("status") == "Approved" else record.get("date", "")
        columns[3].markdown(f'<div class="table-two-line"><span>{escape(date_text or "")}</span><small>{escape(record.get("time", ""))}</small></div>', unsafe_allow_html=True)
        columns[4].markdown(render_status_badge(record.get("status", "Pending")), unsafe_allow_html=True)
        key_suffix = f'{record.get("id", "record")}_{row_index}_{current_page}'
        if columns[5].button("View" if record.get("status") == "Approved" else "Review", key=f"record_{key_suffix}", use_container_width=True):
            open_record(record)
            st.rerun()
        st.markdown('<div class="table-row-rule"></div>', unsafe_allow_html=True)
    if total_pages > 1:
        previous, info, next_col = st.columns([1, 2, 1], vertical_alignment="center")
        if previous.button("Previous", key=f"previous_{state_key}", disabled=current_page == 1, use_container_width=True):
            st.session_state[state_key] -= 1
            st.rerun()
        info.markdown(f'<div class="pagination-label">Page {current_page} of {total_pages}</div>', unsafe_allow_html=True)
        if next_col.button("Next", key=f"next_{state_key}", disabled=current_page == total_pages, use_container_width=True):
            st.session_state[state_key] += 1
            st.rerun()


def get_insight_category(insight):
    lowered = insight.lower()
    if any(x in lowered for x in ("diabetes", "hypertension", "cholesterol", "asthma", "arthritis")):
        return "Diagnoses", "insight-diagnosis"
    if any(x in lowered for x in ("smok", "alcohol", "obesity")):
        return "Risk Factors", "insight-risk"
    if any(x in lowered for x in ("x-ray", "ct scan", "blood", "cbc", "a1c", "mri", "ultrasound")):
        return "Tests/Procedures", "insight-procedure"
    return "Symptoms", "insight-symptom"


def render_grouped_insights(insights):
    grouped = {category: [] for category in ("Diagnoses", "Symptoms", "Risk Factors", "Tests/Procedures")}
    for insight in insights:
        category, css_class = get_insight_category(insight)
        grouped[category].append((insight, css_class))
    parts = ['<div class="insight-scroll">']
    for category, values in grouped.items():
        if not values:
            continue
        parts.append(f'<div class="insight-category"><div class="insight-category-heading">{escape(category)} ({len(values)})</div><div class="insight-category-items">')
        for insight, css_class in values:
            parts.append(f'<div class="insight-chip {css_class}"><b>{escape(insight)}</b></div>')
        parts.append("</div></div>")
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_clinical_graph(patient_name, insights):
    categories = {"Diagnoses": [], "Symptoms": [], "Risk Factors": []}
    for insight in insights:
        category, _ = get_insight_category(insight)
        categories[category if category in categories else "Symptoms"].append(insight)
    class_map = {"Diagnoses": "diagnosis", "Symptoms": "symptom", "Risk Factors": "risk"}
    parts = ['<div class="clinical-graph-v2"><div class="graph-hierarchy-v2">', f'<div class="graph-patient-card">Patient: {escape(patient_name[:40])}</div>', '<div class="graph-connector-main"></div><div class="graph-categories">']
    for category, values in categories.items():
        parts.append(f'<div><div class="graph-category-header">{category}</div><div class="graph-category-content">')
        if values:
            for value in values:
                parts.append(f'<div class="graph-node-card graph-node-{class_map[category]}">{escape(value)}</div>')
        else:
            parts.append(f'<div class="graph-empty-state">No {category.lower()} detected</div>')
        parts.append("</div></div>")
    parts.append("</div></div></div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def clean_clinical_text(value):
    text = unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_summary(summary):
    text = clean_clinical_text(summary)
    if not text:
        st.caption("No clinical summary is available.")
        return
    headings = {"subjective", "objective", "chief complaint", "history of present illness", "past medical history", "current medications", "assessment", "plan"}
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
        st.markdown(f"**{heading.title()}**")
        st.markdown(f'<div class="summary-text">{escape(value)}</div>', unsafe_allow_html=True)


def render_cds_recommendations(recommendations):
    text = clean_clinical_text(recommendations)
    sections = {"Recommendation": "", "Reasoning": "", "Safety Note": ""}
    pattern = r"(?im)^\s*#{0,6}\s*(Recommendation|Reasoning|Safety Note)\s*:?\s*$"
    matches = list(re.finditer(pattern, text))
    if matches:
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections[match.group(1).title()] = text[match.end():end].strip()
    else:
        sections["Recommendation"] = text
    tabs = st.tabs(list(sections.keys()))
    for tab, (label, content) in zip(tabs, sections.items()):
        with tab:
            if content:
                st.markdown(content)
            else:
                st.caption(f"No {label.lower()} provided.")


def render_code_results(values, empty_message):
    if not values:
        st.markdown(f'<div class="code-empty">{escape(empty_message)}</div>', unsafe_allow_html=True)
        return
    if isinstance(values, list) and values and isinstance(values[0], dict):
        st.dataframe(pd.DataFrame(values), hide_index=True, use_container_width=True)
    else:
        for value in values if isinstance(values, list) else [values]:
            st.markdown(f"- {escape(str(value))}")


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


def show_ehr_demo_data():
    render_sidebar("ehr_data")
    render_header("EHR Demo Data", "Synthetic patient and bill records from the demo workbook and backend", show_new=False)

    client = HealthcareApiClient()
    backend_read_methods = [
        "export_patient_data",
        "list_patients",
        "get_patient_chart",
    ]
    backend_create_methods = ["create_patient", "create_patient_record"]
    backend_edit_methods = ["update_patient", "replace_patient_records"]
    backend_delete_methods = ["delete_patient"]
    backend_bill_methods = ["generate_patient_bill"]

    backend_read_available = any(hasattr(client, method) for method in backend_read_methods)
    backend_create_available = any(hasattr(client, method) for method in backend_create_methods)
    backend_edit_available = any(hasattr(client, method) for method in backend_edit_methods)
    backend_delete_available = any(hasattr(client, method) for method in backend_delete_methods)
    backend_bill_available = any(hasattr(client, method) for method in backend_bill_methods)
    workbook_exists = EHR_DATA_FILE.exists()
    workbook = None
    backend_connected = False
    workbook_fallback_active = False
    backend_payload = None

    if backend_read_available:
        try:
            for method_name in backend_read_methods:
                if hasattr(client, method_name):
                    backend_payload = getattr(client, method_name)()
                    backend_connected = True
                    break
        except (RuntimeError, requests.exceptions.RequestException, OSError, ValueError) as error:
            st.warning(f"Backend EHR read endpoint failed: {error}")
            backend_connected = False

    if backend_connected and backend_payload is not None:
        st.success("Backend connected")
        st.caption("Using the live EHR backend for read operations.")
        read_capable = True
        can_create = backend_create_available
        can_edit = backend_edit_available
        can_delete = backend_delete_available
        can_bill = backend_bill_available
        st.dataframe(pd.DataFrame(backend_payload.get("sheets", {}).get("Patient_Master", {}).get("records", [])) if isinstance(backend_payload, dict) else pd.DataFrame())
        st.caption("The live EHR backend is connected. Create, update, delete, and patient bill generation remain available only when the backend exposes those methods.")

        action_cols = st.columns(4)
        action_labels = ["Create patient", "Edit patient", "Delete patient", "Generate patient bill"]
        for column, label, disabled in zip(action_cols, action_labels, [not can_create, not can_edit, not can_delete, not can_bill]):
            column.button(label, disabled=disabled)
    else:
        if workbook_exists:
            workbook_fallback_active = True
            try:
                workbook = load_ehr_demo_workbook(str(EHR_DATA_FILE))
                st.warning("Backend unavailable, using Excel fallback")
                st.info("Read-only Excel fallback is active. Start the EHR backend to enable create, update, delete and patient-specific bill generation.")
            except FileNotFoundError as error:
                st.error(str(error))
                workbook = {}
                workbook_fallback_active = False
                st.error("Workbook unavailable")
        else:
            st.warning("Workbook unavailable")
            st.error(f"The EHR demo workbook was not found at {EHR_DATA_FILE}.")
            return

        available_sheets = list(workbook.keys())
        if not available_sheets:
            st.warning("No workbook sheets were loaded from the EHR demo Excel file.")
            return

        selected_sheet = st.selectbox("Record type", available_sheets, index=available_sheets.index("Patient_Master") if "Patient_Master" in available_sheets else 0)
        sheet_frame = workbook.get(selected_sheet, pd.DataFrame())

        if isinstance(sheet_frame, pd.DataFrame) and not sheet_frame.empty:
            st.caption(f"Loaded {len(sheet_frame)} row(s) from the '{selected_sheet}' sheet.")
        else:
            st.warning(f"The requested record type is unavailable: {selected_sheet}")
            return

        read_capable = True
        can_create = False
        can_edit = False
        can_delete = False
        can_bill = False

        patient_cols = []
        if isinstance(sheet_frame, pd.DataFrame):
            patient_cols = [
                col for col in sheet_frame.columns
                if any(term in str(col).lower() for term in ("patient", "mrn", "first_name", "last_name", "name", "legal_name", "member", "id"))
            ]

        st.caption(f"Workbook sheet: {selected_sheet}")
        search_query = st.text_input("Search patient records", placeholder="Enter patient name, MRN, or ID") if patient_cols else None
        if search_query is not None:
            if patient_cols:
                search_lower = search_query.strip().lower()
                if search_lower:
                    matches = sheet_frame.apply(
                        lambda row: any(
                            isinstance(row.get(col), str) and search_lower in str(row.get(col)).lower()
                            or isinstance(row.get(col), (int, float)) and str(row.get(col)).lower() == search_lower
                            for col in patient_cols
                        ),
                        axis=1,
                    )
                    sheet_frame = sheet_frame[matches].copy()
                st.caption(f"{len(sheet_frame)} patient record(s) match the current filter.")
            else:
                st.info("No patient-identifying columns are available in this workbook sheet, so patient search and filtering are unavailable here.")

        if "Patient_Master" in workbook and selected_sheet == "Patient_Master":
            if any(col in workbook["Patient_Master"].columns for col in ["Patient_ID", "MRN", "First_Name", "Last_Name", "Legal_Name"]):
                st.dataframe(workbook["Patient_Master"], use_container_width=True, hide_index=True)
            else:
                st.warning("The Patient_Master sheet is available, but the expected patient demographics columns are missing.")
        else:
            st.dataframe(sheet_frame, use_container_width=True, hide_index=True)

        action_cols = st.columns(4)
        action_labels = ["Create patient", "Edit patient", "Delete patient", "Generate patient bill"]
        for column, label, disabled in zip(
            action_cols,
            action_labels,
            [True, True, True, True],
        ):
            column.button(label, disabled=disabled)

        st.caption("Read-only Excel fallback is active. Start the EHR backend to enable create, update, delete and patient-specific bill generation.")

    status_cols = st.columns(5)
    status_items = [
        ("Backend connected", backend_connected),
        ("Backend unavailable, using Excel fallback", workbook_fallback_active),
        ("Workbook unavailable", not workbook_exists),
        ("Read-only mode", workbook_fallback_active or not (backend_create_available or backend_edit_available or backend_delete_available or backend_bill_available)),
        ("Full read/write mode", (backend_create_available or backend_edit_available or backend_delete_available or backend_bill_available) and backend_connected),
    ]
    for column, (label, active) in zip(status_cols, status_items):
        if active:
            column.success(label)
        else:
            column.caption(label)

    if not workbook_fallback_active and not backend_connected:
        st.warning("No backend EHR read endpoint is active and the Excel fallback is unavailable.")

    if workbook_exists and not backend_connected:
        st.caption(f"Workbook available at: {EHR_DATA_FILE}")
        st.caption(f"Workbook sheets: {', '.join(sorted(workbook.keys())) if workbook else 'none loaded'}")


def render_transcript(record, read_only=False):
    with st.expander("Transcript Audit Trail", expanded=False):
        doctor, patient = record.get("doctor", "Doctor"), record.get("name", "Patient")
        rows = record.get("transcript_data") or parse_transcript_entries(record.get("transcript", ""), doctor, patient)
        frame = pd.DataFrame(rows or [{"Speaker": doctor, "Text": "No transcript found."}])
        label = "Read-only clinical conversation" if read_only else "Editable clinical conversation"
        st.markdown(f'<div class="audit-note">{label} · {len(frame)} entries</div>', unsafe_allow_html=True)
        if read_only:
            st.dataframe(frame, use_container_width=True, hide_index=True)
            return frame
        return st.data_editor(frame, use_container_width=True, num_rows="dynamic", hide_index=True,
            column_config={"Speaker": st.column_config.SelectboxColumn("Speaker", options=[doctor, patient, "Unknown"], required=True), "Text": st.column_config.TextColumn("Spoken Text", width="large")})


# ============================================================
# 5. DASHBOARD
# ============================================================
def show_dashboard():
    render_sidebar("dashboard")
    render_header("Clinical Operations", "Consultation and documentation overview")
    st.markdown('<div class="toolbar-label">RECORD DIRECTORY</div>', unsafe_allow_html=True)
    filter_search, filter_date, filter_status = st.columns([5, 2, 3])
    search_query = filter_search.text_input("Search patient or ID", placeholder="Search patient or ID").strip().lower()
    date_filter = filter_date.selectbox("Date", ["All Time", "Today", "Last 7 Days", "Last 30 Days", "Custom Date Range"])
    status_filter = filter_status.selectbox("Status", ["All Statuses", "Pending", "Approved"])
    custom_dates = st.date_input("Custom Range", value=(datetime.now().date(), datetime.now().date())) if date_filter == "Custom Date Range" else None
    today, filtered = datetime.now().date(), []
    for record in st.session_state.records:
        searchable = f'{record.get("name", "")} {record.get("id", "")}'.lower()
        if search_query and search_query not in searchable:
            continue
        if status_filter != "All Statuses" and record.get("status") != status_filter:
            continue
        try:
            record_date = datetime.strptime(record.get("date", ""), "%d/%m/%Y").date()
        except (ValueError, TypeError):
            continue
        keep = date_filter == "All Time"
        if date_filter == "Today": keep = record_date == today
        elif date_filter == "Last 7 Days": keep = 0 <= (today - record_date).days <= 7
        elif date_filter == "Last 30 Days": keep = 0 <= (today - record_date).days <= 30
        elif date_filter == "Custom Date Range" and isinstance(custom_dates, tuple): keep = len(custom_dates) == 2 and custom_dates[0] <= record_date <= custom_dates[1]
        if keep: filtered.append(record)
    total = len(filtered)
    pending = sum(r.get("status") == "Pending" for r in filtered)
    approved = sum(r.get("status") == "Approved" for r in filtered)
    today_count = sum(r.get("date") == today.strftime("%d/%m/%Y") for r in filtered)
    kpis = st.columns(4)
    for col, item in zip(kpis, [("Total Consultations", total, "#0f766e", "All recorded consults"), ("Today's Volume", today_count, "#2563eb", "Created today"), ("Pending Review", pending, "#b45309", "Requires attention"), ("Approved Notes", approved, "#15803d", "Completed documentation")]):
        with col: render_kpi_card(*item)
    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
    chart_col, overview_col = st.columns([6.5, 3.5], gap="large")
    with chart_col:
        st.markdown('<div class="section-title">Consultation Activity</div><div class="section-subtitle">Daily consultation throughput and backlog</div>', unsafe_allow_html=True)
        events = []
        for record in filtered:
            events.append({"Date": record.get("date"), "Metric": "New Consults"})
            if record.get("status") == "Approved": events.append({"Date": record.get("approval_date", record.get("date")), "Metric": "Approved"})
        if events:
            frame = pd.DataFrame(events)
            frame["Date"] = pd.to_datetime(frame["Date"], format="%d/%m/%Y", errors="coerce")
            counts = frame.dropna(subset=["Date"]).groupby(["Date", "Metric"]).size().reset_index(name="Count")
            chart = alt.Chart(counts).mark_line(point=True).encode(
                x=alt.X("Date:T", title="Date", axis=alt.Axis(format="%b %d, %Y")),
                y=alt.Y("Count:Q", title="Records"),
                color=alt.Color("Metric:N", legend=alt.Legend(title="")),
                tooltip=[alt.Tooltip("Date:T", title="Date", format="%b %d, %Y"), "Metric:N", "Count:Q"],
            ).properties(height=220)
            st.altair_chart(chart, use_container_width=True)
        else: st.info("No consultation activity is available for the selected filters.")
    with overview_col:
        approval_rate = round(approved / total * 100) if total else 0
        st.markdown('<div class="section-title">Clinical Overview</div><div class="section-subtitle">Current operating position</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="overview-grid"><div><small>Pending review</small><b class="amber-text">{pending}</b></div><div><small>Approval rate</small><b class="green-text">{approval_rate}%</b></div><div><small>Today\'s consultations</small><b class="blue-text">{today_count}</b></div></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-gap"></div><div class="section-title">Patient Records</div><div class="section-subtitle">Clinical documentation queue</div>', unsafe_allow_html=True)
    if filtered:
        render_patient_table(filtered)
    else:
        st.info("No clinical records found.")


# ============================================================
# 6. NEW CONSULTATION
# ============================================================
def show_new_consultation():
    render_sidebar("new_consult")
    render_header("New Consultation", "Capture patient context and generate clinical documentation", show_new=False)
    st.markdown('<div class="workflow-steps"><b>01 Patient</b><span>02 Record</span><span>03 Generate</span><span>04 Review</span></div>', unsafe_allow_html=True)
    if st.button("Back to Dashboard"):
        go_to("dashboard"); st.rerun()
    with st.container(border=True):
        st.markdown('<div class="section-title">Patient Information</div><div class="section-subtitle">Enter the basic context for this consultation</div>', unsafe_allow_html=True)
        left, right = st.columns(2, gap="large")
        patient_name = left.text_input("Full Name", placeholder="e.g. John Doe")
        patient_age = right.text_input("Age")
        patient_gender = left.selectbox("Gender", ["Male", "Female", "Other"])
        doctor_name = right.text_input("Attending Doctor", placeholder="e.g. Dr. Sarah Jenkins")
    with st.container(border=True):
        st.markdown('<div class="section-title">Record Consultation</div><div class="section-subtitle">Capture the clinical conversation securely</div>', unsafe_allow_html=True)
        audio_file = st.audio_input("Record Audio", label_visibility="collapsed")
        st.markdown('<div class="recording-prompt">Audio input ready when you are</div>', unsafe_allow_html=True)
        if st.button("Transcribe and Generate Summary", type="primary", use_container_width=True, disabled=audio_file is None or not patient_name.strip()):
            try:
                with st.spinner("Transcribing audio..."):
                    recognizer = sr.Recognizer()
                    with sr.AudioFile(io.BytesIO(audio_file.getvalue())) as source:
                        transcript = recognizer.recognize_google(recognizer.record(source))
                with st.spinner("Generating clinical summary..."):
                    st.session_state.scribe_result = HealthcareApiClient().scribe_encounter(transcript)
                st.rerun()
            except Exception as error:
                st.error(f"Unable to process the consultation: {error}")
        result = st.session_state.scribe_result
        if result:
            st.markdown('<div class="section-title">Transcript</div>', unsafe_allow_html=True)
            st.text_area("Generated transcript", value=result.get("transcript", ""), height=150, disabled=True, label_visibility="collapsed")
            st.markdown('<div class="section-title">Summary and Key Highlights</div>', unsafe_allow_html=True)
            render_summary(result.get("soap_note") or result.get("patient_summary", "Not available"))
            for highlight in result.get("key_highlights", []): st.markdown(f"- {highlight}")
            st.info("Clinical Decision Support recommendations will be generated for every encounter.")
            if st.button("Generate Note", type="primary", use_container_width=True):
                encounter = result
                try:
                    encounter = HealthcareApiClient().add_cds(encounter)
                except (RuntimeError, requests.exceptions.HTTPError) as error:
                    st.error(f"CDS generation failed: {error}"); return
                transcript = encounter.get("transcript", "")
                doctor, now = doctor_name.strip() or "Doctor", datetime.now()
                record = {"id": f"PT-{str(uuid.uuid4().int)[:6]}", "name": patient_name.strip(), "age": patient_age.strip(), "gender": patient_gender, "doctor": doctor, "date": now.strftime("%d/%m/%Y"), "time": now.strftime("%H:%M"), "status": "Pending", "transcript": transcript, "transcript_data": parse_transcript_entries(transcript, doctor, patient_name.strip()), "summary": encounter.get("soap_note", ""), "patient_summary": encounter.get("patient_summary", ""), "key_highlights": encounter.get("key_highlights", []), "retrieved_guidelines": encounter.get("retrieved_guidelines", ""), "recommendations": encounter.get("recommendations", ""), "cds_requested": True, "analysis_version": ANALYSIS_VERSION}
                st.session_state.records.insert(0, record)
                save_db(st.session_state.records)
                open_record(record)
                st.rerun()


# ============================================================
# 7. REVIEW
# ============================================================
def show_review_note():
    render_sidebar("review")
    record = st.session_state.current_record
    if not record:
        go_to("dashboard"); st.rerun(); return
    is_approved = record.get("status") == "Approved"
    back, title, status = st.columns([1.15, 6.85, 2], vertical_alignment="center")
    if back.button("Back", key="review_back", use_container_width=True):
        go_to("dashboard"); st.rerun()
    title.markdown(f'<div class="page-heading">{escape(record.get("name", ""))}</div><div class="page-kicker">{escape(record.get("id", ""))} · {escape(str(record.get("age", "")))}y · {escape(record.get("gender", ""))} · {escape(record.get("doctor", "N/A"))}</div>', unsafe_allow_html=True)
    status.markdown(render_status_badge(record.get("status", "Pending")), unsafe_allow_html=True)
    if st.session_state.last_record_id != record.get("id"):
        st.session_state.edit_summary = record.get("summary") or record.get("patient_summary", "")
        st.session_state.last_record_id = record.get("id")
    left, right = st.columns([6.2, 3.8], gap="large")
    with left:
        with st.container(border=True):
            st.markdown('<div class="section-title">Transcript</div><div class="section-subtitle">Source conversation used for analysis</div>', unsafe_allow_html=True)
            with st.expander("View source conversation", expanded=True):
                st.text_area("Transcript", value=record.get("transcript", "No transcript available."), height=150, disabled=True, label_visibility="collapsed")

            soap_col, cds_col = st.columns(2)
            with soap_col:
                if not is_approved and st.button("Generate SOAP", type="primary", use_container_width=True, disabled=not record.get("transcript", "").strip()):
                    try:
                        with st.spinner("Generating SOAP note..."):
                            regenerated = HealthcareApiClient().regenerate_encounter(record["transcript"])
                        record["summary"] = regenerated.get("soap_note") or regenerated.get("patient_summary", "")
                        record["patient_summary"] = regenerated.get("patient_summary", "")
                        record["key_highlights"] = regenerated.get("key_highlights", [])
                        record["transcript"] = regenerated.get("transcript", record.get("transcript", ""))
                        for index, existing in enumerate(st.session_state.records):
                            if existing.get("id") == record.get("id"):
                                st.session_state.records[index] = record
                                break
                        save_db(st.session_state.records)
                        st.session_state.edit_summary = record["summary"]
                        st.success("SOAP note generated from the stored transcript.")
                        st.rerun()
                    except (RuntimeError, requests.exceptions.HTTPError) as error:
                        st.error(f"SOAP regeneration failed: {error}")
            with cds_col:
                if not is_approved and st.button("Generate CDS", type="secondary", use_container_width=True, disabled=not record.get("transcript", "").strip()):
                    try:
                        with st.spinner("Generating CDS recommendations..."):
                            regenerated = HealthcareApiClient().regenerate_cds(record)
                        recommendations = regenerated.get("recommendations")
                        if not recommendations or not str(recommendations).strip():
                            raise RuntimeError("The CDS service returned no recommendation. The existing recommendation was kept.")
                        record["retrieved_guidelines"] = regenerated.get("retrieved_guidelines", record.get("retrieved_guidelines", ""))
                        record["recommendations"] = recommendations
                        record["cds_requested"] = True
                        record["analysis_version"] = ANALYSIS_VERSION
                        for index, existing in enumerate(st.session_state.records):
                            if existing.get("id") == record.get("id"):
                                st.session_state.records[index] = record
                                break
                        save_db(st.session_state.records)
                        st.success("CDS recommendations generated.")
                        st.rerun()
                    except (RuntimeError, requests.exceptions.HTTPError) as error:
                        st.error(f"CDS regeneration failed: {error}")
        with st.container(border=True):
            st.markdown('<div class="section-title">Clinical Decision Support Recommendations</div><div class="section-subtitle">Evidence-based guidance for this encounter</div>', unsafe_allow_html=True)
            render_cds_recommendations(record.get("recommendations") or "Review the documented encounter and follow up based on clinical judgment.")
        with st.container(border=True):
            st.markdown('<div class="section-title">Relationship Graph</div><div class="section-subtitle">Clinical relationships detected in this encounter</div>', unsafe_allow_html=True)
            render_clinical_graph(record.get("name", "Patient"), st.session_state.detected_insights)
    with right:
        with st.container(border=True):
            subtitle = "Approved and locked" if is_approved else "Editable encounter summary"
            st.markdown(f'<div class="section-title">SOAP Note</div><div class="section-subtitle">{subtitle}</div>', unsafe_allow_html=True)
            render_summary(st.session_state.edit_summary)
            if not is_approved:
                with st.expander("Review and edit summary", expanded=False): st.text_area("Edit clinical summary", key="edit_summary", height=220, label_visibility="collapsed")
        with st.container(border=True):
            st.markdown('<div class="section-title">Detected Insights</div><div class="section-subtitle">Grouped clinical signals</div>', unsafe_allow_html=True)
            insights = st.session_state.detected_insights
            if insights:
                render_grouped_insights(insights)
            else:
                st.caption("No insights detected in this transcript.")
            st.caption("ICD-10 and CPT/HCPCS suggestions are available on the Codes page.")
            if st.button("Open Codes", key="review_open_codes", use_container_width=True):
                go_to("codes"); st.rerun()
        if not is_approved:
            with st.container(border=True):
                st.markdown('<div class="insight-heading">Add Clinical Insight</div>', unsafe_allow_html=True)
                for index in range(st.session_state.insight_count): st.selectbox(f"Insight {index + 1}", MASTER_INSIGHTS, key=f"insight_{index}", label_visibility="collapsed")
                add_col, push_col = st.columns(2)
                if add_col.button("Add Insight", use_container_width=True, disabled=st.session_state.insight_count >= 6):
                    st.session_state.insight_count += 1; st.rerun()
                def push_selected_insights_to_note():
                    selected = [st.session_state.get(f"insight_{i}", "") for i in range(st.session_state.insight_count)]
                    manual_insights = record.setdefault("manual_insights", [])
                    for insight in selected:
                        if insight != "None" and insight not in manual_insights:
                            manual_insights.append(insight)
                    st.session_state.edit_summary = append_selected_insights_to_note(st.session_state.edit_summary, selected)
                    st.session_state.detected_insights = get_record_insights(record)
                    for index, existing in enumerate(st.session_state.records):
                        if existing.get("id") == record.get("id"):
                            st.session_state.records[index] = record
                            break
                    save_db(st.session_state.records)
                    st.session_state.insight_count = 1
                push_col.button("Push to Note", type="primary", use_container_width=True, on_click=push_selected_insights_to_note)
    edited_df = render_transcript(record, read_only=is_approved)
    def persist(approved=False):
        if record.get("status") == "Approved":
            return
        record["summary"] = st.session_state.edit_summary
        record["transcript_data"] = edited_df.to_dict("records")
        if approved:
            record["status"] = "Approved"
            record["approval_date"] = datetime.now().strftime("%d/%m/%Y")
        for index, existing in enumerate(st.session_state.records):
            if existing.get("id") == record.get("id"):
                st.session_state.records[index] = record; break
        save_db(st.session_state.records)
    if record.get("status") == "Pending":
        st.divider()
        _, draft_col, final_col = st.columns([7, 1.5, 1.5], vertical_alignment="center")
        if draft_col.button("Save Draft", use_container_width=True): persist(False); st.success("Draft and audit trail updated.")
        if final_col.button("Finalize Note", type="primary", use_container_width=True): persist(True); go_to("dashboard"); st.rerun()


# ============================================================
# 8. CODES PAGE
# ============================================================
def show_codes():
    render_sidebar("codes")
    render_header("Codes", "ICD-10 diagnosis and CPT/HCPCS procedure code suggestions")

    records = st.session_state.records
    if not records:
        st.info("No patient encounters are available. Create a consultation before generating codes.")
        return

    record_lookup = {f'{r.get("name", "Unknown")} · {r.get("id", "No ID")}': r for r in records}
    current = st.session_state.current_record
    default_label = next((label for label, value in record_lookup.items() if current and value.get("id") == current.get("id")), list(record_lookup)[0])
    labels = list(record_lookup)
    selected_label = st.selectbox("Select patient encounter", labels, index=labels.index(default_label))
    record = record_lookup[selected_label]

    if not current or current.get("id") != record.get("id"):
        st.session_state.current_record = record
        st.session_state.detected_insights = get_record_insights(record)

    insights = get_record_insights(record)
    st.session_state.detected_insights = insights

    st.markdown(
        f'<div class="code-hero"><div class="code-hero-title">Medical Coding Workspace</div>'
        f'<div class="code-hero-text">Review machine-suggested codes before adding them to the clinical or billing workflow.</div>'
        f'<div class="code-patient"><b>{escape(record.get("name", ""))}</b> · {escape(record.get("id", ""))} · '
        f'{escape(str(record.get("age", "")))}y · {escape(record.get("gender", ""))} · {escape(record.get("doctor", "N/A"))}</div></div>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown('<div class="section-title">Detected Clinical Context</div><div class="section-subtitle">Items used to identify candidate codes</div>', unsafe_allow_html=True)
        if insights:
            render_grouped_insights(insights)
        else:
            st.warning("No codable insights were detected in this encounter transcript.")

        generate_col, review_col = st.columns([1, 1])
        if generate_col.button("Regenerate Code Suggestions", type="primary", use_container_width=True, disabled=not insights):
            with st.spinner("Matching ICD-10 and CPT/HCPCS codes..."):
                st.session_state.code_results = get_backend_code_suggestions(insights, record.get("transcript", ""))
                st.session_state.code_record_id = record.get("id")
                st.session_state.code_results_signature = get_code_results_signature(insights, record.get("transcript", ""))
                st.session_state.code_matcher_version = CODE_MATCHER_VERSION
            st.rerun()
        if review_col.button("Return to Clinical Review", use_container_width=True):
            go_to("review"); st.rerun()

    current_signature = get_code_results_signature(insights, record.get("transcript", ""))
    cached_results_are_stale = (
        st.session_state.code_record_id != record.get("id")
        or st.session_state.code_results_signature != current_signature
        or st.session_state.code_matcher_version != CODE_MATCHER_VERSION
    )
    if cached_results_are_stale:
        with st.spinner("Matching ICD-10 and CPT/HCPCS codes..."):
            st.session_state.code_results = get_backend_code_suggestions(insights, record.get("transcript", ""))
        st.session_state.code_record_id = record.get("id")
        st.session_state.code_results_signature = current_signature
        st.session_state.code_matcher_version = CODE_MATCHER_VERSION

    code_results = st.session_state.code_results
    code_error = st.session_state.get("code_generation_error", "")
    if code_error:
        st.warning(code_error)
    elif st.session_state.code_record_id == record.get("id"):
        total_matches = len(code_results.get("icd10", [])) + len(code_results.get("cpt", []))
        st.success(f"Code matching completed. {total_matches} suggestion(s) found.")

    unified_rows = [
        {
            "Documentation / service": item.get("Extracted Condition", ""),
            "Code type": "ICD-10-CM",
            "Code": item.get("ICD-10 Code", ""),
            "Purpose": item.get("Matched Disease/Injury", ""),
        }
        for item in code_results.get("icd10", [])
    ] + [
        {
            "Documentation / service": item.get("Extracted Procedure", ""),
            "Code type": "CPT/HCPCS",
            "Code": item.get("CPT/HCPCS Code", ""),
            "Purpose": item.get("Matched Procedure/Service", ""),
        }
        for item in code_results.get("cpt", [])
    ]
    with st.container(border=True):
        st.markdown('<div class="section-title">Unified Diagnosis and Procedure Codes</div><div class="section-subtitle">Quick review of the codes derived from this encounter.</div>', unsafe_allow_html=True)
        if unified_rows:
            st.dataframe(pd.DataFrame(unified_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No diagnosis or procedure codes are available for this encounter.")

    icd_tab, cpt_tab = st.tabs(["ICD-10 Diagnosis Codes", "CPT/HCPCS Procedure Codes"])
    with icd_tab:
        with st.container(border=True):
            st.markdown('<div class="section-title">Suggested ICD-10 Codes</div><div class="section-subtitle">Diagnosis code candidates based on detected diagnoses and symptoms</div>', unsafe_allow_html=True)
            render_code_results(code_results.get("icd10", []), "No ICD-10 suggestions are available. Generate codes or verify that code_matcher is configured.")
    with cpt_tab:
        with st.container(border=True):
            st.markdown('<div class="section-title">Suggested CPT/HCPCS Codes</div><div class="section-subtitle">Procedure and service code candidates based on detected tests and procedures</div>', unsafe_allow_html=True)
            render_code_results(code_results.get("cpt", []), "No CPT/HCPCS suggestions are available. Generate codes or verify that code_matcher is configured.")

        (
            billing_eligible_cpt_results,
            excluded_count,
            unverifiable_count,
            billing_rows,
        ) = prepare_billing_lines(
            code_results.get("cpt", []),
            record.get("transcript", ""),
            record.get("date"),
        )
        st.markdown("### Estimated CPT/HCPCS Billing Lines")
        st.caption(
            "These amounts are chargemaster-based estimates only and are not finalized claims, "
            "payer-allowed amounts, or patient responsibility amounts."
        )
        count_columns = st.columns(3)
        count_columns[0].metric("Billing-eligible CPT candidates", len(billing_eligible_cpt_results))
        count_columns[1].metric("Excluded non-performed candidates", excluded_count)
        count_columns[2].metric("Unverifiable performed status", unverifiable_count)

        if unverifiable_count:
            st.warning(
                f"{unverifiable_count} CPT candidate(s) were excluded because performed status could not be verified."
            )

        if billing_rows:
            unavailable_statuses = {
                "Chargemaster unavailable",
                "Unsupported format",
                "Missing required columns",
            }
            if any(row["Rate Source Status"] in unavailable_statuses for row in billing_rows):
                st.warning(
                    "Chargemaster is unavailable or invalid; coding suggestions remain available."
                )
            st.dataframe(pd.DataFrame(billing_rows), hide_index=True, width="stretch")
        else:
            st.info("No billing-eligible CPT candidates are available for chargemaster lookup.")

    st.caption("Coding suggestions require professional validation before billing, claim submission, or addition to the legal health record.")


# ============================================================
# 9. ROUTER
# ============================================================
if st.session_state.page == "dashboard":
    show_dashboard()
elif st.session_state.page == "new_consult":
    show_new_consultation()
elif st.session_state.page == "ehr_data":
    show_ehr_demo_data()
elif st.session_state.page == "review":
    show_review_note()
elif st.session_state.page == "codes":
    show_codes()
else:
    st.session_state.page = "dashboard"
    st.rerun()
