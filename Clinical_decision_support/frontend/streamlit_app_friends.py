import io
import json
import math
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

from frontend.chargemaster import load_chargemaster

if st.session_state.get("theme_defaults_version") != "light-default-v3":
    st.session_state.theme = "Light"
    st.session_state.theme_defaults_version = "light-default-v3"
theme_name = st.session_state.get("theme", "Light")
if theme_name == "Dark":
    theme_vars = """
    --bg: #1e1e1e; --surface: #292929; --surface-soft: #242424;
    --border: #3a3a3a; --border-strong: #4a4a4a; --text: #f5f5f5;
    --muted: #b8b8b8; --primary: #f59e0b; --primary-hover: #d97706;
    --success: #86efac; --success-soft: #164e2b; --warning: #fbbf24;
    --warning-soft: #5b4108; --danger: #fca5a5; --danger-soft: #5c2020;
    --info: #93c5fd; --info-soft: #1e3a5f; --purple: #d8b4fe;
    --sidebar-bg: #181818; --sidebar-border: #343434; --sidebar-text: #f5f5f5;
    --sidebar-muted: #b8b8b8; --sidebar-hover: #3a2a12;
    """
    header_background = "rgba(30, 30, 30, .98)"
else:
    theme_vars = """
    --bg: #f4f7f9; --surface: #ffffff; --surface-soft: #f8fafc;
    --border: #e2e8f0; --border-strong: #cbd5e1; --text: #17212b;
    --muted: #64748b; --primary: #148277; --primary-hover: #0f6f66;
    --success: #15803d; --success-soft: #dcfce7; --warning: #b45309;
    --warning-soft: #fef3c7; --danger: #b91c1c; --danger-soft: #fee2e2;
    --info: #2563eb; --info-soft: #dbeafe; --purple: #7c3aed;
    --sidebar-bg: #fcfcfc; --sidebar-border: #e5e7eb; --sidebar-text: #333333;
    --sidebar-muted: #6b7280; --sidebar-hover: #fff1ef;
    """
    header_background = "rgba(244, 247, 249, .96)"

try:
    from frontend.api_client import HealthcareApiClient
except ModuleNotFoundError:
    from api_client import HealthcareApiClient

try:
    from cpt_coder import (
        classify_medical_item,
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
            extract_performed_procedures,
            get_cpt_codes,
            get_cpt_candidate_sets,
            is_explicitly_performed,
        )
        from icd10_coder import get_icd10_candidate_sets, get_icd10_codes
    except ModuleNotFoundError:
        classify_medical_item = None
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
    page_title="NuuCare | Clinical Scribe",
    page_icon="AP",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items=None,
)

DB_FILE = PROJECT_ROOT / "clinical_records.json"
ANALYSIS_VERSION = "2"
CODE_MATCHER_VERSION = "4"


# ============================================================
# 2. THEME AND LAYOUT
# ============================================================
st.markdown(
    """
<style>
:root {
    {theme_vars}
}
#MainMenu, footer { visibility: hidden; }
html { font-size: 15px; }
.stApp {
    background: var(--bg);
    color: var(--text);
    font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    transition: none !important;
}
header[data-testid="stHeader"] {
    height: 3.25rem;
    background: {header_background};
    border-bottom: 1px solid var(--border);
    z-index: 1000;
}
button[data-testid="stSidebarCollapsedControl"],
[data-testid="stSidebarCollapseButton"] button,
[data-testid="collapsedControl"] button { z-index: 1100; }
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {
    color: var(--primary) !important;
    background: var(--surface) !important;
    border: 1px solid var(--primary) !important;
    border-radius: .45rem !important;
    opacity: 1 !important;
}
[data-testid="collapsedControl"] button,
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebarCollapseButton"] button {
    color: #4a4a4a !important;
    background: #f1f3f5 !important;
    border: 1px solid #d1d5db !important;
    border-radius: .45rem !important;
    opacity: 1 !important;
}
[data-testid="collapsedControl"] button:hover,
[data-testid="stSidebarCollapsedControl"] button:hover,
[data-testid="stSidebarCollapseButton"] button:hover {
    color: #333333 !important;
    background: #e5e7eb !important;
}
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="collapsedControl"] svg,
[data-testid="stSidebarCollapseButton"] svg {
    fill: #4a4a4a !important;
    color: #4a4a4a !important;
    stroke: #4a4a4a !important;
}
[data-testid="stAppViewContainer"] > .main { padding-top: 0; }
.block-container { max-width: 1440px; padding: 4.5rem 2rem 1.5rem !important; }
[data-testid="stVerticalBlock"] { gap: .7rem; }
[data-testid="stHorizontalBlock"] { gap: .75rem; }
[data-testid="stElementContainer"] { margin-bottom: 0; }
hr { margin: .25rem 0 .65rem !important; border-color: var(--border) !important; }

/* Sidebar */
[data-testid="stSidebar"] { background: var(--sidebar-bg); border-right: 1px solid var(--sidebar-border); }
[data-testid="stSidebar"] > div:first-child { padding: 1rem .85rem 1.25rem; }
[data-testid="stSidebar"] * { color: var(--sidebar-text) !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .3rem; }
[data-testid="stSidebar"] .stButton > button {
    min-height: 2.45rem; justify-content: flex-start; background: transparent !important;
    border: 1px solid transparent !important; box-shadow: none !important; pointer-events: auto !important; position: relative; z-index: 10;
}
[data-testid="stSidebar"] .stSelectbox,
[data-testid="stSidebar"] .stButton { pointer-events: auto !important; position: relative; z-index: 10; }
[data-testid="stSidebar"] .stButton > button:hover {
    background: var(--sidebar-hover) !important; border-color: var(--primary) !important;
}
.stSidebar [data-baseweb="select"],
[data-testid="stSidebar"] [data-baseweb="select"] {
    background: var(--surface) !important;
    border: 1px solid var(--sidebar-border) !important;
    border-radius: .45rem !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] [data-baseweb="select"] [role="combobox"] {
    background: var(--surface) !important;
    color: var(--sidebar-text) !important;
    border: 0 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] span,
[data-testid="stSidebar"] [data-baseweb="select"] input {
    color: var(--sidebar-text) !important;
    -webkit-text-fill-color: var(--sidebar-text) !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] svg {
    fill: var(--sidebar-text) !important;
    color: var(--sidebar-text) !important;
    stroke: var(--sidebar-text) !important;
    opacity: 1 !important;
}
[data-testid="stSidebar"] [data-baseweb="radio"] label,
[data-testid="stSidebar"] [role="radio"] { color: var(--sidebar-text) !important; }
.shell-logo { margin: .15rem 0 0; font-size: 1.55rem; font-weight: 800; color: var(--sidebar-text) !important; }
.shell-logo span { color: var(--primary) !important; }
.shell-subtitle { margin: .1rem 0 1.2rem; color: var(--sidebar-muted) !important; font-size: .75rem; }
.shell-section-label { margin: .65rem 0 .25rem; color: var(--sidebar-muted) !important; font-size: .64rem; font-weight: 800; letter-spacing: .11rem; }
.shell-divider { height: 1px; margin: .65rem 0; background: var(--sidebar-border); }
.shell-user { display: flex; align-items: center; gap: .65rem; margin-top: 1rem; padding: .7rem; border-top: 1px solid var(--sidebar-border); }
.shell-user b, .shell-user small { display: block; }
.shell-user small { color: var(--sidebar-muted) !important; font-size: .67rem; }
.user-avatar { display: grid; place-items: center; width: 2rem; height: 2rem; border-radius: 50%; background: var(--primary); color: #fff !important; font-size: .7rem; font-weight: 800; }
.nav-active-marker { height: 2px; margin: -.36rem .65rem .15rem; border-radius: 2px; background: var(--primary); }

/* Shared */
.page-heading { margin: 0; font-size: 1.65rem; line-height: 1.18; font-weight: 780; color: var(--text); }
.page-kicker { margin: .2rem 0 0; font-size: .78rem; color: var(--muted); }
.section-title { margin: 0; font-size: 1rem; line-height: 1.25; font-weight: 750; color: var(--text); }
.section-subtitle { margin: .12rem 0 .55rem; font-size: .72rem; color: var(--muted); }
.toolbar-label, .insight-heading { margin: .55rem 0 .2rem; color: var(--muted); font-size: .65rem; font-weight: 800; letter-spacing: .08rem; text-transform: uppercase; }
.section-gap { height: .45rem; }
.stButton > button { min-height: 2.4rem; border-radius: .48rem; font-weight: 650; }
[data-testid="stMain"] .stButton > button,
[data-testid="stAppViewContainer"] .main .stButton > button {
    background: var(--primary) !important; border: 1px solid var(--primary) !important;
    color: #fff !important; box-shadow: none !important;
}
[data-testid="stMain"] .stButton > button:hover,
[data-testid="stAppViewContainer"] .main .stButton > button:hover {
    background: var(--primary-hover) !important; border-color: var(--primary-hover) !important;
}
[data-testid="stMain"] .stButton > button:disabled { background: #94bdb8 !important; border-color: #94bdb8 !important; opacity: .72 !important; }
.stTextInput, .stSelectbox, .stDateInput, .stTextArea, .stRadio { margin-bottom: 0 !important; }
[data-baseweb="input"], [data-baseweb="select"] > div, textarea { border-radius: .45rem !important; }
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stSelectbox"] [data-baseweb="select"] * {
    background: var(--surface) !important;
    color: var(--text) !important;
}
[data-testid="stSelectbox"] [role="combobox"],
[data-testid="stSelectbox"] input,
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    background: var(--surface) !important;
    color: var(--text) !important;
    -webkit-text-fill-color: var(--text) !important;
    opacity: 1 !important;
    caret-color: var(--text) !important;
}
[data-testid="stTextArea"] textarea:disabled,
[data-testid="stTextArea"] textarea[disabled] {
    background: var(--surface-soft) !important;
    color: var(--text) !important;
    -webkit-text-fill-color: var(--text) !important;
    opacity: 1 !important;
}
[data-testid="stSelectbox"] [data-baseweb="popover"] * { color: var(--text) !important; }
[data-testid="stExpander"] summary,
[data-testid="stExpander"] details,
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    background: var(--surface) !important;
    color: var(--text) !important;
    border-color: var(--border) !important;
}
[data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"] {
    background: var(--surface) !important;
    color: var(--text) !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] [data-baseweb="select"] * {
    background: var(--surface) !important;
    color: var(--text) !important;
}
[data-testid="stSidebar"] [data-baseweb="popover"] * { color: var(--text) !important; }
[data-testid="stSidebar"] [data-baseweb="popover"],
[data-testid="stSidebar"] [role="listbox"] {
    background: var(--surface) !important;
    color: var(--sidebar-text) !important;
    border: 1px solid var(--sidebar-border) !important;
}
[data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li { color: var(--text); }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    --gdg-bg-cell: var(--surface);
    --gdg-bg-cell-medium: var(--surface);
    --gdg-bg-header: var(--surface);
    --gdg-bg-header-hovered: var(--surface-soft);
    --gdg-bg-header-hasFocus: var(--surface-soft);
    --gdg-text-dark: var(--text);
    --gdg-text-medium: var(--text);
    --gdg-text-light: var(--muted);
    --gdg-text-header: var(--text);
    --gdg-text-header-selected: var(--text);
    --gdg-border-color: var(--border);
    --gdg-horizontal-border-color: var(--border);
    --gdg-link-color: var(--primary);
    background: var(--surface) !important;
    border-color: var(--border) !important;
}
[data-testid="stTabs"] [role="tab"] {
    color: var(--muted) !important;
    background: transparent !important;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: var(--primary) !important;
    border-bottom-color: var(--primary) !important;
}
div[data-testid="stVerticalBlockBorderWrapper"] { background: var(--surface); border-color: var(--border) !important; border-radius: .7rem !important; box-shadow: 0 1px 3px rgba(15,23,42,.05); }
div[data-testid="stVerticalBlockBorderWrapper"] > div { padding: .85rem 1rem !important; }

/* Dashboard */
.kpi-card { min-height: 7rem; padding: .85rem 1rem; background: var(--surface); border: 1px solid var(--border); border-top: 3px solid var(--primary); border-radius: .65rem; box-shadow: 0 1px 3px rgba(15,23,42,.05); }
.kpi-title { color: var(--muted); font-size: .65rem; font-weight: 800; text-transform: uppercase; }
.kpi-value { margin-top: .28rem; color: var(--text); font-size: 1.8rem; line-height: 1; font-weight: 790; }
.kpi-context { margin-top: .45rem; color: var(--muted); font-size: .7rem; }
.overview-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: .65rem; padding: 1rem; background: var(--surface); border: 1px solid var(--border); border-radius: .65rem; }
.overview-grid div { padding-left: .6rem; border-left: 2px solid var(--border-strong); }
.overview-grid small { display:block; color:var(--muted); font-size:.66rem; }
.overview-grid b { display:block; margin-top:.25rem; font-size:1.45rem; }
.amber-text { color:var(--warning); } .green-text { color:var(--success); } .blue-text { color:var(--info); }

/* Table */
.patient-table-header { display:grid; grid-template-columns:2.2fr 1.15fr 1.7fr 1.55fr 1.05fr 1.15fr; min-width:790px; padding:.45rem .5rem; border-bottom:1px solid var(--border-strong); color:var(--muted); font-size:.72rem; font-weight:800; text-transform:uppercase; }
.patient-cell, .table-two-line { display:flex; min-height:2.45rem; flex-direction:column; justify-content:center; gap:.2rem; line-height:1.15; }
.patient-cell b { overflow-wrap:anywhere; font-size:.86rem; }
.patient-cell small, .table-two-line small, .table-meta { color:var(--muted); font-size:.71rem; }
.status-pill { display:inline-flex; align-items:center; justify-content:center; padding:.28rem .52rem; border-radius:999px; font-size:.67rem; font-weight:800; }
.status-pending { color:var(--warning); background:var(--warning-soft); }
.status-approved { color:var(--success); background:var(--success-soft); }
.table-row-rule { height:1px; background:var(--border); }
.pagination-label { padding-top:.55rem; color:var(--muted); font-size:.7rem; text-align:center; }

/* Review and graph */
.workflow-steps { display:flex; flex-wrap:wrap; margin:.25rem 0 .55rem; color:var(--muted); font-size:.72rem; }
.workflow-steps span, .workflow-steps b { padding:.45rem 1rem; border-bottom:2px solid var(--border); }
.workflow-steps b { color:var(--primary); border-color:var(--primary); }
.recording-prompt { margin-top:.25rem; padding:.65rem; text-align:center; color:var(--muted); background:var(--surface-soft); border:1px dashed var(--border-strong); border-radius:.5rem; font-size:.72rem; }
.audit-note { padding-bottom:.3rem; color:var(--muted); font-size:.68rem; }
.summary-text { margin:.12rem 0 .55rem; white-space:pre-wrap; color:var(--text); font-size:.76rem; line-height:1.45; }
.insight-scroll { max-height:16rem; overflow-y:auto; padding-right:.2rem; }
.insight-category { margin:.55rem 0; }
.insight-category-heading { margin-bottom:.28rem; color:var(--muted); font-size:.64rem; font-weight:800; text-transform:uppercase; }
.insight-category-items { display:flex; flex-wrap:wrap; gap:.32rem; }
.insight-chip { display:inline-flex; padding:.32rem .5rem; border:1px solid var(--border); border-radius:999px; background:transparent; }
.insight-chip b { font-size:.68rem; }
.insight-diagnosis { background:transparent; border-color:#fecaca; }
.insight-symptom { background:transparent; border-color:#fcd34d; }
.insight-risk { background:transparent; border-color:#fdba74; }
.insight-procedure { background:transparent; border-color:#93c5fd; }
.clinical-graph-v2 { padding:1rem; background:var(--surface); border:1px solid var(--border); border-radius:.65rem; }
.graph-hierarchy-v2 { display:flex; flex-direction:column; align-items:center; gap:.6rem; }
.graph-patient-card { max-width:80%; padding:.48rem .8rem; border-radius:.48rem; background:var(--primary); color:#fff; font-size:.73rem; font-weight:750; text-align:center; }
.graph-connector-main { width:2px; height:.8rem; background:var(--border-strong); }
.graph-categories { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); width:100%; gap:.5rem; }
.graph-category-header { margin-bottom:.35rem; color:var(--muted); font-size:.58rem; font-weight:800; text-align:center; text-transform:uppercase; }
.graph-category-content { display:flex; flex-direction:column; gap:.3rem; }
.graph-node-card, .graph-empty-state { padding:.52rem .4rem; border-radius:.42rem; font-size:.68rem; line-height:1.25; text-align:center; overflow-wrap:anywhere; }
.graph-node-diagnosis { background:var(--purple); color:#fff; }
.graph-node-symptom { background:var(--info); color:#fff; }
.graph-node-risk { background:var(--warning); color:#fff; }
.graph-empty-state { color:var(--muted); border:1px dashed var(--border-strong); }
.code-table-wrap { overflow-x:auto; border:1px solid var(--border); border-radius:.55rem; background:#fff; }
.code-table { width:100%; border-collapse:collapse; background:#fff; color:#17212b; font-size:.78rem; }
.code-table th { padding:.65rem .55rem; background:#f1f5f9; color:#334155; border-bottom:1px solid #cbd5e1; text-align:left; font-weight:750; }
.code-table td { padding:.65rem .55rem; background:#fff; color:#17212b; border-bottom:1px solid #e2e8f0; }
.code-table tr:last-child td { border-bottom:0; }

/* Codes */
.code-hero { padding:1rem 1.1rem; background:linear-gradient(135deg,#ecfdf5,#f0fdfa); border:1px solid #99f6e4; border-radius:.7rem; }
.code-hero-title { font-size:1rem; font-weight:780; color:#115e59; }
.code-hero-text { margin-top:.25rem; color:#47706d; font-size:.75rem; }
.code-patient { color:var(--text) !important; }
.code-patient { margin-top:.65rem; font-size:.78rem; color:var(--text); }
.code-empty { padding:1.2rem; text-align:center; color:var(--muted); background:var(--surface-soft); border:1px dashed var(--border-strong); border-radius:.55rem; }

@media (max-width:760px) {
    .block-container { padding:3.8rem .75rem .75rem !important; }
    .page-heading { font-size:1.35rem; }
    .overview-grid { grid-template-columns:1fr; }
    .graph-node-card, .graph-empty-state { font-size:.56rem; }
}

[data-testid="collapsedControl"] {
    color: #ffffff !important;
    background-color: #111111 !important;
    border: 1px solid #111111 !important;
    border-radius: 4px !important;
    opacity: 1 !important;
}
[data-testid="collapsedControl"] button {
    color: #ffffff !important;
    background-color: #111111 !important;
    border: 0 !important;
    min-width: 2.25rem !important;
    min-height: 2.25rem !important;
    opacity: 1 !important;
}
[data-testid="collapsedControl"] svg {
    fill: #ffffff !important;
    color: #ffffff !important;
    stroke: #ffffff !important;
    opacity: 1 !important;
}
[data-testid="stSidebarCollapsedControl"],
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] button {
    background: #111111 !important;
    color: #ffffff !important;
    border: 1px solid #111111 !important;
    opacity: 1 !important;
}
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="stSidebarCollapsedControl"] svg path,
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapseButton"] svg path,
[data-testid="collapsedControl"] svg path {
    fill: #ffffff !important;
    color: #ffffff !important;
    stroke: #ffffff !important;
    opacity: 1 !important;
}
/* Open sidebar: light control with a dark collapse icon. */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] button {
    background: #f0f2f6 !important;
    color: #333333 !important;
    border: 1px solid #d3d3d3 !important;
    border-radius: 4px !important;
    opacity: 1 !important;
}
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapseButton"] svg path {
    fill: #333333 !important;
    color: #333333 !important;
    stroke: #333333 !important;
    opacity: 1 !important;
}
[data-testid="stSidebar"] {
    background-color: #f8f9fa !important;
    border-right: 1px solid #e9ecef !important;
}
[data-testid="stSidebar"] * {
    color: #2d3748 !important;
}
[data-testid="stSidebar"] hr {
    border-color: #e2e8f0 !important;
}
[data-testid="stSidebar"] .stButton > button:hover,
[data-testid="stSidebar"] [aria-current="page"] {
    color: #0f766e !important;
    background-color: #e6fffb !important;
    border-color: #99f6e4 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] {
    background-color: #ffffff !important;
    border: 1px solid #d3d3d3 !important;
    border-radius: 4px !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background-color: #ffffff !important;
    color: #333333 !important;
    border-color: #d3d3d3 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] span,
[data-testid="stSidebar"] [data-baseweb="select"] input {
    color: #333333 !important;
    -webkit-text-fill-color: #333333 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] svg {
    fill: #333333 !important;
    color: #333333 !important;
    stroke: #333333 !important;
    opacity: 1 !important;
}
[data-testid="stSidebar"] [data-baseweb="popover"] > div,
[data-testid="stSidebar"] [data-baseweb="popover"],
[data-testid="stSidebar"] [role="listbox"] {
    background-color: #ffffff !important;
    color: #333333 !important;
    border-color: #d3d3d3 !important;
}
[data-testid="stSidebar"] [data-baseweb="popover"] *,
[data-testid="stSidebar"] [role="option"] {
    color: #333333 !important;
}
/* Streamlit may render the open select menu in a portal outside the sidebar. */
body [role="listbox"],
body [role="listbox"] [role="option"],
body [data-baseweb="popover"],
body [data-baseweb="popover"] > div,
body .react-aria-Popover,
body .react-aria-ListBox,
body .react-aria-ListBox [role="option"] {
    background: #ffffff !important;
    color: #333333 !important;
    border-color: #d3d3d3 !important;
}
body [role="listbox"] *,
body [data-baseweb="popover"] *,
body .react-aria-Popover *,
body .react-aria-ListBox * {
    color: #333333 !important;
    -webkit-text-fill-color: #333333 !important;
}
[data-testid="stSidebar"] .react-aria-ComboBox,
[data-testid="stSidebar"] .react-aria-ComboBox [role="group"] {
    background: #ffffff !important;
    border: 1px solid #d3d3d3 !important;
    border-radius: 4px !important;
    color: #333333 !important;
}
[data-testid="stSidebar"] .react-aria-ComboBox input,
[data-testid="stSidebar"] .react-aria-ComboBox input[role="combobox"] {
    background: #ffffff !important;
    color: #333333 !important;
    -webkit-text-fill-color: #333333 !important;
    border: 0 !important;
    box-shadow: none !important;
}
[data-testid="stSidebar"] .react-aria-ComboBox button,
[data-testid="stSidebar"] .react-aria-ComboBox button[aria-label="Open"] {
    background: #ffffff !important;
    color: #333333 !important;
    border: 0 !important;
    box-shadow: none !important;
    opacity: 1 !important;
}
[data-testid="stSidebar"] .react-aria-ComboBox button svg {
    fill: #333333 !important;
    color: #333333 !important;
    stroke: #333333 !important;
    opacity: 1 !important;
}
[data-testid="stSidebar"] [role="listbox"] {
    background: #ffffff !important;
    color: #333333 !important;
    border: 1px solid #d3d3d3 !important;
}
</style>
""".replace("{theme_vars}", theme_vars).replace("{header_background}", header_background),
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
    with DB_FILE.open("w", encoding="utf-8") as file:
        json.dump({"records": records}, file, indent=4, ensure_ascii=False)


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
        "chest x ray": "Chest X-ray", "ct scan": "CT Scan", "mri": "MRI",
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
        documentation_text = str(documentation or "").lower()
        if "chest x-ray" in documentation_text or "chest x ray" in documentation_text:
            has_multiple_views = any(
                phrase in documentation_text
                for phrase in ("1-2 view", "1 to 2 view", "two view", "2 view")
            )
            if has_multiple_views:
                cpt_items = [
                    item for item in cpt_items
                    if "chest x-ray" not in item.lower() and "chest x ray" not in item.lower()
                ]
                cpt_items.append("Chest x ray 2 views")
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
            # Keep procedure candidates visible for clinician review. Whether a
            # service was actually performed must be confirmed before billing.
            cpt_details = get_cpt_candidate_sets(cpt_items, documentation, threshold=80)
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
    """Generate codes locally without an avoidable HTTP round trip."""
    return get_code_suggestions(insights, documentation)



# ============================================================
# 4. REUSABLE COMPONENTS
# ============================================================
def go_to(page):
    if page == "codes" or page != st.session_state.page:
        st.session_state.generated_bill = None
        st.session_state.generated_bill_record_id = None
        st.session_state.generated_bill_patient_id = None
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
            '<div class="shell-logo">NuuCare</div>'
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
        selected_theme = st.selectbox("Theme", ["Light", "Dark"], key="theme_selector_v2")
        if st.session_state.get("theme") != selected_theme:
            st.session_state.theme = selected_theme
            st.rerun()
        st.button("Settings", key="nav_settings", use_container_width=True)
        st.button("Help", key="nav_help", use_container_width=True)
        st.markdown(
            '<div class="shell-user"><div class="user-avatar">AP</div>'
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
        columns = list(values[0].keys())
        header = "".join(f"<th>{escape(str(column))}</th>" for column in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(row.get(column, "")))}</td>" for column in columns) + "</tr>"
            for row in values
        )
        st.markdown(
            f'<div class="code-table-wrap"><table class="code-table"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )
    else:
        for value in values if isinstance(values, list) else [values]:
            st.markdown(f"- {escape(str(value))}")


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
def show_patient_data():
    render_sidebar("ehr_data")
    render_header("EHR Demo Data", "Synthetic U.S. patient records and hospital charges", show_new=False)
    client = HealthcareApiClient()
    try:
        payload = client.export_patient_data()
        patients = payload.get("sheets", {}).get("Patient_Master", {}).get("records", [])
    except (RuntimeError, requests.exceptions.RequestException, OSError, ValueError) as error:
        st.error(f"Unable to load Epic patient data from the backend: {error}")
        return

    sheets = payload.get("sheets", {})
    st.caption(f"{len(patients)} synthetic patient chart(s) loaded from the backend. Changes are persisted in Epic_Inspired_USA_50_Patient_Demo.json.")
    data_tab, bills_tab, chargemaster_tab = st.tabs(["Patient Data", "Bill History", "Original Chargemaster"])
    with data_tab:
        with st.expander("Add patient", expanded=False):
            with st.form("add_demo_patient_form"):
                first_col, last_col = st.columns(2)
                first_name = first_col.text_input("First name")
                last_name = last_col.text_input("Last name")
                dob_col, sex_col = st.columns(2)
                dob = dob_col.date_input("Date of birth", value=datetime(1980, 1, 1).date())
                sex_at_birth = sex_col.selectbox("Sex at birth", ["Female", "Male", "Other"])
                city_col, state_col = st.columns(2)
                city = city_col.text_input("City")
                state = state_col.text_input("State")
                add_patient = st.form_submit_button("Add patient", type="primary")
            if add_patient:
                try:
                    created = client.create_patient(
                        {
                            "First_Name": first_name,
                            "Last_Name": last_name,
                            "DOB": dob.isoformat(),
                            "Sex_at_Birth": sex_at_birth,
                            "City": city,
                            "State": state,
                        }
                    )
                    st.session_state["ehr_demo_patient"] = f'{created["Patient_ID"]} · {created["Legal_Name"]}'
                    st.success(f'Added {created["Legal_Name"]} ({created["Patient_ID"]}).')
                    st.rerun()
                except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                    st.error(f"Patient could not be added: {error}")
        if not patients:
            st.info("No patient records are available.")
            return
        patient_options = {
            f'{patient.get("Patient_ID", "")} · {patient.get("Legal_Name", "Unknown patient")}': patient
            for patient in patients
        }
        selected_label = st.selectbox("Patient", list(patient_options), key="ehr_demo_patient")
        selected_patient = patient_options[selected_label]
        patient_id = str(selected_patient.get("Patient_ID", ""))
        try:
            chart = client.get_patient_chart(patient_id)
        except (RuntimeError, requests.exceptions.RequestException) as error:
            st.error(f"Patient chart could not be loaded: {error}")
            return
        patient_sheets = list(chart.get("records", {}))
        encounter_count = len(chart.get("records", {}).get("Encounters", []))
        problem_count = len(chart.get("records", {}).get("Problems", []))
        medication_count = len(chart.get("records", {}).get("Medications", []))
        metric_columns = st.columns(4)
        metric_columns[0].metric("Patient ID", patient_id)
        metric_columns[1].metric("Encounters", encounter_count)
        metric_columns[2].metric("Problems", problem_count)
        metric_columns[3].metric("Medications", medication_count)

        selected_sheet = st.selectbox(
            "Record type",
            patient_sheets,
            format_func=lambda name: name.replace("_", " "),
            key=f"ehr_data_sheet_{patient_id}",
        )
        patient_rows = chart.get("records", {}).get(selected_sheet, [])
        frame = pd.DataFrame(patient_rows, columns=chart.get("headers", {}).get(selected_sheet, []))
        edited_frame = st.data_editor(
            frame,
            key=f"ehr_editor_{patient_id}_{selected_sheet}",
            use_container_width=True,
            hide_index=True,
            num_rows="fixed" if selected_sheet == "Patient_Master" else "dynamic",
            column_config={
                "Patient_ID": st.column_config.TextColumn("Patient ID", disabled=True)
            },
        )
        save_column, download_column = st.columns([1, 1])
        if save_column.button("Save changes to JSON", type="primary", key=f"ehr_save_{patient_id}_{selected_sheet}"):
            edited_records = json.loads(edited_frame.to_json(orient="records", date_format="iso"))
            try:
                if selected_sheet == "Patient_Master":
                    values = {key: value for key, value in edited_records[0].items() if key != "Patient_ID"}
                    client.update_patient(patient_id, values)
                else:
                    client.replace_patient_records(patient_id, selected_sheet, edited_records)
                st.success(f"Saved {selected_sheet.replace('_', ' ')} for {patient_id} through the backend.")
                st.rerun()
            except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                st.error(f"Changes could not be saved: {error}")
        download_column.download_button(
            "Download current JSON",
            data=json.dumps(client.export_patient_data(), indent=2, ensure_ascii=False),
            file_name="Epic_Inspired_USA_50_Patient_Demo.json",
            mime="application/json",
            key=f"ehr_download_{patient_id}_{selected_sheet}",
        )
        with st.expander("View edited rows as JSON"):
            st.json(json.loads(edited_frame.to_json(orient="records", date_format="iso")))
        with st.expander("Delete patient and linked records"):
            st.warning("Deleting a patient also removes that patient's encounters, problems, medications, bills, and other linked demo rows.")
            confirm_delete = st.checkbox("Confirm deletion", key=f"confirm_delete_{patient_id}")
            if st.button("Delete patient", disabled=not confirm_delete, key=f"delete_patient_{patient_id}"):
                try:
                    client.delete_patient(patient_id)
                    st.session_state.pop("ehr_demo_patient", None)
                    st.success(f"Deleted {selected_patient.get('Legal_Name', patient_id)}.")
                    st.rerun()
                except (RuntimeError, requests.exceptions.RequestException) as error:
                    st.error(f"Patient could not be deleted: {error}")

    with bills_tab:
        generated_bills = sheets.get("Generated_Bills", {}).get("records", [])
        historical_claims = sheets.get("Billing_Claims", {}).get("records", [])
        patient_names = {
            str(patient.get("Patient_ID", "")): str(patient.get("Legal_Name", "Unknown patient"))
            for patient in patients
        }
        generated_bill_ids = {str(item.get("Bill_ID", "")) for item in generated_bills}
        bill_history = []
        for bill in generated_bills:
            bill_history.append(
                {
                    "Record": bill.get("Bill_ID", ""),
                    "Record Type": "Generated Bill",
                    "Patient": patient_names.get(str(bill.get("Patient_ID", "")), "Unknown patient"),
                    "Patient ID": bill.get("Patient_ID", ""),
                    "Encounter ID": bill.get("Encounter_ID", ""),
                    "Date": bill.get("Generated_At", ""),
                    "Description": f'{bill.get("Encounter_Type", "Facility")} facility bill',
                    "Gross Charge (USD)": bill.get("Gross_Total_USD", 0),
                    "Status": bill.get("Bill_Status") or "Draft",
                    "Bill ID": bill.get("Bill_ID", ""),
                }
            )
        for claim in historical_claims:
            claim_id = str(claim.get("Claim_ID", ""))
            if any(claim_id.startswith(f'CLM-{bill_id.removeprefix("BILL-")}-') for bill_id in generated_bill_ids):
                continue
            bill_history.append(
                {
                    "Record": claim_id,
                    "Record Type": "Historical Claim",
                    "Patient": patient_names.get(str(claim.get("Patient_ID", "")), "Unknown patient"),
                    "Patient ID": claim.get("Patient_ID", ""),
                    "Encounter ID": claim.get("Encounter_ID", ""),
                    "Date": claim.get("Service_Date", ""),
                    "Description": claim.get("Description", ""),
                    "Gross Charge (USD)": claim.get("Charge_USD", 0),
                    "Status": claim.get("Claim_Status", ""),
                    "Bill ID": "",
                }
            )

        if not bill_history:
            st.info("No historical claims or generated bills are available.")
        else:
            search_col, patient_col, status_col = st.columns([4, 2, 2])
            bill_search = search_col.text_input(
                "Search bills",
                placeholder="Patient, encounter, code, or service",
                key="nuucare_bill_history_search",
            ).strip().lower()
            patient_ids_for_bills = sorted(patient_names, key=lambda patient_id: patient_names[patient_id].lower())
            selected_bill_patient = patient_col.selectbox(
                "Patient filter",
                ["All patients", *patient_ids_for_bills],
                format_func=lambda patient_id: (
                    "All patients"
                    if patient_id == "All patients"
                    else f"{patient_names[patient_id]} · {patient_id}"
                ),
                key="bill_history_patient_filter",
            )
            statuses = sorted({str(item.get("Status", "")) for item in bill_history if item.get("Status")})
            selected_status = status_col.selectbox(
                "Status filter",
                ["All statuses", *statuses],
                key="nuucare_bill_history_status",
            )
            filtered_bills = [
                item for item in bill_history
                if (selected_bill_patient == "All patients" or item.get("Patient ID") == selected_bill_patient)
                and (selected_status == "All statuses" or item.get("Status") == selected_status)
                and (not bill_search or bill_search in " ".join(str(value) for value in item.values()).lower())
            ]
            visible_columns = [column for column in bill_history[0] if column != "Bill ID"]
            history_frame = pd.DataFrame(filtered_bills, columns=visible_columns)
            st.caption(f"Showing {len(filtered_bills)} historical bill or claim record(s) across all patients.")
            st.dataframe(history_frame, use_container_width=True, hide_index=True)
            st.download_button(
                "Download bill history CSV",
                data=history_frame.to_csv(index=False),
                file_name="nuucare_bill_history.csv",
                mime="text/csv",
                disabled=history_frame.empty,
            )
            bill_detail_options = {
                f'{item["Record Type"]} · {item["Record"]} · {item["Patient"]}': item
                for item in filtered_bills
            }
            if bill_detail_options:
                detail_label = st.selectbox("Bill detail", list(bill_detail_options), key="bill_history_detail")
                detail = bill_detail_options[detail_label]
                if detail.get("Bill ID"):
                    bill_lines = [
                        line for line in sheets.get("Generated_Bill_Lines", {}).get("records", [])
                        if line.get("Bill_ID") == detail["Bill ID"]
                    ]
                    if bill_lines:
                        st.dataframe(pd.DataFrame(bill_lines), use_container_width=True, hide_index=True)
                    else:
                        st.info("This bill has no saved charge-line detail.")
                else:
                    st.dataframe(pd.DataFrame([detail]), use_container_width=True, hide_index=True)

    with chargemaster_tab:
        source = PROJECT_ROOT.parent / "Original_Hospital_IPD_OPD_Charges_200_Demo.xlsx"
        st.caption(f"Billing uses the IPD Encounter and CDM Master sheets in {source.name}; OPD CPT rates are also available.")
        try:
            charges = load_chargemaster()
        except (OSError, ValueError, ImportError) as error:
            st.error(f"Unable to load the original chargemaster: {error}")
        else:
            if charges.empty:
                st.info("No CPT/HCPCS-priced lines were found in the original chargemaster.")
            else:
                display_charges = charges.rename(
                    columns={
                        "code": "CPT/HCPCS",
                        "rate": "Unit Charge (USD)",
                        "quantity": "Quantity",
                        "service_date": "Service Date",
                    }
                )
                st.dataframe(display_charges, use_container_width=True, hide_index=True)


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
            ).properties(height=220).configure(
                background="#ffffff" if theme_name == "Light" else "#1e293b"
            ).configure_view(
                strokeWidth=0
            ).configure_axis(
                labelColor="#17212b" if theme_name == "Light" else "#e5e7eb",
                titleColor="#17212b" if theme_name == "Light" else "#e5e7eb",
                gridColor="#e2e8f0" if theme_name == "Light" else "#334155",
            ).configure_legend(
                labelColor="#17212b" if theme_name == "Light" else "#e5e7eb",
                titleColor="#17212b" if theme_name == "Light" else "#e5e7eb",
            )
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
def has_first_and_last_name(name):
    return len(str(name or "").split()) >= 2


def register_consultation_in_ehr(record, client=None, patient=None, dob=None):
    client = client or HealthcareApiClient()
    patient = patient or {}
    patient_id = str(record.get("patient_id", "") or patient.get("Patient_ID", ""))
    if not patient_id:
        patient_name = str(record.get("name", "")).strip()
        if not has_first_and_last_name(patient_name):
            raise ValueError("Enter the patient's first and last name before saving to Epic.")
        first_name, last_name = patient_name.split(maxsplit=1)
        if not dob:
            try:
                approximate_age = max(0, int(record.get("age", "")))
            except (TypeError, ValueError) as error:
                raise ValueError("A date of birth is required to add this patient to Epic.") from error
            dob = f"{max(1900, datetime.now().year - approximate_age)}-01-01"
        patient = client.create_patient(
            {
                "First_Name": first_name,
                "Last_Name": last_name,
                "DOB": dob,
                "Sex_at_Birth": record.get("gender", "Other"),
            }
        )
        patient_id = str(patient["Patient_ID"])

    record["patient_id"] = patient_id
    record["mrn"] = patient.get("MRN", record.get("mrn", ""))
    record["dob"] = patient.get("DOB", dob or record.get("dob", ""))
    patient_age = patient.get("Age")
    if isinstance(patient_age, (int, float)) or str(patient_age).isdigit():
        record["age"] = str(patient_age)
    try:
        service_date = datetime.strptime(record.get("date", ""), "%d/%m/%Y").date().isoformat()
    except ValueError:
        service_date = datetime.now().date().isoformat()
    if not record.get("ehr_encounter_id"):
        encounter = client.create_patient_record(
            patient_id,
            "Encounters",
            {
                "Date": service_date,
                "Type": "Office Visit",
                "Department": "Primary Care",
                "Provider": record.get("doctor", "Unassigned"),
                "Facility": "Northstar Medical Center",
                "Status": "Completed",
                "Primary_Diagnosis": record.get("patient_summary", ""),
                "Priority": "Routine",
            },
        )
        record["ehr_encounter_id"] = encounter["Encounter_ID"]
    if not record.get("ehr_note_id"):
        note = client.create_patient_record(
            patient_id,
            "Clinical_Notes",
            {
                "Encounter_ID": record["ehr_encounter_id"],
                "Note_Type": "SOAP",
                "Service_Date": service_date,
                "Author": record.get("doctor", "Unassigned"),
                "Note_Summary": record.get("summary") or record.get("patient_summary", ""),
                "Status": "Draft" if record.get("status") == "Pending" else "Signed",
            },
        )
        record["ehr_note_id"] = note["Note_ID"]
    return record


def update_ehr_clinical_note(record, approved=False):
    patient_id = record.get("patient_id")
    note_id = record.get("ehr_note_id")
    if not patient_id or not note_id:
        return
    client = HealthcareApiClient()
    chart = client.get_patient_chart(patient_id)
    notes = chart.get("records", {}).get("Clinical_Notes", [])
    for note in notes:
        if note.get("Note_ID") == note_id:
            note["Note_Summary"] = record.get("summary", "")
            note["Status"] = "Signed" if approved else "Draft"
            break
    client.replace_patient_records(patient_id, "Clinical_Notes", notes)


def show_new_consultation():
    render_sidebar("new_consult")
    render_header("New Consultation", "Capture patient context and generate clinical documentation", show_new=False)
    st.markdown('<div class="workflow-steps"><b>01 Patient</b><span>02 Record</span><span>03 Generate</span><span>04 Review</span></div>', unsafe_allow_html=True)
    if st.button("Back to Dashboard"):
        go_to("dashboard"); st.rerun()
    with st.container(border=True):
        st.markdown('<div class="section-title">Patient Information</div><div class="section-subtitle">Enter the basic context for this consultation</div>', unsafe_allow_html=True)
        try:
            demo_patients = HealthcareApiClient().list_patients()
        except (RuntimeError, requests.exceptions.RequestException):
            demo_patients = []
        demo_patient_options = {
            f'{patient.get("Patient_ID", "")} · {patient.get("Legal_Name", "Unknown patient")}': patient
            for patient in demo_patients
        }
        patient_source = st.selectbox(
            "Patient source",
            ["Manual entry", *demo_patient_options],
            key="consult_patient_source",
        )
        selected_demo_patient = demo_patient_options.get(patient_source)
        patient_widget_key = str(selected_demo_patient.get("Patient_ID")) if selected_demo_patient else "manual"
        default_name = str(selected_demo_patient.get("Legal_Name", "")) if selected_demo_patient else ""
        default_age = ""
        default_dob = ""
        if selected_demo_patient:
            try:
                default_dob = str(selected_demo_patient.get("DOB", "")).split("T")[0]
                birth_date = datetime.fromisoformat(default_dob).date()
                today = datetime.now().date()
                default_age = str(today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day)))
            except ValueError:
                pass
        gender_options = ["Male", "Female", "Other"]
        default_gender = str(selected_demo_patient.get("Sex_at_Birth", "Other")) if selected_demo_patient else "Male"
        gender_index = gender_options.index(default_gender) if default_gender in gender_options else 2
        if selected_demo_patient:
            st.caption("Using a synthetic U.S. demo patient. Patient ID and MRN will be linked to the consultation.")
        left, right = st.columns(2, gap="large")
        patient_name = left.text_input("Full Name", value=default_name, key=f"consult_patient_name_{patient_widget_key}", placeholder="e.g. John Doe")
        name_is_valid = bool(selected_demo_patient) or has_first_and_last_name(patient_name)
        if not selected_demo_patient and patient_name.strip() and not name_is_valid:
            st.info("Enter both the patient's first and last name, or select an existing EHR patient.")
        patient_age = right.text_input("Age", value=default_age, key=f"consult_patient_age_{patient_widget_key}")
        patient_gender = left.selectbox("Gender", gender_options, index=gender_index, key=f"consult_patient_gender_{patient_widget_key}")
        patient_dob = left.text_input("Date of birth (YYYY-MM-DD)", value=default_dob, key=f"consult_patient_dob_{patient_widget_key}", disabled=bool(selected_demo_patient), placeholder="Required for a new Epic patient")
        doctor_name = right.text_input("Attending Doctor", placeholder="e.g. Dr. Sarah Jenkins")
    with st.container(border=True):
        st.markdown('<div class="section-title">Record Consultation</div><div class="section-subtitle">Capture the clinical conversation securely</div>', unsafe_allow_html=True)
        audio_file = st.audio_input("Record Audio", label_visibility="collapsed")
        st.markdown('<div class="recording-prompt">Audio input ready when you are</div>', unsafe_allow_html=True)
        if st.button(
            "Transcribe and Generate Summary",
            type="primary",
            use_container_width=True,
            disabled=audio_file is None or not patient_name.strip() or not name_is_valid,
        ):
            transcript = ""
            try:
                with st.spinner("Transcribing audio..."):
                    recognizer = sr.Recognizer()
                    with sr.AudioFile(io.BytesIO(audio_file.getvalue())) as source:
                        transcript = recognizer.recognize_google(recognizer.record(source))
                with st.spinner("Generating clinical summary and CDS recommendations..."):
                    st.session_state.scribe_result = HealthcareApiClient().analyze_encounter(transcript)
                st.rerun()
            except Exception as error:
                if transcript:
                    st.session_state.scribe_result = {
                        "transcript": transcript,
                        "soap_note": "",
                        "patient_summary": "",
                        "key_highlights": [],
                        "retrieved_guidelines": "",
                        "recommendations": "",
                    }
                    st.warning(
                        "The clinical summary service is unavailable, so the transcript was kept. "
                        f"Local ICD-10/CPT matching can still continue. Details: {error}"
                    )
                else:
                    st.error(f"Unable to process the consultation: {error}")
        result = st.session_state.scribe_result
        if result:
            st.markdown('<div class="section-title">Transcript</div>', unsafe_allow_html=True)
            st.text_area("Generated transcript", value=result.get("transcript", ""), height=150, disabled=True, label_visibility="collapsed")
            st.markdown('<div class="section-title">Summary and Key Highlights</div>', unsafe_allow_html=True)
            render_summary(result.get("soap_note") or result.get("patient_summary", "Not available"))
            for highlight in result.get("key_highlights", []): st.markdown(f"- {highlight}")
            if result.get("recommendations"):
                st.success("Clinical Decision Support recommendations generated.")
                with st.container(border=True):
                    st.markdown('<div class="section-title">Clinical Decision Support Recommendations</div><div class="section-subtitle">Evidence-based guidance for this encounter</div>', unsafe_allow_html=True)
                    render_cds_recommendations(result["recommendations"])
            else:
                st.info("Clinical Decision Support recommendations will be generated when you save this note.")
            if st.button(
                "Generate Note",
                type="primary",
                use_container_width=True,
                disabled=not name_is_valid,
            ):
                encounter = result
                try:
                    if not encounter.get("recommendations"):
                        encounter = HealthcareApiClient().add_cds(encounter)
                except (RuntimeError, requests.exceptions.HTTPError) as error:
                    st.warning(
                        "Clinical decision support is unavailable, so the encounter will be saved "
                        f"without recommendations. Local ICD-10/CPT matching can still continue. Details: {error}"
                    )
                transcript = encounter.get("transcript", "")
                doctor, now = doctor_name.strip() or "Doctor", datetime.now()
                record = {"id": f"PT-{str(uuid.uuid4().int)[:6]}", "name": patient_name.strip(), "age": patient_age.strip(), "gender": patient_gender, "doctor": doctor, "date": now.strftime("%d/%m/%Y"), "time": now.strftime("%H:%M"), "status": "Pending", "transcript": transcript, "transcript_data": parse_transcript_entries(transcript, doctor, patient_name.strip()), "summary": encounter.get("soap_note", ""), "patient_summary": encounter.get("patient_summary", ""), "key_highlights": encounter.get("key_highlights", []), "retrieved_guidelines": encounter.get("retrieved_guidelines", ""), "recommendations": encounter.get("recommendations", ""), "cds_requested": True, "analysis_version": ANALYSIS_VERSION, "dob": patient_dob.strip()}
                try:
                    register_consultation_in_ehr(record, patient=selected_demo_patient, dob=patient_dob.strip() or None)
                except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                    st.error(f"The consultation could not be registered in Epic: {error}")
                    return
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
                        update_ehr_clinical_note(record)
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
                    record["summary"] = st.session_state.edit_summary
                    st.session_state.detected_insights = get_record_insights(record)
                    update_ehr_clinical_note(record)
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
            return False
        record["summary"] = st.session_state.edit_summary
        record["transcript_data"] = edited_df.to_dict("records")
        if approved:
            record["status"] = "Approved"
            record["approval_date"] = datetime.now().strftime("%d/%m/%Y")
        try:
            update_ehr_clinical_note(record, approved=approved)
        except (RuntimeError, requests.exceptions.RequestException) as error:
            st.error(f"The note could not be synchronized to Epic: {error}")
            return False
        for index, existing in enumerate(st.session_state.records):
            if existing.get("id") == record.get("id"):
                st.session_state.records[index] = record; break
        save_db(st.session_state.records)
        return True
    if record.get("status") == "Pending":
        st.divider()
        _, draft_col, final_col = st.columns([7, 1.5, 1.5], vertical_alignment="center")
        if draft_col.button("Save Draft", use_container_width=True):
            if persist(False): st.success("Draft and audit trail updated.")
        if final_col.button("Finalize Note", type="primary", use_container_width=True):
            if persist(True): go_to("dashboard"); st.rerun()


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
    if st.session_state.generated_bill_record_id != record.get("id"):
        st.session_state.generated_bill = None
        st.session_state.generated_bill_record_id = None
        st.session_state.generated_bill_patient_id = None

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
                st.session_state.code_results_signature = tuple(insights)
                st.session_state.code_matcher_version = CODE_MATCHER_VERSION
            st.rerun()
        if review_col.button("Return to Clinical Review", use_container_width=True):
            go_to("review"); st.rerun()

    current_signature = tuple(insights)
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
        st.markdown('<div class="section-title">Unified Diagnosis and Procedure Codes</div><div class="section-subtitle">Review this encounter\'s codes before generating its patient-specific gross-charge bill</div>', unsafe_allow_html=True)
        if unified_rows:
            st.dataframe(pd.DataFrame(unified_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No diagnosis or procedure codes are available for this encounter.")

    try:
        demo_patients = HealthcareApiClient().list_patients()
    except (RuntimeError, requests.exceptions.RequestException) as error:
        demo_patients = []
        st.error(f"EHR patients could not be loaded from the backend: {error}")
    patient_lookup = {str(patient.get("Patient_ID", "")): patient for patient in demo_patients}
    patient_ids = list(patient_lookup)
    linked_patient_id = str(record.get("patient_id", ""))
    default_patient_id = linked_patient_id if linked_patient_id in patient_lookup else (patient_ids[0] if patient_ids else "")
    selected_patient_id = ""
    selected_bill_encounter = ""
    encounter_id = None
    selected_patient_chart = {"records": {}}
    if patient_ids:
        selected_patient_id = st.selectbox(
            "Bill for EHR patient",
            patient_ids,
            index=patient_ids.index(default_patient_id),
            format_func=lambda patient_id: f'{patient_id} · {patient_lookup[patient_id].get("Legal_Name", "")}',
            key=f"bill_patient_{record.get('id', 'encounter')}",
        )
        if not linked_patient_id:
            st.caption("Register this consultation against the selected Epic patient to attach its codes and note to the bill.")
            if st.button("Register this consultation in Epic", key=f"register_consult_{record.get('id', 'encounter')}"):
                try:
                    register_consultation_in_ehr(record, patient=patient_lookup[selected_patient_id])
                    for index, existing in enumerate(st.session_state.records):
                        if existing.get("id") == record.get("id"):
                            st.session_state.records[index] = record
                            break
                    save_db(st.session_state.records)
                    st.rerun()
                except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                    st.error(f"Consultation could not be registered in Epic: {error}")
        try:
            selected_patient_chart = HealthcareApiClient().get_patient_chart(selected_patient_id)
        except (RuntimeError, requests.exceptions.RequestException) as error:
            selected_patient_chart = {"records": {}}
            st.error(f"Encounter list could not be loaded: {error}")
        patient_encounters = selected_patient_chart.get("records", {}).get("Encounters", [])
        encounter_lookup = {
            str(item.get("Encounter_ID", "")): item
            for item in patient_encounters
            if item.get("Encounter_ID")
        }
        encounter_options = ["Create new encounter"] + [
            encounter_id for encounter_id in encounter_lookup
        ]
        preferred_encounter_id = str(record.get("ehr_encounter_id", ""))
        if preferred_encounter_id not in encounter_lookup:
            preferred_encounter_id = next(
                (
                    encounter_id for encounter_id, item in encounter_lookup.items()
                    if "billing" not in str(item.get("Type", "")).lower()
                ),
                "",
            )
        default_encounter = preferred_encounter_id or encounter_options[0]
        selected_bill_encounter = st.selectbox(
            "Patient encounter",
            encounter_options,
            index=encounter_options.index(default_encounter),
            format_func=lambda value: (
                "Create new encounter from these codes"
                if value == encounter_options[0]
                else f'{value} · {encounter_lookup[value].get("Type", "Encounter")} · {encounter_lookup[value].get("Date", "")}'
            ),
            key=f"bill_encounter_v2_{selected_patient_id}_{record.get('id', 'encounter')}",
        )
        encounter_id = None if selected_bill_encounter == encounter_options[0] else selected_bill_encounter
        if encounter_id:
            selected_encounter_type = encounter_lookup[encounter_id].get("Type", "")
            bill_encounter_type = "Inpatient" if "inpatient" in str(selected_encounter_type).lower() else "Outpatient"
        else:
            bill_encounter_type = st.selectbox(
                "New encounter type",
                ["Outpatient", "Inpatient"],
                key=f"new_bill_encounter_type_{selected_patient_id}_{record.get('id', 'encounter')}",
            )
        note_encounter_id = str(record.get("ehr_encounter_id", ""))
        use_note_codes = (
            selected_patient_id == linked_patient_id
            and (encounter_id is None or not note_encounter_id or encounter_id == note_encounter_id)
        )
        if not use_note_codes:
            suggested_cpt_codes = [
                str(item.get("CPT/HCPCS Code", ""))
                for item in code_results.get("cpt", [])
                if item.get("CPT/HCPCS Code")
            ]
            use_note_codes = st.checkbox(
                f"Apply this consultation's CPT codes to {patient_lookup[selected_patient_id].get('Legal_Name', selected_patient_id)}",
                help="Use only when these reviewed codes belong to the selected patient's encounter.",
                key=f"apply_consult_codes_{selected_patient_id}_{record.get('id', 'encounter')}",
            )
        else:
            suggested_cpt_codes = [
                str(item.get("CPT/HCPCS Code", ""))
                for item in code_results.get("cpt", [])
                if item.get("CPT/HCPCS Code")
            ]
        encounter_order_codes = [
            str(item.get("CPT_HCPCS", "")).strip()
            for item in selected_patient_chart.get("records", {}).get("Orders_Procedures", [])
            if item.get("Encounter_ID") == encounter_id and item.get("CPT_HCPCS")
        ]
        codes_for_bill = suggested_cpt_codes if use_note_codes else encounter_order_codes
        if not codes_for_bill:
            st.warning("This encounter has no CPT/HCPCS orders. Select the checkbox above only if the displayed consultation codes belong to this patient.")
        if st.button("Generate Bill", type="primary", disabled=not codes_for_bill):
            try:
                bill = HealthcareApiClient().generate_patient_bill(
                    selected_patient_id,
                    [str(item.get("ICD-10 Code", "")) for item in code_results.get("icd10", []) if item.get("ICD-10 Code")] if use_note_codes else [],
                    codes_for_bill,
                    encounter_id=encounter_id,
                    encounter_type=bill_encounter_type,
                )
                st.session_state.generated_bill = bill
                st.session_state.generated_bill_record_id = record.get("id")
                st.session_state.generated_bill_patient_id = selected_patient_id
                st.rerun()
            except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                st.error(f"Bill generation failed: {error}")
    else:
        st.warning("Add an EHR patient before generating and saving a bill.")

    bill = st.session_state.generated_bill
    if (
        bill
        and (
            st.session_state.generated_bill_record_id != record.get("id")
            or st.session_state.generated_bill_patient_id != selected_patient_id
            or bill.get("Patient_ID") != selected_patient_id
            or (encounter_id and bill.get("Encounter_ID") != encounter_id)
        )
    ):
        st.session_state.generated_bill = None
        st.session_state.generated_bill_record_id = None
        st.session_state.generated_bill_patient_id = None
        bill = None
    if bill and bill.get("Patient_ID") == selected_patient_id:
        st.markdown('<div class="section-gap"></div><div class="section-title">Generated Hospital Bill</div><div class="section-subtitle">Services from this patient encounter priced against the original hospital chargemaster</div>', unsafe_allow_html=True)
        bill_total = float(bill.get("Gross_Total_USD", 0))
        total_col, name_col, account_col, encounter_col = st.columns(4)
        total_col.metric("Total Gross Charges", f"${bill_total:,.2f}")
        name_col.metric("Patient", bill.get("Patient_Name", "N/A"))
        account_col.metric("Hospital Account", bill.get("Hospital_Account", "N/A"))
        encounter_col.metric("EHR Encounter", bill.get("Encounter_ID", "N/A"))
        st.dataframe(pd.DataFrame(bill.get("Line_Items", [])), use_container_width=True, hide_index=True)
        st.caption(f'{bill.get("Notice", "Gross charges only.")} {bill.get("Bill_Status", "Draft")} · Saved to the Epic demo JSON as {bill.get("Bill_ID", "")} for {bill.get("Patient_ID", "")} / {bill.get("Encounter_ID", "")} .')
    else:
        st.info("No bill generated in this visit. Select Generate Bill to create a new bill.")

    st.caption("Coding suggestions require professional validation. Bills display gross charges only; payer and patient responsibility are not calculated.")


# ============================================================
# 9. ROUTER
# ============================================================
if st.session_state.page == "dashboard":
    show_dashboard()
elif st.session_state.page == "new_consult":
    show_new_consultation()
elif st.session_state.page == "ehr_data":
    show_patient_data()
elif st.session_state.page == "review":
    show_review_note()
elif st.session_state.page == "codes":
    show_codes()
else:
    st.session_state.page = "dashboard"
    st.rerun()
