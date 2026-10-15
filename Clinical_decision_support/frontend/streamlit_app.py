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
    from frontend.ipd_billing import show_ipd_billing
except ModuleNotFoundError:
    from ipd_billing import show_ipd_billing

try:
    from frontend.claim_generation_page import (
        build_ambient_bill,
        register_generated_bill,
        show_claim_generation_page,
    )
except ModuleNotFoundError:
    from claim_generation_page import (
        build_ambient_bill,
        register_generated_bill,
        show_claim_generation_page,
    )

try:
    from frontend.admin_console import show_admin_dashboard, show_super_admin
except ModuleNotFoundError:
    from admin_console import show_admin_dashboard, show_super_admin

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
        infer_documented_procedures,
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
            infer_documented_procedures,
            is_explicitly_performed,
        )
        from icd10_coder import get_icd10_candidate_sets, get_icd10_codes
    except ModuleNotFoundError:
        classify_medical_item = None
        classify_procedure_status = None
        extract_performed_procedures = None
        get_cpt_candidate_sets = None
        get_cpt_codes = None
        infer_documented_procedures = None
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
EHR_JSON_FILE = PROJECT_ROOT / "Epic_Inspired_USA_50_Patient_Demo.json"
BILLING_NOTICE = (
    "This draft contains gross chargemaster-based demo charges only. It is not a finalized claim, "
    "payer-allowed amount, or patient responsibility amount. Professional coding and billing validation is required."
)
ANALYSIS_VERSION = "2"
CODE_MATCHER_VERSION = "4"


# ============================================================
# 2. THEME AND LAYOUT
# ============================================================

theme_name = st.session_state.get("theme", "Light")
if theme_name == "Dark":
    theme_vars = """
        --bg: #0b1622;
        --surface: #12202f;
        --surface-soft: #0f1b28;
        --border: #24364a;
        --border-strong: #33495f;
        --text: #e8eef5;
        --muted: #93a4b8;
        --primary: #14a394;
        --primary-hover: #0e8679;
        --primary-soft: #0f3532;
        --blue: #5b8cff;
        --blue-soft: #1a2c52;
        --success: #4ade80;
        --success-soft: #133a24;
        --warning: #fbbf24;
        --warning-soft: #3d2f0b;
        --danger: #f87171;
        --danger-soft: #451a1a;
        --violet: #b7aefc;
        --violet-soft: #2a2550;
        --info: #60a5fa;
        --info-soft: #1e3a8a;
        --purple: #c084fc;
        --badge-green-bg: #22c55e; --badge-green-fg: #04210f;
        --badge-amber-bg: #facc15; --badge-amber-fg: #2b2100;
        --badge-red-bg: #ef4444; --badge-red-fg: #ffffff;
        --badge-violet-bg: #3b3474; --badge-violet-fg: #ddd8ff;
        --badge-blue-bg: #3b6ff0; --badge-blue-fg: #ffffff;
        --badge-gray-bg: #24364a; --badge-gray-fg: #c9d5e2;
        --mint-bg: #123b38; --mint-border: #1d625b; --mint-title: #a7f3d0; --mint-text: #c4e8df;
    """
else:
    theme_vars = """
        --bg: #f3f6f9;
        --surface: #ffffff;
        --surface-soft: #f7f9fb;
        --border: #e3e8ee;
        --border-strong: #cfd8e3;
        --text: #14212e;
        --muted: #6b7a8c;
        --primary: #0e6b63;
        --primary-hover: #0b5a53;
        --primary-soft: #e3f3f1;
        --blue: #2f6bed;
        --blue-soft: #eaf0ff;
        --success: #15803d;
        --success-soft: #dcfce7;
        --warning: #92400e;
        --warning-soft: #fef3c7;
        --danger: #b91c1c;
        --danger-soft: #fdecec;
        --violet: #4c3fbf;
        --violet-soft: #e6e2ff;
        --info: #2563eb;
        --info-soft: #e8f1fd;
        --purple: #7c3aed;
        --badge-green-bg: #4ade80; --badge-green-fg: #063b1c;
        --badge-amber-bg: #fcd34d; --badge-amber-fg: #4a3200;
        --badge-red-bg: #e5484d; --badge-red-fg: #ffffff;
        --badge-violet-bg: #e2deff; --badge-violet-fg: #3f33a8;
        --badge-blue-bg: #2f6bed; --badge-blue-fg: #ffffff;
        --badge-gray-bg: #e8edf3; --badge-gray-fg: #405063;
        --mint-bg: #e7f7f1; --mint-border: #a8d9c9; --mint-title: #115e59; --mint-text: #47706d;
    """

APP_CSS = """
<style>
:root {
    /*THEME*/
    --nav-bg: #0c2135;
    --nav-bg-2: #0f2740;
    --nav-active: #1b3b57;
    --nav-text: #d6e2ee;
    --nav-muted: #8ba0b5;
    --nav-border: #1d3550;
    --nav-accent: #2dd4bf;
}
#MainMenu, footer, [data-testid="stAppDeployButton"], [data-testid="stToolbarActions"], [data-testid="stStatusWidget"] { display: none !important; }
html { font-size: 15px; }
.stApp {
    background: var(--bg);
    color: var(--text);
    font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
header[data-testid="stHeader"] {
    height: 2.6rem; background: transparent !important; pointer-events: none; z-index: 1000;
}
header[data-testid="stHeader"] button { pointer-events: auto; }
[data-testid="stAppViewContainer"] > .main { padding-top: 0; }
.block-container { max-width: 1560px; padding: 0 1.4rem 1.2rem !important; }
[data-testid="stVerticalBlock"] { gap: .55rem; }
[data-testid="stHorizontalBlock"] { gap: .65rem; }
[data-testid="stElementContainer"] { margin-bottom: 0; }
hr { margin: .25rem 0 .5rem !important; border-color: var(--border) !important; }

/* ---------- Top bar ---------- */
.st-key-nc_topbar {
    width: calc(100% + 2.8rem) !important; max-width: none !important; background: var(--nav-bg); margin: 0 -1.4rem .9rem; padding: .55rem 1.4rem .5rem;
    border-bottom: 1px solid var(--nav-border);
}
.st-key-nc_topbar [data-testid="stHorizontalBlock"] { align-items: center; gap: .35rem; }
.nc-brand { display: flex; align-items: center; gap: .55rem; white-space: nowrap; }
.nc-brand-mark { width: 1.35rem; height: 1.35rem; border-radius: 50%; border: 3px solid #2dd4bf; border-right-color: #14b8a6; border-bottom-color: #0e7490; }
.nc-brand-name { color: #fff; font-weight: 800; font-size: 1.02rem; line-height: 1; }
.nc-brand-sub { color: var(--nav-muted); font-size: .5rem; font-weight: 800; letter-spacing: .08rem; margin-top: .15rem; }
.nc-app-pills { display: flex; gap: .3rem; margin-left: .75rem; }
.nc-app-pill { padding: .22rem .6rem; border-radius: 999px; font-size: .68rem; font-weight: 650; color: var(--nav-text); background: #142c45; white-space: nowrap; }
.nc-app-pill.on { background: #0f766e; color: #ecfeff; }
.nc-topbar-right { display: flex; justify-content: flex-end; align-items: center; gap: 1rem; color: var(--nav-text); font-size: .74rem; font-weight: 600; white-space: nowrap; }
.nc-topbar-right .nc-count { display: inline-grid; place-items: center; min-width: 1.05rem; height: 1.05rem; margin-left: .2rem; padding: 0 .25rem; border-radius: 999px; background: #e5484d; color: #fff; font-size: .6rem; }
.nc-avatar { display: grid; place-items: center; width: 1.75rem; height: 1.75rem; border-radius: 50%; background: #0ea5e9; color: #fff; font-size: .66rem; font-weight: 800; }
[data-testid="stMain"] [class*="st-key-topnav_"] button {
    background: transparent !important; border: 0 !important; box-shadow: none !important;
    color: var(--nav-text) !important; min-height: 1.9rem !important; padding: .2rem .5rem !important;
    border-radius: .4rem !important; font-size: .74rem !important; font-weight: 600 !important; white-space: nowrap;
}
[class*="st-key-topnav_"] button p { font-size: .74rem !important; }
[data-testid="stMain"] [class*="st-key-topnav_"] button:hover { background: #17324d !important; color: #fff !important; }
[data-testid="stMain"] [class*="st-key-topnav_on_"] button { background: #1b3b57 !important; color: #fff !important; }

/* ---------- Sidebar ---------- */
[data-testid="stSidebar"] { background: var(--nav-bg) !important; border-right: 1px solid var(--nav-border); }
[data-testid="stSidebar"] > div:first-child { padding-top: .2rem; }
[data-testid="stSidebar"] * { color: var(--nav-text); }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .12rem; }
[data-testid="stSidebar"] .stButton > button {
    justify-content: flex-start; min-height: 2.05rem; padding: .3rem .7rem; background: transparent !important;
    border: 0 !important; border-left: 3px solid transparent !important; border-radius: .35rem !important;
    box-shadow: none !important; color: var(--nav-text) !important;
}
[data-testid="stSidebar"] .stButton > button > div { justify-content: flex-start; width: 100%; }
[data-testid="stSidebar"] .stButton > button p { font-size: .8rem; font-weight: 600; text-align: left; }
[data-testid="stSidebar"] .stButton > button:hover { background: var(--nav-bg-2) !important; color: #fff !important; }
[data-testid="stSidebar"] [class*="st-key-nav_on_"] button { background: var(--nav-active) !important; border-left-color: var(--nav-accent) !important; color: #fff !important; }
[data-testid="stSidebar"] [data-testid="stExpander"] details { border-color: var(--nav-border) !important; background: transparent; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover { background: var(--nav-bg-2); }
[data-testid="stSidebar"] [data-baseweb="select"] > div { background: var(--nav-bg-2) !important; border-color: var(--nav-border) !important; }
[data-testid="stSidebar"] label p { color: var(--nav-muted) !important; font-size: .68rem; }
.shell-section-label { display: flex; align-items: center; gap: .45rem; margin: .55rem 0 .6rem; padding: 0 .3rem; color: var(--nav-text) !important; font-size: .7rem; font-weight: 800; letter-spacing: .09rem; }
.shell-section-label.muted { color: var(--nav-muted) !important; font-size: .6rem; margin-top: .9rem; }
.shell-divider { height: 1px; margin: .55rem 0; background: var(--nav-border); }
.shell-user { display: flex; align-items: center; gap: .55rem; margin-top: .8rem; padding: .6rem .3rem; border-top: 1px solid var(--nav-border); }
.shell-user b, .shell-user small { display: block; }
.shell-user b { color: #fff !important; font-size: .78rem; }
.shell-user small { color: var(--nav-muted) !important; font-size: .66rem; }
.user-avatar { display: grid; place-items: center; width: 2rem; height: 2rem; border-radius: 50%; background: #0ea5e9; color: #fff !important; font-size: .68rem; font-weight: 800; }

/* ---------- Buttons ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button { min-height: 2.15rem; border-radius: 999px; font-weight: 650; }
[data-testid="stMain"] [data-testid="stBaseButton-primary"],
[data-testid="stMain"] [data-testid="stBaseButton-primaryFormSubmit"] {
    background: var(--primary) !important; border: 1px solid var(--primary) !important; color: #fff !important; box-shadow: none !important;
}
[data-testid="stMain"] [data-testid="stBaseButton-primary"]:hover { background: var(--primary-hover) !important; border-color: var(--primary-hover) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-primary"]:disabled { background: #9cc6c1 !important; border-color: #9cc6c1 !important; opacity: .8 !important; }
[data-testid="stMain"] [data-testid="stBaseButton-secondary"],
[data-testid="stMain"] [data-testid="stBaseButton-secondaryFormSubmit"] {
    background: var(--surface) !important; border: 1px solid var(--blue) !important; color: var(--blue) !important; box-shadow: none !important;
}
[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:hover { background: var(--blue-soft) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:disabled { border-color: var(--border-strong) !important; color: var(--muted) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-tertiary"] { color: var(--blue) !important; font-weight: 650; min-height: 1.8rem; }
[data-testid="stMain"] [data-testid="stBaseButton-tertiary"]:hover { text-decoration: underline; }
[data-testid="stMain"] [class*="st-key-accent_"] button { background: var(--blue) !important; border-color: var(--blue) !important; color: #fff !important; }
[data-testid="stMain"] [class*="st-key-accent_"] button:hover { filter: brightness(.94); }
[data-testid="stMain"] [class*="st-key-danger_"] button { border-color: var(--danger) !important; color: var(--danger) !important; }

/* ---------- Inputs and cards ---------- */
/* Widgets follow the app tokens, not the viewer's OS/browser theme. */
[data-testid="stMain"] [data-testid="stWidgetLabel"] p,
[data-testid="stMain"] [data-testid="stWidgetLabel"] label { color: var(--text) !important; font-size: .74rem; font-weight: 650; }
[data-testid="stMain"] [data-baseweb="input"],
[data-testid="stMain"] [data-baseweb="base-input"],
[data-testid="stMain"] [data-baseweb="textarea"],
[data-testid="stMain"] [data-baseweb="select"] > div {
    background: var(--surface) !important; border-color: var(--border-strong) !important; color: var(--text) !important;
}
[data-testid="stMain"] input, [data-testid="stMain"] textarea,
[data-testid="stMain"] [data-baseweb="select"] div { color: var(--text) !important; -webkit-text-fill-color: var(--text); background-color: transparent; }
[data-testid="stMain"] input:disabled, [data-testid="stMain"] textarea:disabled { -webkit-text-fill-color: var(--muted); }
[data-testid="stMain"] input::placeholder, [data-testid="stMain"] textarea::placeholder { color: var(--muted) !important; -webkit-text-fill-color: var(--muted); }
[data-testid="stMain"] [data-baseweb="select"] svg { fill: var(--muted); }
[data-baseweb="popover"] ul[role="listbox"], [data-baseweb="popover"] [role="option"] { background: var(--surface) !important; color: var(--text) !important; }
[data-baseweb="popover"] [role="option"]:hover, [data-baseweb="popover"] [aria-selected="true"] { background: var(--primary-soft) !important; }
[data-testid="stFileUploaderDropzone"] { background: var(--surface-soft) !important; border: 1.5px dashed var(--border-strong) !important; border-radius: .6rem !important; }
[data-testid="stFileUploaderDropzone"] * { color: var(--muted) !important; }
[data-testid="stFileUploaderDropzone"] button { background: var(--surface) !important; border: 1px solid var(--blue) !important; color: var(--blue) !important; }
[data-testid="stFileUploaderDropzone"] button * { color: var(--blue) !important; }
[data-testid="stFileUploaderFile"] * { color: var(--text) !important; }
/* Actual widget shells Streamlit paints with its (possibly dark) theme colour. */
[data-testid="stMain"] [data-testid="stTextInputRootElement"],
[data-testid="stMain"] [data-testid="stTextAreaRootElement"],
[data-testid="stMain"] [data-testid="stNumberInputContainer"],
[data-testid="stMain"] [data-testid="stDateInputField"],
[data-testid="stMain"] [data-testid="stSelectbox"] [role="group"],
[data-testid="stMain"] [data-testid="stMultiSelect"] [role="group"] {
    background: var(--surface) !important; border: 1px solid var(--border-strong) !important; border-radius: .5rem !important; color: var(--text) !important;
}
[data-testid="stMain"] [data-testid="stTextInputRootElement"] *,
[data-testid="stMain"] [data-testid="stTextAreaRootElement"] *,
[data-testid="stMain"] [data-testid="stSelectbox"] [role="group"] * { background-color: transparent !important; color: var(--text) !important; -webkit-text-fill-color: var(--text); }
[data-testid="stMain"] [data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stMain"] [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stMain"] [data-testid="stSelectbox"] [role="group"]:focus-within { border-color: var(--primary) !important; box-shadow: 0 0 0 2px var(--primary-soft) !important; }
[data-testid="stMain"] [data-testid="stTextInputRootElement"]:has(input:disabled) { background: var(--surface-soft) !important; }
[data-testid="stMain"] [data-testid="stTextInputRootElement"] input:disabled { -webkit-text-fill-color: var(--muted); }
[data-testid="stMain"] [data-testid="stSelectbox"] svg { fill: var(--muted) !important; color: var(--muted) !important; }
[data-testid="stMain"] [data-testid="stPopover"] button {
    background: var(--surface) !important; color: var(--text) !important; border: 1px solid var(--border-strong) !important; box-shadow: none !important;
}
[data-testid="stMain"] [data-testid="stPopover"] button * { color: var(--text) !important; fill: var(--text) !important; }
[data-testid="stMain"] [data-testid="stPopover"] button:hover { border-color: var(--blue) !important; background: var(--blue-soft) !important; }
[data-testid="stPopoverBody"] { background: var(--surface) !important; color: var(--text) !important; border: 1px solid var(--border) !important; }
[data-testid="stPopoverBody"] p, [data-testid="stPopoverBody"] label { color: var(--text) !important; }
[data-testid="stPopoverBody"] [data-testid="stTextAreaRootElement"],
[data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"] { background: var(--surface) !important; border: 1px solid var(--border-strong) !important; border-radius: .5rem !important; }
[data-testid="stPopoverBody"] [data-testid="stTextAreaRootElement"] *,
[data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"] * { background-color: transparent !important; color: var(--text) !important; -webkit-text-fill-color: var(--text); }
[data-testid="stPopoverBody"] button { background: var(--primary) !important; border: 1px solid var(--primary) !important; color: #fff !important; }
[data-testid="stPopoverBody"] button * { color: #fff !important; }
[data-testid="stPopoverBody"] button:disabled { background: #9cc6c1 !important; border-color: #9cc6c1 !important; }
[data-testid="stMain"] [data-testid="stTab"], [data-testid="stMain"] [data-testid="stTab"] p { color: var(--blue) !important; }
[data-testid="stMain"] [data-testid="stTab"][aria-selected="true"], [data-testid="stMain"] [data-testid="stTab"][aria-selected="true"] p { color: var(--text) !important; font-weight: 700; }
[data-testid="stAudioInput"] > div { background: var(--surface) !important; border: 1px solid var(--border-strong) !important; border-radius: .6rem !important; }
[data-testid="stAudioInput"] button { color: var(--text) !important; opacity: 1 !important; }
[data-testid="stAudioInput"] button[aria-label="Record"] { color: var(--primary) !important; }
[data-testid="stAudioInput"] button[aria-label="Stop recording"] { color: #e5484d !important; }
[data-testid="stAudioInput"] button svg { fill: currentColor !important; color: currentColor !important; }
[data-testid="stAudioInput"] span, [data-testid="stAudioInput"] p { background: transparent !important; color: var(--muted) !important; }
[data-testid="stMain"] [data-testid="stExpander"] details { background: var(--surface); border-color: var(--border) !important; border-radius: .55rem; }
[data-testid="stMain"] [data-testid="stExpander"] summary p { color: var(--text) !important; font-weight: 650; }
[data-testid="stMain"] [data-testid="stCheckbox"] p { color: var(--text) !important; }
.stTextInput, .stSelectbox, .stDateInput, .stTextArea, .stRadio { margin-bottom: 0 !important; }
[data-baseweb="input"], [data-baseweb="select"] > div, textarea { border-radius: .5rem !important; }
div[data-testid="stVerticalBlockBorderWrapper"] { background: var(--surface); border-color: var(--border) !important; border-radius: .7rem !important; box-shadow: 0 1px 2px rgba(15,23,42,.04); }
div[data-testid="stVerticalBlockBorderWrapper"] > div { padding: .8rem .95rem !important; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap: .25rem; border-bottom: 1px solid var(--border); }
[data-testid="stTabs"] [data-baseweb="tab"] { padding: .35rem .7rem; color: var(--blue); font-size: .78rem; }
[data-testid="stTabs"] [aria-selected="true"] { color: var(--text); font-weight: 700; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background: var(--primary); }

/* ---------- Page headers ---------- */
.nc-eyebrow { color: var(--muted); font-size: .64rem; font-weight: 800; letter-spacing: .09rem; text-transform: uppercase; }
.page-heading { margin: .1rem 0 0; font-size: 1.55rem; line-height: 1.15; font-weight: 780; color: var(--text); }
.page-kicker { margin: .2rem 0 .45rem; font-size: .76rem; color: var(--muted); }
.section-title { margin: 0; font-size: .98rem; line-height: 1.25; font-weight: 750; color: var(--text); }
.section-subtitle { margin: .1rem 0 .5rem; font-size: .72rem; color: var(--muted); }
.toolbar-label, .insight-heading { margin: .45rem 0 .2rem; color: var(--muted); font-size: .64rem; font-weight: 800; letter-spacing: .08rem; text-transform: uppercase; }
.section-gap { height: .45rem; }

/* ---------- Badges ---------- */
.nc-badge { display: inline-flex; align-items: center; gap: .25rem; padding: .2rem .5rem; border-radius: .3rem; font-size: .68rem; font-weight: 700; line-height: 1.2; white-space: nowrap; }
.nc-b-green { background: var(--badge-green-bg); color: var(--badge-green-fg); }
.nc-b-amber { background: var(--badge-amber-bg); color: var(--badge-amber-fg); }
.nc-b-red { background: var(--badge-red-bg); color: var(--badge-red-fg); }
.nc-b-violet { background: var(--badge-violet-bg); color: var(--badge-violet-fg); }
.nc-b-blue { background: var(--badge-blue-bg); color: var(--badge-blue-fg); }
.nc-b-gray { background: var(--badge-gray-bg); color: var(--badge-gray-fg); }
.nc-b-outline { background: transparent; color: var(--blue); border: 1px solid var(--blue); border-radius: 999px; }
.nc-badge-row { margin-bottom: .25rem; display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; }
.status-pill { display: inline-flex; align-items: center; justify-content: center; padding: .24rem .55rem; border-radius: .3rem; font-size: .66rem; font-weight: 800; }
.status-pending { background: var(--badge-amber-bg); color: var(--badge-amber-fg); }
.status-approved { background: var(--badge-green-bg); color: var(--badge-green-fg); }

/* ---------- Stepper ---------- */
.nc-stepper { margin-bottom: .35rem; display: flex; flex-wrap: wrap; align-items: center; gap: 1.1rem; padding: .55rem .8rem; background: var(--surface); border: 1px solid var(--border); border-radius: .6rem; }
.nc-step { display: flex; align-items: center; gap: .4rem; color: var(--muted); font-size: .74rem; font-weight: 600; }
.nc-step i { display: grid; place-items: center; width: 1.25rem; height: 1.25rem; border-radius: 50%; border: 1px solid var(--border-strong); font-style: normal; font-size: .64rem; }
.nc-step.done { color: var(--primary); }
.nc-step.done i { background: var(--primary); border-color: var(--primary); color: #fff; }
.nc-step.current { color: var(--primary); font-weight: 750; }
.nc-step.current i { border: 2px solid var(--primary); color: var(--primary); }

/* ---------- Context strip and banners ---------- */
.nc-context { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .9rem; padding: .8rem 1rem; background: var(--surface); border: 1px solid var(--border); border-radius: .6rem; }
.nc-context small { display: block; color: var(--muted); font-size: .6rem; font-weight: 800; letter-spacing: .06rem; text-transform: uppercase; }
.nc-context b { display: block; margin-top: .25rem; color: var(--text); font-size: .78rem; font-weight: 600; overflow-wrap: anywhere; }
.nc-context-foot { grid-column: 1 / -1; color: var(--muted); font-size: .66rem; }
.nc-banner { display: flex; gap: .6rem; align-items: flex-start; padding: .65rem .85rem; border-radius: .55rem; border: 1px solid var(--border); border-left: 3px solid var(--info); background: var(--info-soft); font-size: .74rem; color: var(--text); }
.nc-banner b { display: block; font-size: .75rem; }
.nc-banner small { display: block; color: var(--muted); font-size: .68rem; margin-top: .1rem; }
.nc-banner.warn { border-left-color: #d97706; background: var(--warning-soft); }
.nc-banner.danger { border-left-color: var(--danger); background: var(--danger-soft); align-items: center; }
.nc-banner.ok { border-left-color: var(--success); background: var(--success-soft); }
.nc-banner .nc-grow { flex: 1; }

/* ---------- Recommendation cards ---------- */
[class*="st-key-rec_"] { gap: .45rem; padding: .7rem .85rem .6rem; background: var(--surface); border: 1px solid var(--border); border-left: 3px solid #e5484d; border-radius: .5rem; }
[class*="st-key-rec_med_"] { border-left-color: #f59e0b; }
[class*="st-key-rec_low_"] { border-left-color: var(--primary); }
.nc-rec { padding-bottom: .6rem; }
.nc-rec-title { margin: .35rem 0 .2rem; font-size: .86rem; font-weight: 750; color: var(--text); }
.nc-rec-body { color: var(--muted); font-size: .74rem; line-height: 1.45; white-space: pre-wrap; }
.nc-rec-source { margin-top: .3rem; color: var(--blue); font-size: .66rem; font-weight: 650; }
.nc-meter { display: flex; align-items: center; gap: .5rem; margin-top: .35rem; font-size: .72rem; color: var(--text); }
.nc-meter span { display: block; width: 6.5rem; height: .32rem; border-radius: 999px; background: var(--border); overflow: hidden; }
.nc-meter span em { display: block; height: 100%; background: var(--blue); }
.nc-decision { margin-top: .3rem; font-size: .68rem; color: var(--muted); }

/* ---------- Check lists (medication safety, care gaps) ---------- */
.nc-checklist { display: flex; flex-direction: column; }
.nc-check { display: flex; justify-content: space-between; align-items: center; gap: .6rem; padding: .48rem 0; border-bottom: 1px solid var(--border); font-size: .76rem; color: var(--text); }
.nc-check:last-child { border-bottom: 0; }
.nc-check .nc-dot { display: inline-block; width: .42rem; height: .42rem; margin-right: .5rem; border-radius: 50%; background: var(--muted); }
.nc-check small { display: block; margin-left: .92rem; color: var(--muted); font-size: .64rem; }

/* ---------- Data tables ---------- */
.nc-table { width: 100%; border-collapse: collapse; font-size: .74rem; color: var(--text); }
.nc-table th { padding: .5rem .55rem; text-align: left; color: var(--muted); font-size: .6rem; font-weight: 800; letter-spacing: .05rem; text-transform: uppercase; border-bottom: 1px solid var(--border); background: var(--surface-soft); }
.nc-table td { padding: .55rem .55rem; border-bottom: 1px solid var(--border); vertical-align: middle; }
.nc-table tr.sel td { background: var(--primary-soft); }
.nc-table tr.blocked td { background: var(--danger-soft); color: var(--danger); }
.nc-table code { font-family: inherit; font-weight: 750; color: var(--text); background: none; padding: 0; }
.nc-table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: .55rem; background: var(--surface); }
.nc-rowhead { display: grid; padding: .45rem .3rem; color: var(--muted); font-size: .6rem; font-weight: 800; letter-spacing: .05rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }
.nc-cell { font-size: .74rem; color: var(--text); line-height: 1.25; overflow-wrap: anywhere; }
.nc-cell.muted { color: var(--muted); }
.nc-cell b { font-weight: 750; }
.nc-rule { height: 1px; background: var(--border); margin: .05rem 0; }

/* ---------- Evidence panel ---------- */
.nc-excerpt { padding: .65rem .75rem; background: var(--surface-soft); border: 1px solid var(--border); border-radius: .45rem; }
.nc-excerpt small, .nc-label { display: block; color: var(--muted); font-size: .6rem; font-weight: 800; letter-spacing: .06rem; text-transform: uppercase; margin-bottom: .3rem; }
.nc-excerpt q { font-style: italic; font-size: .76rem; color: var(--text); line-height: 1.45; }
.nc-cdi { padding: .65rem .75rem; background: var(--warning-soft); border: 1px solid #f6d58a; border-radius: .45rem; font-size: .74rem; }
.nc-cdi b { display: block; margin-bottom: .2rem; }
.nc-meta { color: var(--muted); font-size: .66rem; line-height: 1.5; }

/* ---------- Summary tiles ---------- */
.nc-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: .55rem; }
.nc-tile { padding: .6rem .75rem; background: var(--surface-soft); border: 1px solid var(--border); border-radius: .45rem; }
.nc-tile small { display: block; color: var(--muted); font-size: .58rem; font-weight: 800; letter-spacing: .06rem; text-transform: uppercase; }
.nc-tile b { display: block; margin-top: .3rem; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .95rem; color: var(--text); }

/* ---------- Selectable rows ---------- */
[class*="st-key-coderow_"], [class*="st-key-encrow_"] { padding: .1rem .2rem; border-bottom: 1px solid var(--border); border-radius: .3rem; }
[class*="st-key-coderow_sel_"], [class*="st-key-encrow_sel_"] { background: var(--primary-soft); }
[class*="st-key-dir_"] { border-radius: .55rem !important; }
[class*="st-key-dir_on_"] { border-color: var(--primary) !important; background: var(--primary-soft) !important; }
[class*="st-key-dir_"] > div { padding: .45rem .55rem !important; }

/* ---------- Patient directory / banner ---------- */
.nc-patient { display: flex; align-items: center; gap: .6rem; }
.nc-initials { display: grid; place-items: center; flex: none; width: 2.1rem; height: 2.1rem; border-radius: 50%; background: var(--badge-gray-bg); color: var(--text); font-size: .66rem; font-weight: 800; }
.nc-initials.lg { width: 2.8rem; height: 2.8rem; background: #0f4c5c; color: #fff; font-size: .8rem; }
.nc-patient b { display: block; font-size: .82rem; color: var(--text); }
.nc-patient small { display: block; color: var(--muted); font-size: .66rem; }
.nc-banner-card { display: flex; flex-wrap: wrap; align-items: center; gap: 1.2rem; padding: .8rem 1rem; background: var(--surface); border: 1px solid var(--border); border-radius: .6rem; }

/* ---------- Dashboard ---------- */
.kpi-card { min-height: 6.4rem; padding: .85rem 1rem; background: var(--surface); border: 1px solid var(--border); border-top: 3px solid var(--primary); border-radius: .65rem; }
.kpi-title { color: var(--muted); font-size: .64rem; font-weight: 800; letter-spacing: .05rem; text-transform: uppercase; }
.kpi-value { margin-top: .3rem; color: var(--text); font-size: 1.75rem; line-height: 1; font-weight: 790; }
.kpi-context { margin-top: .45rem; color: var(--muted); font-size: .7rem; }
.overview-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: .65rem; padding: 1rem; background: var(--surface); border: 1px solid var(--border); border-radius: .65rem; }
.overview-grid div { padding-left: .6rem; border-left: 2px solid var(--border-strong); }
.overview-grid small { display: block; color: var(--muted); font-size: .66rem; }
.overview-grid b { display: block; margin-top: .25rem; font-size: 1.45rem; }
.amber-text { color: var(--warning); } .green-text { color: var(--success); } .blue-text { color: var(--info); }
.patient-table-header { display: grid; grid-template-columns: 2.2fr 1.15fr 1.7fr 1.55fr 1.05fr 1.15fr; min-width: 790px; padding: .45rem .5rem; border-bottom: 1px solid var(--border-strong); color: var(--muted); font-size: .62rem; font-weight: 800; letter-spacing: .05rem; text-transform: uppercase; }
.patient-cell, .table-two-line { display: flex; min-height: 2.45rem; flex-direction: column; justify-content: center; gap: .2rem; line-height: 1.15; }
.patient-cell b { overflow-wrap: anywhere; font-size: .84rem; }
.patient-cell small, .table-two-line small, .table-meta { color: var(--muted); font-size: .71rem; }
.table-row-rule { height: 1px; background: var(--border); }
.pagination-label { padding-top: .55rem; color: var(--muted); font-size: .7rem; text-align: center; }

/* ---------- Review, transcript and graph ---------- */
.recording-prompt { margin-top: .25rem; padding: .65rem; text-align: center; color: var(--muted); background: var(--surface-soft); border: 1px dashed var(--border-strong); border-radius: .5rem; font-size: .72rem; }
.audit-note { padding-bottom: .3rem; color: var(--muted); font-size: .68rem; }
.summary-text { margin: .12rem 0 .55rem; white-space: pre-wrap; color: var(--text); font-size: .76rem; line-height: 1.45; }
.insight-scroll { max-height: 16rem; overflow-y: auto; padding-right: .2rem; }
.insight-category { margin: .55rem 0; }
.insight-category-heading { margin-bottom: .28rem; color: var(--muted); font-size: .64rem; font-weight: 800; text-transform: uppercase; }
.insight-category-items { display: flex; flex-wrap: wrap; gap: .32rem; }
.insight-chip { display: inline-flex; padding: .3rem .5rem; border: 1px solid var(--border); border-radius: 999px; background: transparent; }
.insight-chip b { font-size: .68rem; }
.insight-diagnosis { border-color: #fecaca; } .insight-symptom { border-color: #fcd34d; }
.insight-risk { border-color: #fdba74; } .insight-procedure { border-color: #93c5fd; }
.clinical-graph-v2 { padding: 1rem; background: var(--surface-soft); border: 1px solid var(--border); border-radius: .65rem; }
.graph-hierarchy-v2 { display: flex; flex-direction: column; align-items: center; gap: .6rem; }
.graph-patient-card { max-width: 80%; padding: .48rem .8rem; border-radius: .48rem; background: var(--primary); color: #fff; font-size: .73rem; font-weight: 750; text-align: center; }
.graph-connector-main { width: 2px; height: .8rem; background: var(--border-strong); }
.graph-categories { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); width: 100%; gap: .5rem; }
.graph-category-header { margin-bottom: .35rem; color: var(--muted); font-size: .58rem; font-weight: 800; text-align: center; text-transform: uppercase; }
.graph-category-content { display: flex; flex-direction: column; gap: .3rem; }
.graph-node-card, .graph-empty-state { padding: .52rem .4rem; border-radius: .42rem; font-size: .68rem; line-height: 1.25; text-align: center; overflow-wrap: anywhere; }
.graph-node-diagnosis { background: var(--purple); color: #fff; }
.graph-node-symptom { background: var(--info); color: #fff; }
.graph-node-risk { background: #d97706; color: #fff; }
.graph-empty-state { color: var(--muted); border: 1px dashed var(--border-strong); }
.code-empty { padding: 1.2rem; text-align: center; color: var(--muted) !important; background: var(--surface-soft); border: 1px dashed var(--border-strong); border-radius: .55rem; font-size: .76rem; }
.nc-upload-tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid var(--border); border-radius: .5rem; overflow: hidden; }
.nc-upload-tiles div { padding: .55rem .7rem; border-right: 1px solid var(--border); }
.nc-upload-tiles div:last-child { border-right: 0; }
.nc-upload-tiles small { display: block; color: var(--muted); font-size: .62rem; }
.nc-upload-tiles b { display: block; margin-top: .15rem; font-size: .74rem; }

@media (max-width: 900px) {
    .block-container { padding: 0 .75rem .75rem !important; }
    .st-key-nc_topbar { width: calc(100% + 1.5rem) !important; margin: 0 -.75rem .8rem; padding: .5rem .75rem; }
    .page-heading { font-size: 1.3rem; }
    .nc-context { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .overview-grid { grid-template-columns: 1fr; }
    .nc-upload-tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .nc-app-pills { display: none; }
}
</style>
"""
st.markdown(APP_CSS.replace("/*THEME*/", theme_vars), unsafe_allow_html=True)


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


def is_documented_cpt_candidate(item, documentation):
    """Return True for CPT suggestions that are documented in the note, even if not yet performed."""
    if not documentation or not str(documentation).strip():
        return False
    if not is_explicitly_performed:
        return False
    status = classify_procedure_status(item, documentation) if classify_procedure_status else "unknown"
    return status in {"performed", "ordered_not_performed", "planned", "recommended", "discussed"}


def is_chest_xray_multi_view_documented(documentation):
    normalized = str(documentation or "").lower()
    mentions_chest_xray = re.search(r"\bchest\s+(?:x\s*[- ]?\s*ray|radiographs?)\b", normalized)
    mentions_views = re.search(r"\b(?:1\s*(?:-|to)\s*2|two|2)\s*[- ]?\s*views?\b", normalized)
    return bool(mentions_chest_xray and mentions_views)


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

    if documentation and infer_documented_procedures:
        cpt_items.extend(infer_documented_procedures(documentation))
    cpt_items = list(dict.fromkeys(cpt_items))

    try:
        if is_chest_xray_multi_view_documented(documentation):
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
            performed_cpt_items = [
                item for item in cpt_items
                if is_documented_cpt_candidate(item, documentation)
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
        if is_chest_xray_multi_view_documented(documentation):
            procedures = [
                item for item in procedures
                if "chest x-ray" not in item.lower() and "chest x ray" not in item.lower()
            ]
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

        status_procedure, status_documentation = _procedure_status_inputs(source_procedure, documentation)
        status = (
            classify_procedure_status(status_procedure, status_documentation)
            if classify_procedure_status
            else "unknown"
        )
        if status == "unknown":
            unverifiable_count += 1
        elif is_explicitly_performed and is_explicitly_performed(status_procedure, status_documentation):
            billing_eligible_cpt_results.append(candidate)
        else:
            excluded_nonperformed_count += 1

    return (
        billing_eligible_cpt_results,
        excluded_nonperformed_count,
        unverifiable_count,
    )


def _procedure_status_inputs(procedure, documentation):
    status_procedure = re.sub(r"\bwithout\s+contrast\b", "", str(procedure or ""), flags=re.IGNORECASE)
    status_documentation = re.sub(r"\bwithout\s+contrast\b", "", str(documentation or ""), flags=re.IGNORECASE)
    return status_procedure, status_documentation


def prepare_billing_lines(cpt_results, documentation, encounter_date=None, encounter_type=None):
    billing_eligible_cpt_results, excluded_count, unverifiable_count = (
        filter_billing_eligible_cpt_results(cpt_results, documentation)
    )
    if billing_eligible_cpt_results:
        billing_rows = (
            build_cpt_bill_rows(billing_eligible_cpt_results, encounter_date)
            if encounter_type is None
            else build_cpt_bill_rows(billing_eligible_cpt_results, encounter_date, encounter_type)
        )
    else:
        billing_rows = []
    return billing_eligible_cpt_results, excluded_count, unverifiable_count, billing_rows


def normalize_billing_code(value):
    return re.sub(r"[\s.-]", "", str(value or "").strip()).upper()


def normalize_estimate_quantity(value):
    try:
        quantity = float(value)
    except (TypeError, ValueError, OverflowError):
        return 1
    if not math.isfinite(quantity) or quantity <= 0:
        return 1
    return int(quantity) if quantity.is_integer() else quantity


@st.cache_data(show_spinner=False)
def load_chargemaster_department_map (file_path, sheet_name):
    path = Path(file_path)
    if not path.is_file():
        return {}
    try:
        frame = pd.read_excel(path, sheet_name=sheet_name, header=3, dtype=str, keep_default_na=False, engine="openpyxl")
    except (OSError, ValueError, ImportError):
        return {}
    code_column = next((column for column in frame.columns if re.sub(r"[^a-z0-9]", "", str(column).lower()) in {"cpthcpcs", "cpthcpcscode"}), None)
    department_column = next((column for column in frame.columns if str(column).strip().casefold() == "department"), None)
    if code_column is None or department_column is None:
        return {}
    departments = {}
    for _, row in frame.iterrows():
        code = normalize_billing_code(row.get(code_column))
        department = str(row.get(department_column) or "").strip()
        if code and department:
            departments.setdefault(code, department)
    return departments


def select_cpt_results_for_bill(cpt_results, selected_codes):
    selected = {normalize_billing_code(code) for code in selected_codes or []}
    results = []
    included = set()
    for candidate in cpt_results or []:
        if not isinstance(candidate, dict):
            continue
        code = normalize_billing_code(candidate.get("CPT/HCPCS Code"))
        if code in selected and code not in included:
            results.append(candidate)
            included.add(code)
    return results


def build_estimated_hospital_bill(cpt_results, encounter_type):
    candidates_by_code = {}
    for candidate in cpt_results or []:
        if not isinstance(candidate, dict):
            continue
        raw_code = str(candidate.get("CPT/HCPCS Code") or "").strip()
        normalized_code = normalize_billing_code(raw_code)
        if not raw_code or not normalized_code:
            continue
        raw_quantity = candidate.get("quantity", candidate.get("Quantity"))
        quantity_is_explicit = raw_quantity is not None and str(raw_quantity).strip() != ""
        existing = candidates_by_code.get(normalized_code)
        if existing is None:
            candidates_by_code[normalized_code] = {
                **candidate,
                "CPT/HCPCS Code": raw_code,
                "quantity": normalize_estimate_quantity(raw_quantity),
                "quantity_explicit": quantity_is_explicit,
            }
        elif quantity_is_explicit and not existing.get("quantity_explicit"):
            existing["quantity"] = normalize_estimate_quantity(raw_quantity)
            existing["quantity_explicit"] = True

    candidates = list(candidates_by_code.values())
    encounter_key = "IPD" if str(encounter_type).upper() in {"IPD", "INPATIENT"} else "OPD"
    pricing_rows = build_cpt_bill_rows(candidates, encounter_type=encounter_key)
    sheet_name = "IPD Charges" if encounter_key == "IPD" else "OPD Charges"
    departments = load_chargemaster_department_map(
        PROJECT_ROOT / "Original_Hospital_IPD_OPD_Charges_200_Demo.xlsx",
        sheet_name,
    )
    table_rows = []
    total = 0.0
    unpriced_codes = []
    for row in pricing_rows:
        code = str(row.get("CPT/HCPCS Code", ""))
        quantity = normalize_estimate_quantity(row.get("Quantity", 1))
        unit_value = row.get("Estimated Rate", "Not available")
        try:
            unit_charge = float(unit_value)
        except (TypeError, ValueError):
            unit_charge = None
        if row.get("Rate Source Status") == "Found" and unit_charge is not None:
            gross_charge = round(quantity * unit_charge, 2)
            total += gross_charge
            pricing_status = "Priced"
            unit_display = f"${unit_charge:,.2f}"
            gross_display = f"${gross_charge:,.2f}"
        else:
            gross_charge = None
            pricing_status = "Unpriced"
            unit_display = "Unpriced"
            gross_display = "Unpriced"
            unpriced_codes.append(code)
        table_rows.append({
            "CPT/HCPCS Code": code,
            "Service Description": row.get("Procedure Description", "Not specified"),
            "Department": departments.get(normalize_billing_code(code), "Not specified"),
            "Quantity": quantity,
            "Unit Charge": unit_display,
            "Gross Charge": gross_display,
            "Pricing Status": pricing_status,
        })
    return table_rows, round(total, 2), list(dict.fromkeys(unpriced_codes))


def persist_consultation_cpt_results(record, cpt_results):
    history = record.get("cpt_results_history", [])
    history = list(history) if isinstance(history, list) else []
    known_candidates = {
        (normalize_billing_code(item.get("CPT/HCPCS Code")), str(item.get("Extracted Procedure", "")).casefold())
        for item in history if isinstance(item, dict)
    }
    for candidate in cpt_results or []:
        identity = (
            normalize_billing_code(candidate.get("CPT/HCPCS Code")),
            str(candidate.get("Extracted Procedure", "")).casefold(),
        )
        if identity not in known_candidates:
            history.append(dict(candidate))
            known_candidates.add(identity)
    record["cpt_results_history"] = history
    record["code_results"] = {
        **(record.get("code_results") if isinstance(record.get("code_results"), dict) else {}),
        "cpt": list(cpt_results or []),
    }
    save_db(st.session_state.records)


# ============================================================
# 4. REUSABLE COMPONENTS
# ============================================================
for _state_key, _state_value in {
    "consult_step": "patient",
    "consult_draft": {},
    "consult_audio_signature": None,
    "review_step": "cds",
    "soap_rev": 0,
    "active_code": None,
    "billing_patient_key": None,
    "billing_record_id": None,
    "kb_results": None,
}.items():
    if _state_key not in st.session_state:
        st.session_state[_state_key] = _state_value

CURRENT_USER = {"name": "NuuCare User", "role": "Clinical Operations", "initials": "NU"}
CONSULT_STEPS = ("Patient", "Record", "Clinical Review & CDS", "SOAP Note & Sign")
SIDEBAR_NAV = (
    ("dashboard", "Clinical Overview", ":material/dashboard:"),
    ("new_consult", "Consultations", ":material/assignment:"),
    ("review", "Clinical Review & CDS", ":material/verified_user:"),
    ("medications", "Medication & RTBC", ":material/medication:"),
    ("prior_auth", "Prior Authorization", ":material/task_alt:"),
    ("codes", "Coding & HCC", ":material/code:"),
    ("billing", "Billing & RCM", ":material/receipt_long:"),
    ("knowledge_base", "Knowledge Base", ":material/menu_book:"),
)
RCM_TOOLS_NAV = (
    ("ipd_billing", "IPD Billing", ":material/local_hospital:"),
    ("claim_generation", "Claim Generation", ":material/request_quote:"),
)
ADMIN_NAV = (
    ("ehr_data", "EHR Demo Data", ":material/database:"),
    ("admin_dashboard", "Admin Dashboard", ":material/admin_panel_settings:"),
    ("super_admin", "Super Admin", ":material/security:"),
)
TOP_NAV = (
    ("dashboard", "Overview"),
    ("new_consult", "Consultations"),
    ("review", "CDS Workbench"),
    ("codes", "Coding & Billing"),
    ("prior_auth", "Prior Auth"),
    ("admin_dashboard", "Oversight"),
)
TOP_NAV_GROUP = {
    "dashboard": "dashboard",
    "new_consult": "new_consult",
    "review": "review",
    "medications": "review",
    "knowledge_base": "review",
    "codes": "codes",
    "billing": "codes",
    "ipd_billing": "codes",
    "claim_generation": "codes",
    "prior_auth": "prior_auth",
    "admin_dashboard": "admin_dashboard",
    "super_admin": "admin_dashboard",
    "ehr_data": "admin_dashboard",
}
BACKEND_ERRORS = (RuntimeError, requests.exceptions.RequestException, OSError, ValueError)


def backend_error_message(error):
    """Prefer the FastAPI `detail` text over the bare HTTP status line."""
    response = getattr(error, "response", None)
    if response is not None:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if detail:
            return f"{detail} (HTTP {response.status_code})"
    return str(error)


def go_to(page):
    st.session_state.page = page


def start_new_consultation():
    st.session_state.scribe_result = None
    st.session_state.consult_step = "patient"
    st.session_state.consult_draft = {}
    st.session_state.consult_audio_signature = None
    st.session_state.page = "new_consult"


def open_record(record, destination="review"):
    st.session_state.current_record = record
    st.session_state.detected_insights = get_record_insights(record)
    st.session_state.insight_count = 1
    if destination == "review":
        st.session_state.review_step = "cds"
    go_to(destination)


def save_record(record):
    for index, existing in enumerate(st.session_state.records):
        if existing.get("id") == record.get("id"):
            st.session_state.records[index] = record
            break
    save_db(st.session_state.records)


def set_edit_summary(text):
    """Replace the working SOAP text and force the editor widget to pick it up."""
    st.session_state.edit_summary = text
    st.session_state.soap_rev += 1


def badge(text, tone="gray"):
    return f'<span class="nc-badge nc-b-{tone}">{escape(str(text))}</span>'


def initials(name):
    parts = [part for part in re.split(r"\s+", str(name or "").strip()) if part]
    return "".join(part[0] for part in parts[:2]).upper() or "?"


def render_topbar(active_page):
    group = TOP_NAV_GROUP.get(active_page)
    pending = sum(record.get("status") == "Pending" for record in st.session_state.records)
    count_html = f'<span class="nc-count">{pending}</span>' if pending else ""
    with st.container(key="nc_topbar"):
        brand_col, nav_col, right_col = st.columns([3.3, 6.1, 2.1], vertical_alignment="center")
        brand_col.markdown(
            '<div class="nc-brand"><div class="nc-brand-mark"></div><div>'
            '<div class="nc-brand-name">NuuCare</div><div class="nc-brand-sub">POWERED BY DATA COSMOS</div></div>'
            '<div class="nc-app-pills"><span class="nc-app-pill">Quote-to-Card</span>'
            '<span class="nc-app-pill on">Clinical Decision Platform</span></div></div>',
            unsafe_allow_html=True,
        )
        with nav_col:
            with st.container(horizontal=True, horizontal_alignment="center", gap="small"):
                for page_key, label in TOP_NAV:
                    state = "on" if group == page_key else "off"
                    st.button(label, key=f"topnav_{state}_{page_key}", on_click=go_to, args=(page_key,), width="content")
        right_col.markdown(
            f'<div class="nc-topbar-right"><span title="Notes pending sign-off">&#128276; Alerts{count_html}</span>'
            f'<span>&#9432; Help</span><span class="nc-avatar">{escape(CURRENT_USER["initials"])}</span></div>',
            unsafe_allow_html=True,
        )


def _sidebar_button(page_key, label, icon, active_page):
    state = "on" if page_key == active_page else "off"
    st.button(label, key=f"nav_{state}_{page_key}", icon=icon, on_click=go_to, args=(page_key,), width="stretch")


def render_sidebar(active_page):
    render_topbar(active_page)
    with st.sidebar:
        st.markdown('<div class="shell-section-label">&#8767; CLINICAL DECISION PLATFORM</div>', unsafe_allow_html=True)
        for page_key, label, icon in SIDEBAR_NAV:
            _sidebar_button(page_key, label, icon, active_page)

        st.markdown('<div class="shell-section-label muted">RCM TOOLS</div>', unsafe_allow_html=True)
        for page_key, label, icon in RCM_TOOLS_NAV:
            _sidebar_button(page_key, label, icon, active_page)

        st.markdown('<div class="shell-section-label muted">ADMINISTRATION</div>', unsafe_allow_html=True)
        for page_key, label, icon in ADMIN_NAV:
            _sidebar_button(page_key, label, icon, active_page)

        st.markdown('<div class="shell-divider"></div>', unsafe_allow_html=True)
        dark_mode = st.toggle("Dark mode", value=st.session_state.get("theme") == "Dark", key="theme_toggle")
        theme_value = "Dark" if dark_mode else "Light"
        if st.session_state.get("theme") != theme_value:
            st.session_state["theme"] = theme_value
            st.rerun()
        st.markdown(
            f'<div class="shell-user"><div class="user-avatar">{escape(CURRENT_USER["initials"])}</div>'
            f'<div><b>{escape(CURRENT_USER["name"])}</b><small>{escape(CURRENT_USER["role"])}</small></div></div>',
            unsafe_allow_html=True,
        )


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


def render_header(title, subtitle, show_new=True):
    eyebrow = {
        "ipd_billing": "BILLING & RCM",
        "claim_generation": "BILLING & RCM",
        "ehr_data": "ADMINISTRATION",
        "admin_dashboard": "OVERSIGHT",
        "super_admin": "OVERSIGHT",
    }.get(st.session_state.page, "CLINICAL DECISION PLATFORM")
    actions = page_header(eyebrow, title, subtitle, action_weight=2.6)
    if show_new:
        actions.button(
            "New Consultation", key=f"header_new_{st.session_state.page}", type="primary",
            icon=":material/add:", on_click=start_new_consultation, width="content",
        )


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
        + (f'<small>{escape(detail)}</small>' if detail else "")
        + f'</div>{trailing}</div>',
        unsafe_allow_html=True,
    )


def card_title(title, subtitle="", trailing=""):
    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:.6rem">'
        f'<div><div class="section-title">{escape(title)}</div>'
        + (f'<div class="section-subtitle">{escape(subtitle)}</div>' if subtitle else "")
        + f'</div><div>{trailing}</div></div>',
        unsafe_allow_html=True,
    )


def render_kpi_card(label, value, accent, context):
    st.markdown(
        f'<div class="kpi-card" style="border-top-color:{accent}">'
        f'<div class="kpi-title">{escape(label)}</div><div class="kpi-value">{value}</div>'
        f'<div class="kpi-context">{escape(context)}</div></div>', unsafe_allow_html=True,
    )


def render_status_badge(status):
    css_class = "status-approved" if status == "Approved" else "status-pending"
    label = "SIGNED" if status == "Approved" else "DRAFT"
    return f'<span class="status-pill {css_class}">{label}</span>'


def render_tiles(items):
    tiles = "".join(f'<div class="nc-tile"><small>{escape(label)}</small><b>{escape(str(value))}</b></div>' for label, value in items)
    st.markdown(f'<div class="nc-tiles">{tiles}</div>', unsafe_allow_html=True)


def render_html_table(headers, rows, row_classes=None):
    """rows hold pre-escaped HTML cell strings."""
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = []
    for index, row in enumerate(rows):
        css = f' class="{row_classes[index]}"' if row_classes and row_classes[index] else ""
        body.append(f"<tr{css}>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    st.markdown(f'<div class="nc-table-wrap"><table class="nc-table"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)


# ---------- EHR chart helpers ----------
@st.cache_data(ttl=60, show_spinner=False)
def fetch_ehr_patients():
    """Return the EHR patient list, or None when the backend is unavailable."""
    try:
        return HealthcareApiClient().list_patients().get("patients", [])
    except BACKEND_ERRORS:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_patient_chart(patient_id):
    try:
        return HealthcareApiClient().get_patient_chart(str(patient_id))
    except BACKEND_ERRORS:
        return {}


def record_patient_id(record):
    return str((record or {}).get("ehr_patient_id") or (record or {}).get("patient_id") or "")


def record_chart(record):
    patient_id = record_patient_id(record)
    return fetch_patient_chart(patient_id) if patient_id else {}


def parse_any_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for date_format in ("%d/%m/%Y", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            continue
    return None


def format_date(value, fallback="—"):
    parsed = parse_any_date(value)
    if parsed:
        return parsed.strftime("%d %b %Y")
    return str(value) if value else fallback


def to_number(value):
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def chart_rows(chart, sheet):
    rows = ((chart or {}).get("records") or {}).get(sheet) or []
    return [row for row in rows if isinstance(row, dict)]


def latest_first(rows, date_key):
    return sorted(rows, key=lambda row: parse_any_date(row.get(date_key)) or datetime.min, reverse=True)


def is_active_row(row):
    status = str(row.get("Status") or row.get("Clinical_Status") or "Active").strip().lower()
    return status in {"active", "confirmed", ""}


def age_from_dob(dob):
    parsed = parse_any_date(str(dob or "").split("T")[0])
    if not parsed:
        return ""
    today = datetime.now().date()
    born = parsed.date()
    return str(today.year - born.year - ((today.month, today.day) < (born.month, born.day)))


def primary_coverage(chart):
    coverages = chart_rows(chart, "Insurance")
    primary = [row for row in coverages if str(row.get("Priority", "")).lower() == "primary"]
    return (primary or coverages or [{}])[0]


def active_drug_allergies(chart):
    return [
        row for row in chart_rows(chart, "Allergies")
        if is_active_row(row) and str(row.get("Category", "Drug")).lower() in {"drug", "medication", ""}
    ]


def chart_context(chart):
    problems = [row.get("Problem_Name") for row in chart_rows(chart, "Problems") if is_active_row(row) and row.get("Problem_Name")]
    medications = [row.get("Medication") for row in chart_rows(chart, "Medications") if is_active_row(row) and row.get("Medication")]
    allergies = [row.get("Substance") for row in chart_rows(chart, "Allergies") if is_active_row(row) and row.get("Substance")]
    vitals_rows = latest_first(chart_rows(chart, "Vitals"), "Observed_Date")
    vitals = "—"
    if vitals_rows:
        latest = vitals_rows[0]
        parts = []
        if latest.get("Systolic_mmHg") and latest.get("Diastolic_mmHg"):
            parts.append(f'BP {latest["Systolic_mmHg"]}/{latest["Diastolic_mmHg"]}')
        if latest.get("Temperature_F"):
            parts.append(f'T {latest["Temperature_F"]}°F')
        if latest.get("SpO2_Percent"):
            parts.append(f'SpO₂ {latest["SpO2_Percent"]}%')
        if latest.get("Respiratory_Rate"):
            parts.append(f'RR {latest["Respiratory_Rate"]}')
        if latest.get("Heart_Rate_bpm"):
            parts.append(f'HR {latest["Heart_Rate_bpm"]}')
        vitals = " · ".join(parts) or "—"
    lab_rows = latest_first(chart_rows(chart, "Labs"), "Result_Date")
    a1c = next((row for row in lab_rows if "a1c" in str(row.get("Test_Name", "")).lower()), None)
    flagged = [row for row in lab_rows if str(row.get("Flag", "")).lower() not in {"", "normal"}]
    lab_parts = []
    if a1c:
        lab_parts.append(f'HbA1c {a1c.get("Result")}{a1c.get("Unit", "")} · {format_date(a1c.get("Result_Date"))}')
    for row in flagged[:1]:
        if row is not a1c:
            lab_parts.append(f'{row.get("Test_Name")} {row.get("Result")} {row.get("Unit", "")} ({row.get("Flag")})')
    return {
        "problems": " · ".join(problems[:3]) or "None recorded",
        "medications": " · ".join(medications[:3]) or "None recorded",
        "allergies": " · ".join(allergies) or "NKDA",
        "vitals": vitals,
        "labs": " · ".join(lab_parts) or "—",
    }


def render_context_strip(chart, record=None):
    if not chart or not chart.get("patient"):
        source = "No EHR patient is linked to this consultation, so chart context is unavailable."
        if record is not None and record_patient_id(record):
            source = "The EHR backend is unavailable; chart context could not be loaded."
        render_banner("Chart context unavailable", source, tone="warn", icon="&#9888;")
        return
    context = chart_context(chart)
    counts = chart.get("record_counts") or {}
    footer = " · ".join(f"{name.replace('_', ' ')} {count}" for name, count in counts.items() if name in {"Problems", "Medications", "Allergies", "Vitals", "Labs"})
    st.markdown(
        '<div class="nc-context">'
        f'<div><small>Problems</small><b>{escape(context["problems"])}</b></div>'
        f'<div><small>Medications</small><b>{escape(context["medications"])}</b></div>'
        f'<div><small>Allergies</small><b>{escape(context["allergies"])}</b></div>'
        f'<div><small>Vitals</small><b>{escape(context["vitals"])}</b></div>'
        f'<div><small>Labs</small><b>{escape(context["labs"])}</b></div>'
        f'<div class="nc-context-foot">EHR chart · {escape(footer)} · refreshed {datetime.now().strftime("%d %b %Y %H:%M")}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


ALLERGY_CROSS_REACTIVE = {
    "penicillin": ("penicillin", "amoxicillin", "ampicillin", "augmentin", "piperacillin", "nafcillin", "dicloxacillin"),
    "sulfa": ("sulfa", "sulfamethoxazole", "bactrim", "sulfasalazine"),
    "cephalosporin": ("cephalexin", "cefuroxime", "ceftriaxone", "cefdinir", "cefazolin"),
    "nsaid": ("ibuprofen", "naproxen", "diclofenac", "ketorolac", "aspirin"),
    "aspirin": ("aspirin",),
    "codeine": ("codeine",),
    "morphine": ("morphine",),
}


def medication_safety_checks(chart, plan_text=""):
    """Deterministic checks over the EHR chart and the current plan text."""
    if not chart or not chart.get("patient"):
        return []
    medications = [row for row in chart_rows(chart, "Medications") if is_active_row(row)]
    medication_text = " ".join(str(row.get("Medication", "")) for row in medications).lower()
    plan_lower = str(plan_text or "").lower()
    checks = []
    for allergy in active_drug_allergies(chart):
        substance = str(allergy.get("Substance") or "Drug").strip()
        terms = ALLERGY_CROSS_REACTIVE.get(substance.lower(), (substance.lower(),))
        hits = sorted({term for term in terms if term and (term in medication_text or term in plan_lower)})
        if hits:
            checks.append((f"{substance} allergy", "Attention", "red", "Related agent mentioned: " + ", ".join(hits)))
        else:
            checks.append((f"{substance} allergy", "Pass", "green", "No related agent in active meds or plan"))
    if "metformin" in medication_text:
        renal = latest_first(
            [row for row in chart_rows(chart, "Labs") if re.search(r"egfr|creatinine", str(row.get("Test_Name", "")), re.IGNORECASE)],
            "Result_Date",
        )
        egfr = next((row for row in renal if "egfr" in str(row.get("Test_Name", "")).lower()), None)
        value = to_number(egfr.get("Result")) if egfr else None
        if value is None:
            checks.append(("Metformin renal safety", "Review", "amber", "No eGFR result on file"))
        elif value < 30:
            checks.append(("Metformin renal safety", "Attention", "red", f"eGFR {value:g} — below 30"))
        elif value < 45:
            checks.append(("Metformin renal safety", "Review", "amber", f"eGFR {value:g} — 30 to 44"))
        else:
            checks.append(("Metformin renal safety", "Pass", "green", f"eGFR {value:g}"))
    base_names = [re.split(r"[\s\d]", str(row.get("Medication", "")).strip().lower(), maxsplit=1)[0] for row in medications]
    duplicates = sorted({name for name in base_names if name and base_names.count(name) > 1})
    if duplicates:
        checks.append(("Duplicate therapy", "Review", "amber", "Listed more than once: " + ", ".join(duplicates)))
    else:
        checks.append(("Duplicate therapy", "Pass", "green", f"{len(medications)} active medication(s)"))
    if len(medications) >= 5:
        checks.append(("Polypharmacy", "Review", "amber", f"{len(medications)} active medications"))
    else:
        checks.append(("Polypharmacy", "Pass", "green", "Fewer than 5 active medications"))
    return checks


def care_gaps(chart):
    if not chart or not chart.get("patient"):
        return []
    problems = chart_rows(chart, "Problems")
    problem_text = " ".join(f'{row.get("Problem_Name", "")} {row.get("ICD_10_CM", "")}' for row in problems if is_active_row(row)).lower()
    has_diabetes = "diabet" in problem_text or re.search(r"\be1[01]\.", problem_text)
    has_hypertension = "hypertension" in problem_text or re.search(r"\bi1[0-6]", problem_text)
    labs = latest_first(chart_rows(chart, "Labs"), "Result_Date")
    one_year_ago = datetime.now() - pd.Timedelta(days=365)
    gaps = []
    if has_diabetes:
        a1c = next((row for row in labs if "a1c" in str(row.get("Test_Name", "")).lower()), None)
        a1c_date = parse_any_date(a1c.get("Result_Date")) if a1c else None
        a1c_value = to_number(a1c.get("Result")) if a1c else None
        if not a1c or not a1c_date or a1c_date < one_year_ago:
            gaps.append(("HbA1c control", "Due", "amber", "No HbA1c in the last 12 months"))
        elif a1c_value is not None and a1c_value >= 8:
            gaps.append(("HbA1c control", "Open", "amber", f"Latest HbA1c {a1c_value:g}% (goal < 8%)"))
        else:
            gaps.append(("HbA1c control", "Met", "green", f"Latest HbA1c {a1c_value:g}%" if a1c_value is not None else "Recent HbA1c on file"))
        activity = " ".join(
            f'{row.get("Order_Description", "")} {row.get("Type", "")} {row.get("Department", "")}'
            for row in chart_rows(chart, "Orders_Procedures") + chart_rows(chart, "Encounters")
        ).lower()
        if re.search(r"eye|retin|ophthalm", activity):
            gaps.append(("Diabetic eye exam", "Met", "green", "Eye exam found in orders or encounters"))
        else:
            gaps.append(("Diabetic eye exam", "Open", "amber", "No eye exam found"))
        lab_names = " ".join(str(row.get("Test_Name", "")) for row in labs).lower()
        if "egfr" in lab_names and "albumin" in lab_names:
            gaps.append(("Kidney health evaluation", "Met", "green", "eGFR and uACR on file"))
        else:
            gaps.append(("Kidney health evaluation", "Due", "amber", "eGFR + uACR not both on file"))
    if has_hypertension:
        vitals = latest_first(chart_rows(chart, "Vitals"), "Observed_Date")
        latest = vitals[0] if vitals else {}
        systolic, diastolic = to_number(latest.get("Systolic_mmHg")), to_number(latest.get("Diastolic_mmHg"))
        if systolic is None or diastolic is None:
            gaps.append(("Blood pressure control", "Due", "amber", "No recent BP reading"))
        elif systolic < 140 and diastolic < 90:
            gaps.append(("Blood pressure control", "Met", "green", f"Latest BP {systolic:g}/{diastolic:g}"))
        else:
            gaps.append(("Blood pressure control", "Open", "amber", f"Latest BP {systolic:g}/{diastolic:g} (goal < 140/90)"))
    return gaps


def render_checklist(items, empty_message):
    if not items:
        st.markdown(f'<div class="code-empty">{escape(empty_message)}</div>', unsafe_allow_html=True)
        return
    dot_colors = {"red": "#e5484d", "amber": "#f59e0b", "green": "#22c55e", "gray": "#94a3b8", "violet": "#7c6cf0", "blue": "#2f6bed"}
    rows = "".join(
        f'<div class="nc-check"><div><span class="nc-dot" style="background:{dot_colors.get(tone, "#94a3b8")}"></span>{escape(label)}'
        f'<small>{escape(detail)}</small></div>{badge(status, tone)}</div>'
        for label, status, tone, detail in items
    )
    st.markdown(f'<div class="nc-checklist">{rows}</div>', unsafe_allow_html=True)


# ---------- Transcript, note and insight helpers ----------
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


def append_to_plan(current_text, line):
    text = (current_text or "").rstrip()
    bullet = f"• {line.strip()}"
    if bullet in text:
        return text
    if re.search(r"(?im)^\s*#*\s*plan\s*:?", text):
        return f"{text}\n{bullet}"
    return f"{text}\n\nPLAN\n{bullet}".strip()


EXCERPT_STOPWORDS = {
    "the", "and", "with", "without", "for", "patient", "established", "new", "office", "visit", "test",
    "exam", "unspecified", "automated", "complete", "single", "view", "views", "type", "blood", "level",
}


def best_excerpt(term, text, max_chars=260):
    """Return the sentence from the documentation that best supports a term."""
    words = {word for word in re.findall(r"[a-z0-9]+", str(term or "").lower()) if len(word) > 2 and word not in EXCERPT_STOPWORDS}
    if not words:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", clean_clinical_text(text)) if part.strip()]
    scored = []
    for sentence in sentences:
        lowered = sentence.lower()
        score = sum(1 for word in words if word in lowered)
        if score:
            scored.append((score, -len(sentence), sentence))
    if not scored:
        return ""
    excerpt = max(scored)[2]
    excerpt = re.sub(r"^(doctor|patient)\s*:\s*", "", excerpt, flags=re.IGNORECASE)
    return excerpt if len(excerpt) <= max_chars else excerpt[: max_chars - 1].rstrip() + "…"


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
        st.markdown(f'<div class="nc-label" style="margin-top:.35rem">{escape(heading.upper())}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="summary-text">{escape(value)}</div>', unsafe_allow_html=True)


def split_cds_sections(recommendations):
    text = clean_clinical_text(recommendations)
    sections = {"Recommendation": "", "Reasoning": "", "Safety Note": ""}
    pattern = r"(?im)^\s*#{0,6}\s*\**\s*(?:Clinical\s+)?(Recommendations?|Reasoning|Safety Note)\s*\**\s*:?\s*\**\s*$"
    matches = list(re.finditer(pattern, text))
    if matches:
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            label = match.group(1).title().rstrip("s") if match.group(1).lower().startswith("recommendation") else match.group(1).title()
            sections[label] = text[match.end():end].strip()
    else:
        sections["Recommendation"] = text
    return sections


def render_cds_recommendations(recommendations):
    sections = split_cds_sections(recommendations)
    tabs = st.tabs(list(sections.keys()))
    for tab, (label, content) in zip(tabs, sections.items()):
        with tab:
            if content:
                st.markdown(content)
            else:
                st.caption(f"No {label.lower()} provided.")


HIGH_PRIORITY_TERMS = ("urgent", "immediate", "emergency", "avoid", "contraindicat", "allerg", "severe", "sepsis", "red flag", "hospital", "admit")
MEDIUM_PRIORITY_TERMS = ("consider", "assess", "evaluate", "monitor", "adjust", "escalat", "start", "order")


def recommendation_items(recommendation_text):
    """Split the CDS recommendation section into individual actionable items."""
    text = re.sub(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", recommendation_text or "").strip()
    if not text:
        return []
    bullet_pattern = r"(?m)^\s*(?:[-*•]|\d+[.)])\s+"
    if re.search(bullet_pattern, text):
        chunks = [chunk.strip() for chunk in re.split(bullet_pattern, text) if chunk.strip()]
    else:
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    items = []
    for chunk in chunks:
        plain = re.sub(r"\*\*|__", "", chunk).strip()
        if plain.endswith(":") and "\n" not in plain:
            continue  # lead-in sentence or empty heading, not a recommendation
        bold = re.match(r"^\*\*(.+?)\*\*\s*:?\s*(.*)$", chunk, flags=re.DOTALL)
        if bold:
            title, body = bold.group(1).strip().rstrip(":"), re.sub(r"\*\*|__", "", bold.group(2)).strip()
        else:
            sentence = re.match(r"^(.{12,160}?[.:;])\s+(.*)$", plain, flags=re.DOTALL)
            title, body = (sentence.group(1).rstrip(".:;"), sentence.group(2).strip()) if sentence else (plain, "")
        lowered = plain.lower()
        if any(term in lowered for term in HIGH_PRIORITY_TERMS):
            priority = "HIGH"
        elif any(term in lowered for term in MEDIUM_PRIORITY_TERMS):
            priority = "MED"
        else:
            priority = "ROUTINE"
        items.append({"title": title, "body": body, "priority": priority})
    return items


def guideline_passages(retrieved_guidelines):
    text = str(retrieved_guidelines or "").strip()
    if not text or text.lower().startswith("no highly relevant context"):
        return []
    passages = []
    for block in re.split(r"(?m)^(?=Source:)", text):
        block = block.strip()
        if not block:
            continue
        first, _, rest = block.partition("\n")
        if first.startswith("Source:"):
            passages.append({"source": first.replace("Source:", "", 1).strip(), "text": rest.strip()})
        else:
            passages.append({"source": "Knowledge base", "text": block})
    return passages


def render_code_results(values, empty_message):
    if not values:
        st.markdown(f'<div class="code-empty">{escape(empty_message)}</div>', unsafe_allow_html=True)
        return
    if isinstance(values, list) and values and isinstance(values[0], dict):
        st.dataframe(pd.DataFrame(values), hide_index=True, width="stretch")
    else:
        for value in values if isinstance(values, list) else [values]:
            st.markdown(f"- {escape(str(value))}")


def render_patient_table(records, selected_date=None):
    if selected_date:
        records = [r for r in records if selected_date in (r.get("date"), r.get("approval_date"))]
        st.caption(f"Cases for {selected_date} ({len(records)})")
    per_page = 3 if selected_date else 6
    state_key = "chart_cases_page" if selected_date else "dash_page"
    total_pages = max(1, math.ceil(len(records) / per_page))
    st.session_state[state_key] = min(max(st.session_state[state_key], 1), total_pages)
    current_page = st.session_state[state_key]
    current_records = records[(current_page - 1) * per_page: current_page * per_page]
    st.markdown(
        '<div class="patient-table-header"><span>Patient</span><span>ID</span><span>Doctor</span>'
        '<span>Date</span><span>Note</span><span>Action</span></div>', unsafe_allow_html=True,
    )
    for row_index, record in enumerate(current_records):
        columns = st.columns([2.2, 1.15, 1.7, 1.55, 1.05, 1.15], vertical_alignment="center")
        columns[0].markdown(f'<div class="nc-patient"><div class="nc-initials">{escape(initials(record.get("name")))}</div><div><b>{escape(record.get("name", ""))}</b><small>{escape(str(record.get("age", "")))}y · {escape(record.get("gender", ""))}</small></div></div>', unsafe_allow_html=True)
        columns[1].markdown(f'<span class="table-meta">{escape(record.get("id", ""))}</span>', unsafe_allow_html=True)
        columns[2].markdown(f'<span class="table-meta">{escape(record.get("doctor", "N/A"))}</span>', unsafe_allow_html=True)
        date_text = record.get("approval_date") if record.get("status") == "Approved" else record.get("date", "")
        columns[3].markdown(f'<div class="table-two-line"><span>{escape(date_text or "")}</span><small>{escape(record.get("time", ""))}</small></div>', unsafe_allow_html=True)
        columns[4].markdown(render_status_badge(record.get("status", "Pending")), unsafe_allow_html=True)
        key_suffix = f'{record.get("id", "record")}_{row_index}_{current_page}'
        columns[5].button(
            "View" if record.get("status") == "Approved" else "Review", key=f"record_{key_suffix}",
            on_click=open_record, args=(record,), width="stretch",
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


# ---------- EHR demo data (administration) ----------
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
    workbook_exists = EHR_DATA_FILE.is_file()
    try:
        patient_response = client.list_patients()
        patients = patient_response.get("patients", [])
        backend_connected = True
    except (RuntimeError, requests.exceptions.RequestException, OSError, ValueError) as error:
        patients = []
        backend_connected = False
        st.warning(f"EHR backend unavailable. Showing the Excel source read-only. ({error})")

    if backend_connected:
        st.success(f"JSON EHR connected · {len(patients)} patient(s) · {EHR_JSON_FILE.name}")
        patient_options = {
            f"{item.get('display_name') or item.get('Legal_Name') or item.get('Patient_ID')} · {item.get('patient_id') or item.get('Patient_ID')}": str(item.get("patient_id") or item.get("Patient_ID"))
            for item in patients
        }
        patient_labels = list(patient_options)
        admin_patient_id = st.session_state.pop("admin_ehr_patient_id", None)
        admin_patient_index = next((index for index, patient_id in enumerate(patient_options.values()) if patient_id == admin_patient_id), 0)
        if patient_labels and admin_patient_id:
            st.session_state["ehr_patient_selection"] = patient_labels[admin_patient_index]
        selected_label = st.selectbox("Patient", patient_labels, index=admin_patient_index if patient_labels else None, placeholder="Select a patient", key="ehr_patient_selection")
        selected_patient_id = patient_options.get(selected_label) if selected_label else None
        chart = client.get_patient_chart(selected_patient_id) if selected_patient_id else {"patient": {}, "records": {}}

        overview_tab, edit_tab, records_tab, billing_tab, create_tab = st.tabs(
            ["Patient chart", "Edit patient", "Clinical records", "Bill history", "Create patient"]
        )
        with overview_tab:
            if chart.get("patient"):
                st.dataframe(pd.DataFrame([chart["patient"]]), width="stretch", hide_index=True)
                sheet_names = list(chart.get("records", {}))
                if sheet_names:
                    selected_sheet = st.selectbox("Chart section", sheet_names, key=f"ehr_chart_sheet_{selected_patient_id}")
                    st.dataframe(pd.DataFrame(chart["records"].get(selected_sheet, [])), width="stretch", hide_index=True)
                else:
                    st.info("No related clinical records are available for this patient.")
            else:
                st.info("Select a patient to view their chart.")

        with edit_tab:
            if chart.get("patient"):
                current = chart["patient"]
                with st.form(f"ehr_patient_edit_{selected_patient_id}"):
                    first, last = st.columns(2)
                    first_name = first.text_input("First name", value=str(current.get("First_Name") or ""))
                    last_name = last.text_input("Last name", value=str(current.get("Last_Name") or ""))
                    legal_name = st.text_input("Legal name", value=str(current.get("Legal_Name") or ""))
                    mrn = st.text_input("MRN", value=str(current.get("MRN") or ""))
                    city, state = st.columns(2)
                    city_value = city.text_input("City", value=str(current.get("City") or ""))
                    state_value = state.text_input("State", value=str(current.get("State") or ""))
                    email = st.text_input("Email", value=str(current.get("Email") or ""))
                    mobile = st.text_input("Mobile", value=str(current.get("Mobile") or ""))
                    if st.form_submit_button("Save patient changes", type="primary"):
                        try:
                            updated = client.update_patient(selected_patient_id, {
                                "First_Name": first_name, "Last_Name": last_name,
                                "Legal_Name": legal_name or f"{first_name} {last_name}".strip(),
                                "MRN": mrn, "City": city_value, "State": state_value,
                                "Email": email, "Mobile": mobile,
                            })
                            st.success(f"Updated {updated.get('Legal_Name') or selected_patient_id}.")
                            st.rerun()
                        except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                            st.error(f"Patient update failed: {error}")
                st.divider()
                confirm_delete = st.checkbox("I understand this permanently removes this synthetic patient and linked records.", key=f"ehr_delete_confirm_{selected_patient_id}")
                if st.button("Delete patient", type="secondary", disabled=not confirm_delete, key=f"ehr_delete_{selected_patient_id}"):
                    try:
                        client.delete_patient(selected_patient_id)
                        st.success("Patient and linked records deleted.")
                        st.rerun()
                    except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                        st.error(f"Patient deletion failed: {error}")
            else:
                st.info("Select a patient to edit or delete.")

        with records_tab:
            chart_records = chart.get("records", {})
            if selected_patient_id and chart_records:
                sheet_name = st.selectbox("Editable record sheet", list(chart_records), key=f"ehr_edit_sheet_{selected_patient_id}")
                records_frame = pd.DataFrame(chart_records.get(sheet_name, []))
                edited_records = st.data_editor(records_frame, num_rows="dynamic", width="stretch", hide_index=True, key=f"ehr_records_{selected_patient_id}_{sheet_name}")
                if st.button("Save record changes", key=f"ehr_save_records_{selected_patient_id}_{sheet_name}"):
                    try:
                        client.replace_patient_records(selected_patient_id, sheet_name, edited_records.to_dict("records"))
                        st.success(f"Saved {sheet_name} records.")
                        st.rerun()
                    except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                        st.error(f"Record update failed: {error}")
                with st.expander("Add a record"):
                    record_json = st.text_area("Record JSON", value="{}", key=f"ehr_new_record_json_{selected_patient_id}_{sheet_name}")
                    if st.button("Add record", key=f"ehr_add_record_{selected_patient_id}_{sheet_name}"):
                        try:
                            record_payload = json.loads(record_json)
                            if not isinstance(record_payload, dict):
                                raise ValueError("Record JSON must be an object.")
                            client.create_patient_record(selected_patient_id, sheet_name, record_payload)
                            st.success("Record added.")
                            st.rerun()
                        except (json.JSONDecodeError, ValueError, RuntimeError, requests.exceptions.RequestException) as error:
                            st.error(f"Record creation failed: {error}")
            else:
                st.info("Select a patient with chart records to edit them.")

        with billing_tab:
            if selected_patient_id:
                try:
                    bill_response = client.list_bills(selected_patient_id)
                    bills = bill_response.get("bills", [])
                except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                    bills = []
                    st.error(f"Bill history could not be loaded: {error}")
                if bills:
                    bill_labels = [f"{item.get('Bill_ID') or item.get('bill_id')} · {item.get('Status') or item.get('status')}" for item in bills]
                    chosen_bill = st.selectbox("Saved draft bill", range(len(bills)), format_func=lambda index: bill_labels[index], key=f"ehr_bill_history_{selected_patient_id}")
                    st.json(bills[chosen_bill])
                    st.info(BILLING_NOTICE)
                else:
                    st.info("No saved draft bills for this patient.")
            else:
                st.info("Select a patient to view saved bills.")

        with create_tab:
            with st.form("ehr_patient_create"):
                first, last = st.columns(2)
                new_first_name = first.text_input("First name")
                new_last_name = last.text_input("Last name")
                new_mrn = st.text_input("MRN (optional)")
                new_city = st.text_input("City")
                if st.form_submit_button("Create patient", type="primary"):
                    if not new_first_name.strip() or not new_last_name.strip():
                        st.error("First and last name are required.")
                    else:
                        try:
                            created = client.create_patient({
                                "First_Name": new_first_name.strip(),
                                "Last_Name": new_last_name.strip(),
                                "Legal_Name": f"{new_first_name.strip()} {new_last_name.strip()}",
                                "MRN": new_mrn.strip() or None,
                                "City": new_city.strip() or None,
                            })
                            st.success(f"Created {created.get('Legal_Name')} · {created.get('Patient_ID')}.")
                            st.rerun()
                        except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                            st.error(f"Patient creation failed: {error}")
    else:
        if not workbook_exists:
            st.error(f"The Excel fallback workbook was not found at {EHR_DATA_FILE}.")
            return
        try:
            workbook = load_ehr_demo_workbook(str(EHR_DATA_FILE))
        except FileNotFoundError as error:
            st.error(str(error))
            return
        st.info("Read-only Excel fallback is active. Patient edits, record writes, deletion, and saved-bill actions are disabled until the backend is available.")
        available_sheets = list(workbook)
        if not available_sheets:
            st.warning("No workbook sheets were loaded.")
            return
        selected_sheet = st.selectbox("Workbook sheet", available_sheets, index=available_sheets.index("Patient_Master") if "Patient_Master" in available_sheets else 0)
        frame = workbook.get(selected_sheet, pd.DataFrame())
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            st.caption(f"{len(frame)} row(s) · source remains unchanged: {EHR_DATA_FILE.name}")
            st.dataframe(frame, width="stretch", hide_index=True)
        else:
            st.info("This sheet has no tabular rows to display.")
        st.button("Create patient", disabled=True)
        st.button("Save patient or record changes", disabled=True)
        st.button("Generate saved bill", disabled=True)

# ============================================================
# 5. CLINICAL OVERVIEW
# ============================================================
def show_dashboard():
    render_sidebar("dashboard")
    actions = page_header("CLINICAL OVERVIEW", "Clinical Operations", "Consultation, documentation and coding throughput", action_weight=2.6)
    actions.button("New Consultation", key="dashboard_new", type="primary", icon=":material/add:", on_click=start_new_consultation, width="content")
    filter_search, filter_date, filter_status = st.columns([5, 2, 3])
    search_query = filter_search.text_input("Search patient or ID", placeholder="Search name / MRN / consultation ID").strip().lower()
    date_filter = filter_date.selectbox("Date", ["All Time", "Today", "Last 7 Days", "Last 30 Days", "Custom Date Range"])
    status_filter = filter_status.selectbox("Status", ["All Statuses", "Pending", "Approved"])
    custom_dates = st.date_input("Custom Range", value=(datetime.now().date(), datetime.now().date())) if date_filter == "Custom Date Range" else None
    today, filtered = datetime.now().date(), []
    for record in st.session_state.records:
        searchable = f'{record.get("name", "")} {record.get("id", "")} {record.get("mrn", "")} {record.get("ehr_patient_id", "")}'.lower()
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
    coded = sum(bool((r.get("code_results") or {}).get("cpt") or (r.get("code_results") or {}).get("icd10")) for r in filtered)
    kpis = st.columns(5)
    for col, item in zip(kpis, [
        ("Total Consultations", total, "#0e6b63", "All recorded consults"),
        ("Today's Volume", today_count, "#2f6bed", "Created today"),
        ("Pending Sign-off", pending, "#d97706", "SOAP drafts awaiting review"),
        ("Signed Notes", approved, "#15803d", "Completed documentation"),
        ("Coded Encounters", coded, "#7c3aed", "ICD-10 / CPT suggestions saved"),
    ]):
        with col: render_kpi_card(*item)
    chart_col, overview_col = st.columns([6.5, 3.5], gap="medium")
    with chart_col:
        with st.container(border=True):
            card_title("Consultation Activity", "Daily consultation throughput and sign-off")
            events = []
            for record in filtered:
                events.append({"Date": record.get("date"), "Metric": "New Consults"})
                if record.get("status") == "Approved": events.append({"Date": record.get("approval_date", record.get("date")), "Metric": "Signed"})
            if events:
                frame = pd.DataFrame(events)
                frame["Date"] = pd.to_datetime(frame["Date"], format="%d/%m/%Y", errors="coerce")
                counts = frame.dropna(subset=["Date"]).groupby(["Date", "Metric"]).size().reset_index(name="Count")
                chart = alt.Chart(counts).mark_line(point=True).encode(
                    x=alt.X("Date:T", title="Date", axis=alt.Axis(format="%b %d")),
                    y=alt.Y("Count:Q", title="Records"),
                    color=alt.Color("Metric:N", legend=alt.Legend(title="", orient="top"), scale=alt.Scale(range=["#0e6b63", "#2f6bed"])),
                    tooltip=[alt.Tooltip("Date:T", title="Date", format="%b %d, %Y"), "Metric:N", "Count:Q"],
                ).properties(height=210)
                st.altair_chart(chart, width="stretch")
            else:
                st.info("No consultation activity is available for the selected filters.")
    with overview_col:
        with st.container(border=True):
            approval_rate = round(approved / total * 100) if total else 0
            card_title("Operating Position", "Current documentation backlog")
            st.markdown(f'<div class="overview-grid" style="border:0;padding:.2rem 0"><div><small>Pending sign-off</small><b class="amber-text">{pending}</b></div><div><small>Sign-off rate</small><b class="green-text">{approval_rate}%</b></div><div><small>Today</small><b class="blue-text">{today_count}</b></div></div>', unsafe_allow_html=True)
            patients = fetch_ehr_patients()
            if patients is None:
                render_banner("EHR backend offline", "Start it with: python -m app", tone="warn", icon="&#9888;")
            else:
                render_banner(f"EHR connected · {len(patients)} patients", "FastAPI JSON EHR at 127.0.0.1:8000", tone="ok", icon="&#10003;")
    with st.container(border=True):
        card_title("Patient Records", "Clinical documentation queue")
        if filtered:
            render_patient_table(filtered)
        else:
            st.info("No clinical records found.")


# ============================================================
# 6. CONSULTATIONS (STEPS 1-2)
# ============================================================
def has_first_and_last_name(name):
    return len(str(name or "").split()) >= 2


def register_consultation_in_ehr(record, client=None, patient=None, dob=None, encounter_type="Office Visit"):
    client = client or HealthcareApiClient()
    patient = patient or {}
    patient_id = str(record.get("ehr_patient_id") or record.get("patient_id") or patient.get("Patient_ID") or patient.get("patient_id") or "")
    if not patient_id:
        patient_name = str(record.get("name", "")).strip()
        if not has_first_and_last_name(patient_name):
            raise ValueError("Enter the patient's first and last name before creating an EHR patient.")
        first_name, last_name = patient_name.split(maxsplit=1)
        if not dob:
            try:
                approximate_age = max(0, int(record.get("age", "")))
            except (TypeError, ValueError) as error:
                raise ValueError("A date of birth is required to create an EHR patient.") from error
            dob = f"{max(1900, datetime.now().year - approximate_age)}-01-01"
        patient = client.create_patient({
            "First_Name": first_name,
            "Last_Name": last_name,
            "DOB": dob,
            "Sex_at_Birth": record.get("gender", "Other"),
        })
        patient_id = str(patient.get("Patient_ID") or patient.get("patient_id") or "")
        if not patient_id:
            raise ValueError("The EHR patient response did not include a patient ID.")

    record["ehr_patient_id"] = patient_id
    record["patient_id"] = patient_id
    record["mrn"] = patient.get("MRN", record.get("mrn", ""))
    record["dob"] = patient.get("DOB", dob or record.get("dob", ""))
    try:
        service_date = datetime.strptime(record.get("date", ""), "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError):
        service_date = datetime.now().date().isoformat()

    if not record.get("ehr_encounter_id"):
        encounter = client.create_patient_record(patient_id, "Encounters", {
            "Encounter_ID": f"ENC-{uuid.uuid4().hex[:10].upper()}",
            "Patient_ID": patient_id,
            "Date": service_date,
            "Type": encounter_type,
            "Department": "Primary Care",
            "Provider": record.get("doctor", "Unassigned"),
            "Facility": "NuuCare Demo",
            "Status": "In Progress",
            "Primary_Diagnosis": record.get("patient_summary", ""),
            "Priority": "Routine",
        })
        record["ehr_encounter_id"] = encounter.get("Encounter_ID")
    if not record.get("ehr_encounter_id"):
        raise ValueError("The EHR encounter response did not include an encounter ID.")

    if not record.get("ehr_note_id"):
        note = client.create_patient_record(patient_id, "Clinical_Notes", {
            "Note_ID": f"NOTE-{uuid.uuid4().hex[:10].upper()}",
            "Patient_ID": patient_id,
            "Encounter_ID": record["ehr_encounter_id"],
            "Note_Type": "SOAP",
            "Service_Date": service_date,
            "Author": record.get("doctor", "Unassigned"),
            "Note_Summary": record.get("summary") or record.get("patient_summary", ""),
            "Status": "Draft" if record.get("status") == "Pending" else "Signed",
        })
        record["ehr_note_id"] = note.get("Note_ID")
    return record


def update_ehr_clinical_note(record, approved=False):
    patient_id = record.get("ehr_patient_id") or record.get("patient_id")
    note_id = record.get("ehr_note_id")
    if not patient_id or not note_id:
        return
    client = HealthcareApiClient()
    chart = client.get_patient_chart(str(patient_id))
    notes = chart.get("records", {}).get("Clinical_Notes", [])
    for note in notes:
        if str(note.get("Note_ID", "")) == str(note_id):
            note["Note_Summary"] = record.get("summary", "")
            note["Status"] = "Signed" if approved else "Draft"
            break
    else:
        return
    client.replace_patient_records(str(patient_id), "Clinical_Notes", notes)


def _consult_step_patient():
    draft = st.session_state.consult_draft
    with st.container(border=True):
        card_title("Patient", "Select an EHR patient or register a new one for this consultation")
        ehr_patients = fetch_ehr_patients()
        if ehr_patients is None:
            render_banner("EHR backend unavailable", "You can still enter patient details manually; EHR linking will be retried when the note is generated.", tone="warn", icon="&#9888;")
            ehr_patients = []
        ehr_patient_options = {"Create new EHR patient": None}
        ehr_patient_by_id = {}
        for item in ehr_patients:
            patient_id = str(item.get("patient_id") or item.get("Patient_ID") or "")
            if patient_id:
                patient_label = item.get("display_name") or item.get("Legal_Name") or patient_id
                ehr_patient_options[f"{patient_label} · {patient_id} · {item.get('MRN', '')}"] = patient_id
                ehr_patient_by_id[patient_id] = item
        labels = list(ehr_patient_options)
        default_label = next((label for label, value in ehr_patient_options.items() if value and value == draft.get("ehr_patient_id")), labels[0])
        selected_ehr_patient_label = st.selectbox("Patient source", labels, index=labels.index(default_label), key="consult_ehr_patient")
        selected_ehr_patient_id = ehr_patient_options[selected_ehr_patient_label]
        selected_ehr_patient = ehr_patient_by_id.get(selected_ehr_patient_id, {})
        patient_widget_key = selected_ehr_patient_id or "manual"
        same_patient = draft.get("ehr_patient_id") == selected_ehr_patient_id and draft
        default_name = draft.get("name") if same_patient else str(selected_ehr_patient.get("Legal_Name") or selected_ehr_patient.get("display_name") or "")
        default_dob = draft.get("dob") if same_patient else str(selected_ehr_patient.get("DOB") or "").split("T")[0]
        default_age = draft.get("age") if same_patient else age_from_dob(default_dob)
        gender_options = ["Male", "Female", "Other"]
        default_gender = draft.get("gender") if same_patient else str(selected_ehr_patient.get("Sex_at_Birth") or selected_ehr_patient.get("Gender") or "Male")
        gender_index = gender_options.index(default_gender) if default_gender in gender_options else 2
        if selected_ehr_patient_id:
            chart = fetch_patient_chart(selected_ehr_patient_id)
            coverage = primary_coverage(chart)
            allergies = ", ".join(str(row.get("Substance")) for row in active_drug_allergies(chart)) or "NKDA"
            st.markdown(
                '<div class="nc-badge-row" style="margin:.2rem 0 .4rem">'
                + badge(coverage.get("Payer_Name") or "No coverage on file", "blue")
                + badge(f'Eligibility {str(coverage.get("Status") or "unknown").upper()}', "green" if str(coverage.get("Status", "")).lower() == "active" else "amber")
                + badge(f"Allergy: {allergies}", "red" if allergies != "NKDA" else "gray")
                + badge(f'PCP {selected_ehr_patient.get("PCP") or "—"}', "gray")
                + "</div>",
                unsafe_allow_html=True,
            )
        left, right = st.columns(2, gap="large")
        patient_name = left.text_input("Full Name", value=default_name, key=f"consult_patient_name_{patient_widget_key}", placeholder="e.g. John Doe")
        name_is_valid = bool(selected_ehr_patient_id) or has_first_and_last_name(patient_name)
        patient_age = right.text_input("Age", value=default_age, key=f"consult_patient_age_{patient_widget_key}")
        patient_gender = left.selectbox("Gender", gender_options, index=gender_index, key=f"consult_patient_gender_{patient_widget_key}")
        patient_dob = right.text_input(
            "Date of birth (YYYY-MM-DD)", value=default_dob, key=f"consult_patient_dob_{patient_widget_key}",
            disabled=bool(selected_ehr_patient_id), placeholder="Required to create a new EHR patient",
        )
        doctor_name = left.text_input("Attending Doctor", value=draft.get("doctor", ""), placeholder="e.g. Dr. Sarah Jenkins", key="consult_doctor")
        encounter_options = ["Outpatient", "Inpatient"]
        consultation_encounter_type = right.selectbox(
            "Encounter type", encounter_options,
            index=encounter_options.index(draft.get("encounter_type", "Outpatient")), key="consult_encounter_type",
        )
        if patient_name.strip() and not name_is_valid:
            st.info("Enter both the patient's first and last name, or select an existing EHR patient.")

    def proceed():
        st.session_state.consult_draft = {
            "ehr_patient_id": selected_ehr_patient_id,
            "ehr_patient": selected_ehr_patient,
            "name": patient_name.strip(),
            "age": patient_age.strip(),
            "gender": patient_gender,
            "dob": patient_dob.strip(),
            "doctor": doctor_name.strip(),
            "encounter_type": consultation_encounter_type,
        }
        st.session_state.consult_step = "record"

    back_col, _, next_col = st.columns([2, 5, 3])
    back_col.button("← Back to Overview", key="consult_back_overview", on_click=go_to, args=("dashboard",), width="stretch")
    next_col.button(
        "Proceed to Record →", key="consult_to_record", type="primary", on_click=proceed, width="stretch",
        disabled=not patient_name.strip() or not name_is_valid,
    )


def _audio_duration(audio_bytes):
    try:
        import wave
        with wave.open(io.BytesIO(audio_bytes)) as handle:
            seconds = handle.getnframes() / float(handle.getframerate() or 1)
        return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"
    except Exception:
        return "--:--"


def _format_size(size):
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size} B"


def _create_consultation_record():
    draft = st.session_state.consult_draft
    encounter = st.session_state.scribe_result or {}
    try:
        encounter = HealthcareApiClient().add_cds(encounter)
    except (RuntimeError, requests.exceptions.HTTPError) as error:
        if encounter.get("recommendations"):
            st.warning(f"CDS could not be refreshed; keeping the existing recommendations. Details: {backend_error_message(error)}")
        else:
            st.warning(f"CDS is unavailable; the consultation will be saved without recommendations. Details: {backend_error_message(error)}")
    transcript = encounter.get("transcript", "")
    doctor, now = draft.get("doctor") or "Doctor", datetime.now()
    selected_ehr_patient_id = draft.get("ehr_patient_id")
    record = {
        "id": f"PT-{str(uuid.uuid4().int)[:6]}", "name": draft.get("name", ""), "age": draft.get("age", ""),
        "gender": draft.get("gender", "Other"), "doctor": doctor, "date": now.strftime("%d/%m/%Y"),
        "time": now.strftime("%H:%M"), "status": "Pending", "transcript": transcript,
        "transcript_data": parse_transcript_entries(transcript, doctor, draft.get("name", "Patient")),
        "summary": encounter.get("soap_note", ""), "patient_summary": encounter.get("patient_summary", ""),
        "key_highlights": encounter.get("key_highlights", []), "retrieved_guidelines": encounter.get("retrieved_guidelines", ""),
        "recommendations": encounter.get("recommendations", ""), "ehr_patient_id": selected_ehr_patient_id,
        "ehr_encounter_type": draft.get("encounter_type", "Outpatient"), "dob": draft.get("dob", ""),
        "cds_requested": bool(encounter.get("recommendations")), "analysis_version": ANALYSIS_VERSION,
    }
    try:
        register_consultation_in_ehr(
            record,
            patient=draft.get("ehr_patient") or {},
            dob=draft.get("dob") or None,
            encounter_type=draft.get("encounter_type", "Outpatient"),
        )
        fetch_ehr_patients.clear()
        fetch_patient_chart.clear()
    except (RuntimeError, requests.exceptions.RequestException, ValueError, KeyError) as error:
        st.warning(f"Consultation saved locally, but EHR registration was unavailable: {error}")
    st.session_state.records.insert(0, record)
    save_db(st.session_state.records)
    st.session_state.consult_step = "patient"
    st.session_state.consult_draft = {}
    st.session_state.scribe_result = None
    st.session_state.consult_audio_signature = None
    open_record(record)


def _transcribe_audio(audio_bytes):
    recognizer = sr.Recognizer()
    with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
        return recognizer.recognize_google(recognizer.record(source))


def _process_consultation(transcript=None, audio=None):
    """Transcribe (when audio is given) and generate the SOAP draft, with visible progress."""
    with st.status("Processing consultation…", expanded=True) as status:
        try:
            if audio is not None:
                st.write("Transcribing audio with Google Speech Recognition…")
                transcript = _transcribe_audio(audio["bytes"])
                st.write(f"Transcript captured · {len(transcript.split())} words")
            st.write("Generating SOAP draft and key highlights…")
            st.session_state.scribe_result = HealthcareApiClient().scribe_encounter(transcript)
            st.session_state.consult_audio_signature = audio["signature"] if audio else None
            status.update(label="Transcript and SOAP draft ready", state="complete", expanded=False)
            return True
        except sr.UnknownValueError:
            status.update(label="No speech could be recognised — record closer to the microphone or upload a clearer file", state="error")
        except sr.RequestError as error:
            status.update(label=f"Speech recognition service unreachable: {error}", state="error")
        except ValueError as error:
            status.update(label=f"Unsupported or unreadable audio (use WAV, FLAC or AIFF): {error}", state="error")
        except (RuntimeError, requests.exceptions.RequestException) as error:
            status.update(label=f"Clinical summary service failed: {backend_error_message(error)}", state="error")
    return False


def _consult_step_record():
    draft = st.session_state.consult_draft
    context_badges = (
        '<div class="nc-badge-row" style="justify-content:flex-end">'
        + badge(draft.get("name") or "Patient", "gray")
        + badge(draft.get("ehr_patient_id") or "New EHR patient", "blue" if draft.get("ehr_patient_id") else "amber")
        + badge(draft.get("encounter_type", "Outpatient"), "violet")
        + badge(draft.get("doctor") or "Attending not set", "gray")
        + "</div>"
    )
    with st.container(border=True):
        card_title("Record Consultation", "Capture the clinical conversation with the microphone, or upload a recording", trailing=context_badges)
        render_banner("Use synthetic data only in this environment", "Avoid recording or uploading real PHI unless your environment is explicitly approved.", tone="warn", icon="&#9888;")
        record_col, upload_col = st.columns(2, gap="medium")
        with record_col:
            st.markdown('<div class="nc-label" style="margin-top:.5rem">&#127908; Record with microphone</div>', unsafe_allow_html=True)
            recorded = st.audio_input("Record consultation", key="consult_audio_mic", label_visibility="collapsed")
            st.caption("Click the microphone to start and again to stop. Re-record any time before transcribing.")
        with upload_col:
            st.markdown('<div class="nc-label" style="margin-top:.5rem">&#128193; Upload audio file</div>', unsafe_allow_html=True)
            uploaded = st.file_uploader(
                "Upload consultation audio", type=["wav", "flac", "aif", "aiff"], key="consult_audio_upload",
                label_visibility="collapsed", help="Supported: WAV, FLAC, AIFF",
            )

        if recorded and uploaded:
            source = st.radio("Audio to transcribe", ["Microphone recording", "Uploaded file"], horizontal=True, key="consult_audio_source")
            audio_file = recorded if source == "Microphone recording" else uploaded
        else:
            audio_file = recorded or uploaded
        audio = None
        if audio_file:
            audio_bytes = audio_file.getvalue()
            source_name = "Microphone recording" if audio_file is recorded else getattr(audio_file, "name", "Uploaded file")
            audio = {"bytes": audio_bytes, "name": source_name, "signature": (source_name, len(audio_bytes))}

        result = st.session_state.scribe_result
        transcribed_this = bool(result and audio and st.session_state.consult_audio_signature == audio["signature"])
        if transcribed_this or (result and not audio):
            status_text = "Transcribed"
        elif audio:
            status_text = "Ready to transcribe"
        else:
            status_text = "Awaiting audio"
        st.markdown(
            '<div class="nc-upload-tiles" style="margin-top:.4rem">'
            f'<div><small>Source</small><b>{escape(audio["name"]) if audio else "No audio yet"}</b></div>'
            f'<div><small>Duration</small><b>{_audio_duration(audio["bytes"]) if audio else "--:--"}</b></div>'
            f'<div><small>Size</small><b>{_format_size(len(audio["bytes"])) if audio else "0 B"}</b></div>'
            f'<div><small>Status</small><b>{status_text}</b></div></div>',
            unsafe_allow_html=True,
        )
        if st.button(
            "Re-transcribe and regenerate summary" if transcribed_this else "Transcribe and Generate Summary",
            key="consult_transcribe", type="secondary" if transcribed_this else "primary",
            icon=":material/graphic_eq:", width="stretch", disabled=audio is None,
        ):
            if _process_consultation(audio=audio):
                st.rerun()

        with st.expander("No audio? Paste a transcript instead"):
            pasted = st.text_area("Transcript", height=150, placeholder="Doctor: ...\nPatient: ...", key="consult_transcript_paste", label_visibility="collapsed")
            if st.button("Generate summary from transcript", key="consult_paste_go", disabled=not pasted.strip()):
                if _process_consultation(transcript=pasted.strip()):
                    st.rerun()

        if result:
            transcript_col, summary_col = st.columns(2, gap="medium")
            with transcript_col:
                st.markdown('<div class="nc-label" style="margin-top:.6rem">TRANSCRIPT</div>', unsafe_allow_html=True)
                st.text_area("Generated transcript", value=result.get("transcript", ""), height=220, disabled=True, label_visibility="collapsed")
            with summary_col:
                st.markdown('<div class="nc-label" style="margin-top:.6rem">DRAFT SUMMARY</div>', unsafe_allow_html=True)
                render_summary(result.get("soap_note") or result.get("patient_summary", "Not available"))
                for highlight in result.get("key_highlights", []):
                    st.markdown(f"- {highlight}")

    back_col, _, next_col = st.columns([2, 4.5, 3.5])
    if back_col.button("← Back to Patient", key="consult_back_patient", width="stretch"):
        st.session_state.consult_step = "patient"
        st.rerun()
    if next_col.button("Proceed to Clinical Review & CDS →", key="consult_to_cds", type="primary", width="stretch", disabled=not st.session_state.scribe_result):
        with st.spinner("Running clinical decision support and saving the consultation..."):
            _create_consultation_record()
        st.rerun()


def show_new_consultation():
    render_sidebar("new_consult")
    step = st.session_state.consult_step
    actions = page_header("CONSULTATIONS", "New Consultation", "Record encounter details and transcribe consultation audio.", action_weight=2.4)
    actions.button("Start over", key="consult_reset", type="tertiary", on_click=start_new_consultation)
    render_stepper(0 if step == "patient" else 1)
    if step == "record" and st.session_state.consult_draft:
        _consult_step_record()
    else:
        st.session_state.consult_step = "patient"
        _consult_step_patient()


# ============================================================
# 7. CLINICAL REVIEW & CDS (STEPS 3-4)
# ============================================================
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
        return st.data_editor(frame, width="stretch", num_rows="dynamic", hide_index=True,
            column_config={"Speaker": st.column_config.SelectboxColumn("Speaker", options=[doctor, patient, "Unknown"], required=True), "Text": st.column_config.TextColumn("Spoken Text", width="large")})


def record_subtitle(record):
    parts = [
        record.get("name", ""), record.get("id", ""), record.get("ehr_encounter_id") or "",
        record.get("doctor", ""), format_date(record.get("date"), ""),
    ]
    return " · ".join(str(part) for part in parts if part)


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
        st.text_area("Transcript", value=record.get("transcript", "No transcript available."), height=220, disabled=True, label_visibility="collapsed")
        if not is_approved:
            if st.button("Regenerate SOAP draft from transcript", key="review_regen_soap", disabled=not record.get("transcript", "").strip()):
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
        + f'{decision_html}</div>',
        unsafe_allow_html=True,
    )
    if is_approved:
        return

    def decide(status, note=""):
        decisions[str(index)] = {"status": status, "note": note, "at": datetime.now().strftime("%d %b %H:%M"), "title": item["title"]}
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
            st.button("Save modification", key=f"cds_modify_{index}", on_click=lambda: decide("modified", st.session_state.get(f"cds_modify_text_{index}", "").strip()))
        with st.popover("Override…"):
            reason = st.text_input("Override reason", key=f"cds_override_reason_{index}", placeholder="Clinical rationale")
            st.button("Record override", key=f"cds_override_{index}", disabled=not reason.strip(), on_click=lambda: decide("overridden", st.session_state.get(f"cds_override_reason_{index}", "").strip()))
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
            tone="warn", icon="&#128737;",
        )
    main_col, side_col = st.columns([6.3, 3.7], gap="medium")
    with main_col:
        with st.container(border=True):
            if not is_approved:
                with st.container(horizontal=True, horizontal_alignment="right"):
                    regenerate = st.button("↻ Regenerate CDS", key="review_regen_cds", type="tertiary", disabled=not record.get("transcript", "").strip())
                if regenerate:
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
                    st.caption("Priority is derived from the recommendation wording (e.g. avoid, urgent, allergy → HIGH). Clinician judgment governs.")
                else:
                    st.markdown('<div class="code-empty">No CDS recommendations yet. Use “Regenerate CDS” to request them.</div>', unsafe_allow_html=True)
            with reasoning_tab:
                st.markdown(sections["Reasoning"] or "_No reasoning provided._")
            with evidence_tab:
                if passages:
                    for passage in passages:
                        st.markdown(f'<div class="nc-excerpt" style="margin-bottom:.45rem"><small>§ {escape(passage["source"])}</small><div class="nc-rec-body">{escape(passage["text"][:900])}</div></div>', unsafe_allow_html=True)
                else:
                    st.caption("No guideline passages were retrieved for this encounter.")
            with safety_tab:
                st.markdown(sections["Safety Note"] or "_No safety note provided._")
            with history_tab:
                history = list(record.get("review_history") or [])
                history.insert(0, {"at": f'{record.get("date", "")} {record.get("time", "")}', "action": "Consultation created"})
                if record.get("approval_date"):
                    history.append({"at": record["approval_date"], "action": "Note signed"})
                st.dataframe(pd.DataFrame(history).rename(columns={"at": "When", "action": "Event"}), hide_index=True, width="stretch")
    with side_col:
        plan_text = f'{recommendation_text}\n{st.session_state.edit_summary}'
        with st.container(border=True):
            card_title("Medication Safety", "Deterministic checks over the EHR chart and plan")
            render_checklist(medication_safety_checks(chart, plan_text), "Link an EHR patient to run medication checks.")
        with st.container(border=True):
            coverage = primary_coverage(chart) if chart else {}
            card_title("Care Gaps · VBC", f'{coverage.get("Plan_Type") or "Commercial"} measure set')
            render_checklist(care_gaps(chart), "No quality measure applies to the active problem list.")
        with st.container(border=True):
            card_title("Coding & Billing", "ICD-10 / CPT suggestions and the guarded draft bill")
            code_col, bill_col = st.columns(2)
            code_col.button("ICD-10 / CPT codes →", key="cds_open_codes", type="secondary", width="stretch", on_click=open_record, args=(record, "codes"))
            bill_col.button("Bill generation →", key="cds_open_billing", type="secondary", width="stretch",
                            on_click=lambda: st.session_state.update(page="billing", billing_record_id=record.get("id"), current_record=record))
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
                if add_col.button("Add Insight", width="stretch", disabled=st.session_state.insight_count >= 6, key="cds_add_insight"):
                    st.session_state.insight_count += 1
                    st.rerun()
                push_col.button("Push to Note", type="primary", width="stretch", on_click=push_selected_insights_to_note, key="cds_push_insights")
    back_col, _, next_col = st.columns([2, 5, 3])
    if back_col.button("← Back to Record", key="review_cds_back", width="stretch"):
        st.session_state.review_step = "record"
        st.rerun()
    if next_col.button("Proceed to SOAP Note →", key="review_cds_next", type="primary", width="stretch"):
        st.session_state.review_step = "soap"
        st.rerun()


def _review_step_soap(record, is_approved):
    left, right = st.columns([6.2, 3.8], gap="medium")
    with left:
        with st.container(border=True):
            status_badge = badge("SIGNED · locked", "green") if is_approved else badge("SOAP draft", "amber")
            card_title("SOAP Note", "Signed notes are locked; drafts can be edited before signing", trailing=status_badge)
            editor_key = f"soap_editor_{record.get('id')}_{st.session_state.soap_rev}"
            if not is_approved and editor_key in st.session_state:
                st.session_state.edit_summary = st.session_state[editor_key]
            render_summary(st.session_state.edit_summary)
            if not is_approved:
                with st.expander("Edit SOAP draft", expanded=False):
                    st.text_area(
                        "Edit clinical summary", value=st.session_state.edit_summary, height=260,
                        key=editor_key, label_visibility="collapsed",
                    )
        edited_df = render_transcript(record, read_only=is_approved)
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
            _log_history(record, "Note signed and locked")
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
    codes_col.button("View ICD-10 / CPT codes →", key="soap_open_codes", type="tertiary", on_click=open_record, args=(record, "codes"))
    if is_approved:
        draft_col.button("Billing →", key="soap_open_billing", width="stretch",
                         on_click=lambda: st.session_state.update(page="billing", billing_record_id=record.get("id"), current_record=record))
        final_col.button("Proceed to Coding →", key="review_to_coding", type="primary", width="stretch", on_click=go_to, args=("codes",))
    else:
        if draft_col.button("Save Draft", key="review_soap_draft", width="stretch"):
            if persist(False):
                st.success("Draft and audit trail updated.")
        if final_col.button("Sign & Finalize ✓", key="review_soap_sign", type="primary", width="stretch"):
            if persist(True):
                st.session_state.code_record_id = None
                go_to("codes")
                st.rerun()


def show_review_note():
    render_sidebar("review")
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
    actions = page_header(eyebrow, title, record_subtitle(record), action_weight=3.2)
    if not is_approved:
        if actions.button("Save Draft", key="review_header_save", type="tertiary"):
            _save_note_draft(record)
    else:
        actions.markdown(badge("SIGNED", "green"), unsafe_allow_html=True)
    actions.button("All consultations", key="review_header_list", type="tertiary", on_click=lambda: st.session_state.update(current_record=None))
    render_stepper({"record": 1, "cds": 2, "soap": 3}[step])
    chart = record_chart(record)
    if step in {"record", "cds"}:
        render_context_strip(chart, record)
    if step == "record":
        _review_step_record(record, is_approved)
    elif step == "cds":
        _review_step_cds(record, is_approved, chart)
    else:
        _review_step_soap(record, is_approved)


# ============================================================
# 8. CODING & HCC
# ============================================================
# Small, explicitly labelled demo subset of CMS-HCC V28 mappings.
HCC_V28_DEMO = {
    "E10.9": "HCC 38", "E11.9": "HCC 38",
    "E66.01": "HCC 48",
    "I48.91": "HCC 238",
    "I50.9": "HCC 226",
    "J44.9": "HCC 280",
    "N18.4": "HCC 327", "N18.5": "HCC 326",
}
EM_MDM_LEVELS = {
    "99202": "Straightforward", "99203": "Low", "99204": "Moderate", "99205": "High",
    "99212": "Straightforward", "99213": "Low", "99214": "Moderate", "99215": "High",
}
CODE_STATUS_TONES = {
    "Accepted": "green", "Rejected": "red", "Query sent": "amber", "Suggested": "amber",
    "Symptom only": "violet", "Ordered": "violet", "Planned": "violet", "Discussed": "violet",
    "Performed": "blue", "E/M check": "amber", "Unverified": "gray",
}
MEAT_TERMS = {
    "M": ("monitor", "follow", "stable", "improv", "worse", "symptom", "progress", "history", "since"),
    "E": ("test", "result", "lab", "exam", "a1c", "x-ray", "level", "review", "vital", "reading"),
    "A": ("assess", "diagnos", "consistent", "likely", "impression", "controlled", "uncontrolled", "exacerbation"),
    "T": ("medication", "prescri", "continue", "start", "mg", "refer", "therapy", "treat", "inhaler", "metformin"),
}


def match_score(term, description):
    if not term or not description:
        return None
    try:
        from rapidfuzz import fuzz
        return int(round(fuzz.token_set_ratio(str(term).lower(), str(description).lower())))
    except ImportError:
        from difflib import SequenceMatcher
        return int(round(SequenceMatcher(None, str(term).lower(), str(description).lower()).ratio() * 100))


def record_documentation(record):
    return f'{record.get("transcript", "")}\n{record.get("summary", "")}'


def build_code_rows(record, code_results):
    documentation = record_documentation(record)
    decisions = record.get("code_decisions") or {}
    rows = []
    for item in code_results.get("icd10", []) or []:
        code = str(item.get("ICD-10 Code") or "").strip()
        if not code:
            continue
        term = item.get("Extracted Condition", "")
        auto_status = "Symptom only" if code.upper().startswith("R") else "Suggested"
        key = f"ICD:{code}"
        rows.append({
            "key": key, "type": "ICD-10-CM", "code": code, "term": term,
            "description": item.get("Matched Disease/Injury", ""), "hcc": HCC_V28_DEMO.get(code.upper(), ""),
            "match": match_score(term, item.get("Matched Disease/Injury", "")),
            "status": decisions.get(key, {}).get("status") or auto_status,
            "excerpt": best_excerpt(term, documentation),
        })
    for item in code_results.get("cpt", []) or []:
        code = str(item.get("CPT/HCPCS Code") or "").strip()
        if not code:
            continue
        term = item.get("Extracted Procedure", "")
        status_procedure, status_documentation = _procedure_status_inputs(term, documentation)
        procedure_status = classify_procedure_status(status_procedure, status_documentation) if classify_procedure_status else "unknown"
        if code in EM_MDM_LEVELS:
            auto_status = "E/M check"
        else:
            auto_status = {
                "performed": "Performed", "ordered_not_performed": "Ordered", "planned": "Planned",
                "recommended": "Planned", "discussed": "Discussed",
            }.get(procedure_status, "Unverified")
        key = f"CPT:{code}"
        rows.append({
            "key": key, "type": "CPT · E/M" if code in EM_MDM_LEVELS else "CPT", "code": code, "term": term,
            "description": item.get("Matched Procedure/Service", ""), "hcc": "",
            "match": match_score(term, item.get("Matched Procedure/Service", "")),
            "status": decisions.get(key, {}).get("status") or auto_status, "procedure_status": procedure_status,
            "excerpt": best_excerpt(term, documentation),
        })
    return rows


def set_code_decision(record, keys, status, note=""):
    decisions = record.setdefault("code_decisions", {})
    audit = record.setdefault("coding_audit", [])
    stamp = datetime.now().strftime("%d %b %Y %H:%M")
    for key in keys:
        decisions[key] = {"status": status, "at": stamp, "note": note}
        audit.append({"at": stamp, "code": key, "action": status, "note": note})
    save_record(record)


def meat_signals(text):
    lowered = str(text or "").lower()
    return {letter: any(term in lowered for term in terms) for letter, terms in MEAT_TERMS.items()}


def ensure_code_results(record, insights):
    """Run (or reuse) code matching for this record, mirroring the cached signature logic."""
    current_signature = get_code_results_signature(insights, record.get("transcript", ""))
    cached_results_are_stale = (
        st.session_state.code_record_id != record.get("id")
        or st.session_state.code_results_signature != current_signature
        or st.session_state.code_matcher_version != CODE_MATCHER_VERSION
    )
    if cached_results_are_stale:
        with st.spinner("Matching ICD-10 and CPT/HCPCS codes..."):
            st.session_state.code_results = get_backend_code_suggestions(insights, record.get("transcript", ""))
            persist_consultation_cpt_results(record, st.session_state.code_results.get("cpt", []))
            record["code_results"]["icd10"] = list(st.session_state.code_results.get("icd10", []))
            save_record(record)
        st.session_state.code_record_id = record.get("id")
        st.session_state.code_results_signature = current_signature
        st.session_state.code_matcher_version = CODE_MATCHER_VERSION
    return current_signature


def _render_code_table(record, rows):
    widths = [0.45, 2.7, 1.0, 0.85, 0.85, 1.35, 1.2, 0.7]
    header = st.columns(widths, vertical_alignment="center")
    for col, label in zip(header, ("", "Documentation", "Type", "Code", "HCC", "Match", "Status", "")):
        col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
    st.markdown('<div class="nc-rule"></div>', unsafe_allow_html=True)
    selected_keys = []
    for row in rows:
        is_active = st.session_state.active_code == row["key"]
        with st.container(key=f"coderow_{'sel' if is_active else 'off'}_{re.sub(r'[^A-Za-z0-9]', '_', row['key'])}"):
            cols = st.columns(widths, vertical_alignment="center")
            if cols[0].checkbox("select", key=f"code_pick_{record.get('id')}_{row['key']}", label_visibility="collapsed"):
                selected_keys.append(row["key"])
            cols[1].markdown(f'<div class="nc-cell">{escape(row["description"] or row["term"])}<div class="nc-meta">{escape(row["term"])}</div></div>', unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell muted">{escape(row["type"])}</div>', unsafe_allow_html=True)
            cols[3].markdown(f'<div class="nc-cell"><b>{escape(row["code"])}</b></div>', unsafe_allow_html=True)
            cols[4].markdown(badge(row["hcc"], "blue") if row["hcc"] else '<span class="nc-cell muted">—</span>', unsafe_allow_html=True)
            if row["match"] is None:
                cols[5].markdown('<span class="nc-cell muted">—</span>', unsafe_allow_html=True)
            else:
                cols[5].markdown(f'<div class="nc-meter" style="margin:0"><span><em style="width:{row["match"]}%"></em></span>{row["match"]}%</div>', unsafe_allow_html=True)
            cols[6].markdown(badge(row["status"], CODE_STATUS_TONES.get(row["status"], "gray")), unsafe_allow_html=True)
            cols[7].button("View", key=f"code_view_{row['key']}", type="tertiary", on_click=lambda key=row["key"]: st.session_state.update(active_code=key))
    return selected_keys


def _render_evidence_panel(record, row, is_signed):
    with st.container(border=True):
        match_badge = badge(f'{row["match"]}% match', "green" if (row["match"] or 0) >= 85 else "amber") if row["match"] is not None else ""
        card_title("Evidence & Provenance", f'{row["code"]} · {row["type"]}', trailing=match_badge)
        excerpt = row["excerpt"] or "No supporting sentence was found in the transcript or SOAP note."
        st.markdown(f'<div class="nc-excerpt"><small>Note excerpt</small><q>{escape(excerpt)}</q></div>', unsafe_allow_html=True)
        if row["type"] == "ICD-10-CM":
            signals = meat_signals(f'{row["excerpt"]} {record.get("summary", "")}' if row["excerpt"] else "")
            labels = {"M": "Monitor", "E": "Evaluate", "A": "Assess", "T": "Treat"}
            meat = " · ".join(f'{letter}: {labels[letter]} {"✓" if found else "—"}' for letter, found in signals.items())
            st.markdown(f'<div class="nc-label" style="margin-top:.7rem">Why this code · MEAT (keyword scan)</div><div class="nc-cell">{escape(meat)}</div>', unsafe_allow_html=True)
            if "unspecified" in str(row["description"]).lower():
                st.markdown(
                    '<div class="nc-cdi" style="margin-top:.6rem"><div class="nc-label">CDI opportunity</div>'
                    f'<b>{escape(row["code"])} is an unspecified code</b>'
                    'The note may support a more specific code. Do not infer specificity — query the provider.</div>',
                    unsafe_allow_html=True,
                )
                st.button(
                    "Send non-leading physician query", key=f"code_query_{row['key']}", type="secondary",
                    icon=":material/chat_bubble:", disabled=row["status"] == "Query sent",
                    on_click=set_code_decision, args=(record, [row["key"]], "Query sent", "Specificity query"),
                )
        else:
            status_text = {
                "performed": "Documented as performed during this encounter",
                "ordered_not_performed": "Ordered — no result or performed evidence yet",
                "planned": "Planned / recommended — not yet performed",
                "recommended": "Recommended — not yet performed",
                "discussed": "Discussed only",
            }.get(row.get("procedure_status"), "Performed status could not be verified")
            st.markdown(f'<div class="nc-label" style="margin-top:.7rem">Performed-service status</div><div class="nc-cell">{escape(status_text)}</div>', unsafe_allow_html=True)
            if row["code"] in EM_MDM_LEVELS:
                st.markdown(f'<div class="nc-label" style="margin-top:.6rem">E/M validation</div><div class="nc-cell">Level {escape(row["code"])} implies {EM_MDM_LEVELS[row["code"]]} MDM — confirm against the signed note.</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="nc-meta" style="margin-top:.6rem">Source: {"Signed" if is_signed else "Draft"} SOAP note + transcript · '
            f'Matched “{escape(row["term"])}” → {escape(row["description"])}<br>Code matcher v{CODE_MATCHER_VERSION} · ICD-10-CM / CPT catalogs</div>',
            unsafe_allow_html=True,
        )
        with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
            st.button("Reject", key=f"code_reject_{row['key']}", type="secondary", on_click=set_code_decision, args=(record, [row["key"]], "Rejected"))
            st.button("Accept", key=f"accent_code_accept_{row['key']}", on_click=set_code_decision, args=(record, [row["key"]], "Accepted"))


def show_codes():
    render_sidebar("codes")
    records = st.session_state.records
    if not records:
        page_title("CODING & HCC", "Medical Coding Workspace")
        st.info("No patient encounters are available. Create a consultation before generating codes.")
        return

    current = st.session_state.current_record
    record = next((r for r in records if current and r.get("id") == current.get("id")), records[0])
    actions = page_header("CODING & HCC", "Medical Coding Workspace", f'{record_subtitle(record)} · synthetic demo record', action_weight=4.2)
    actions.button("← Return to Clinical Review", key="codes_back_review", type="secondary", on_click=open_record, args=(record,))
    actions.button("Proceed to Billing →", key="accent_codes_billing", on_click=lambda: st.session_state.update(page="billing", billing_record_id=record.get("id")))

    with st.container(border=True):
        enc_col, badge_col = st.columns([5.5, 4.5], vertical_alignment="bottom")
        record_lookup = {f'{r.get("ehr_encounter_id") or r.get("id")} · {format_date(r.get("date"), "")} · {r.get("name", "Unknown")} · {r.get("ehr_encounter_type") or "Office visit"}': r for r in records}
        labels = list(record_lookup)
        default_label = next(label for label, value in record_lookup.items() if value.get("id") == record.get("id"))
        selected_label = enc_col.selectbox("Encounter", labels, index=labels.index(default_label), key="codes_encounter")
        if record_lookup[selected_label].get("id") != record.get("id"):
            st.session_state.current_record = record_lookup[selected_label]
            st.session_state.active_code = None
            st.rerun()
        is_signed = record.get("status") == "Approved"
        badge_col.markdown(
            '<div class="nc-badge-row" style="justify-content:flex-end;padding-bottom:.35rem">'
            + badge("Note SIGNED" if is_signed else "Note DRAFT", "green" if is_signed else "amber")
            + badge("ICD-10-CM · CPT/HCPCS", "blue")
            + badge("CMS-HCC V28 (demo map)", "violet")
            + "</div>",
            unsafe_allow_html=True,
        )

    insights = get_record_insights(record)
    st.session_state.detected_insights = insights
    if st.session_state.current_record is None or st.session_state.current_record.get("id") != record.get("id"):
        st.session_state.current_record = record
    ensure_code_results(record, insights)
    code_results = st.session_state.code_results
    code_error = st.session_state.get("code_generation_error", "")
    if code_error:
        st.warning(code_error)
    if not is_signed:
        render_banner("Coding a draft note", "Codes can be reviewed now, but final coding should follow the signed SOAP note.", tone="warn", icon="&#9888;")

    rows = build_code_rows(record, code_results)
    if rows and st.session_state.active_code not in {row["key"] for row in rows}:
        st.session_state.active_code = rows[0]["key"]
    table_col, evidence_col = st.columns([6.6, 3.4], gap="medium")
    with table_col:
        with st.container(border=True):
            card_title("Encounter Codes", "Matcher suggestions, extracted services and E/M validation", trailing=badge(f"{len(rows)} candidates", "violet"))
            if rows:
                selected_keys = _render_code_table(record, rows)
                with st.container(horizontal=True, gap="small"):
                    st.button("Accept selected", key="codes_bulk_accept", type="primary", disabled=not selected_keys, on_click=set_code_decision, args=(record, selected_keys, "Accepted"))
                    st.button("Reject selected", key="codes_bulk_reject", type="secondary", disabled=not selected_keys, on_click=set_code_decision, args=(record, selected_keys, "Rejected"))
                    if st.button("↻ Re-run matcher", key="codes_regenerate", type="tertiary", disabled=not insights):
                        st.session_state.code_record_id = None
                        st.rerun()
            else:
                st.markdown('<div class="code-empty">No diagnosis or procedure codes are available for this encounter.</div>', unsafe_allow_html=True)
    with evidence_col:
        active_row = next((row for row in rows if row["key"] == st.session_state.active_code), None)
        if active_row:
            _render_evidence_panel(record, active_row, is_signed)
        else:
            with st.container(border=True):
                card_title("Evidence & Provenance")
                st.caption("Select a code to see its supporting documentation.")

    statuses = [row["status"] for row in rows]
    hccs = sorted({row["hcc"] for row in rows if row["hcc"] and row["status"] not in {"Rejected"}})
    em_code = next((row["code"] for row in rows if row["code"] in EM_MDM_LEVELS and row["status"] != "Rejected"), None)
    with st.container(border=True):
        card_title("Coding Summary", "Encounter-level coding and risk capture")
        render_tiles([
            ("Accepted", statuses.count("Accepted")),
            ("Pending query", statuses.count("Query sent")),
            ("Rejected", statuses.count("Rejected")),
            ("HCCs captured", f'{len(hccs)} · {", ".join(hccs)}' if hccs else "0"),
            ("E/M level", em_code or "—"),
            ("MDM", EM_MDM_LEVELS.get(em_code, "—") if em_code else "—"),
        ])
        audit = record.get("coding_audit") or []
        last = audit[-1] if audit else None
        st.markdown(
            f'<div class="nc-meta" style="margin-top:.5rem">{"Last action: " + escape(last["action"]) + " " + escape(last["code"]) + " · " + escape(last["at"]) if last else "No coder actions yet"}'
            ' · Every status transition is retained in the encounter audit log.</div>',
            unsafe_allow_html=True,
        )
    with st.expander("Detected clinical context and raw matcher output"):
        if insights:
            render_grouped_insights(insights)
        icd_tab, cpt_tab, audit_tab = st.tabs(["ICD-10 matcher output", "CPT/HCPCS matcher output", "Coding audit log"])
        with icd_tab:
            render_code_results(code_results.get("icd10", []), "No ICD-10 suggestions are available.")
        with cpt_tab:
            render_code_results(code_results.get("cpt", []), "No CPT/HCPCS suggestions are available.")
        with audit_tab:
            if audit:
                st.dataframe(pd.DataFrame(audit), hide_index=True, width="stretch")
            else:
                st.caption("No coder actions recorded yet.")
    st.caption("Coding suggestions require professional validation before billing, claim submission, or addition to the legal health record.")


# ============================================================
# 9. BILLING & RCM
# ============================================================
def billing_guardrail_rows(record):
    """Classify each CPT candidate as billable/blocked using performed-status evidence."""
    cpt_results = list((record.get("code_results") or {}).get("cpt") or [])
    documentation = record_documentation(record)
    decisions = record.get("code_decisions") or {}
    note_text = str(record.get("summary") or "").lower()
    rows = []
    for candidate in cpt_results:
        if not isinstance(candidate, dict):
            continue
        code = str(candidate.get("CPT/HCPCS Code") or "").strip()
        term = str(candidate.get("Extracted Procedure") or "")
        if not code:
            continue
        status_procedure, status_documentation = _procedure_status_inputs(term, documentation)
        procedure_status = classify_procedure_status(status_procedure, status_documentation) if classify_procedure_status else "unknown"
        performed = bool(is_explicitly_performed and is_explicitly_performed(status_procedure, status_documentation))
        rejected = decisions.get(f"CPT:{code}", {}).get("status") == "Rejected"
        words = [word for word in re.findall(r"[a-z]+", term.lower()) if len(word) > 3]
        in_note = any(word in note_text for word in words)
        if rejected:
            guardrail, tone, reason = "EXCLUDED", "gray", "Rejected by coder"
        elif performed:
            guardrail, tone, reason = "BILLABLE", "green", "Performed and documented"
        elif procedure_status == "unknown":
            guardrail, tone, reason = "UNVERIFIED", "amber", "Performed status could not be verified"
        else:
            guardrail, tone, reason = "BLOCKED", "red", "Not documented as performed"
        rows.append({
            "candidate": candidate, "code": code, "service": candidate.get("Matched Procedure/Service") or term,
            "order": {"performed": "Performed", "ordered_not_performed": "Ordered", "planned": "Planned", "recommended": "Recommended", "discussed": "Discussed"}.get(procedure_status, "Unknown"),
            "evidence": best_excerpt(term, documentation, max_chars=90) or "No performed evidence found",
            "documentation": ("Signed note" if record.get("status") == "Approved" else "Draft note") if in_note else "Transcript only",
            "guardrail": guardrail, "tone": tone, "reason": reason,
        })
    return rows


def _patient_key(record):
    return record_patient_id(record) or f'local:{str(record.get("name", "")).strip().lower()}'


def _billing_status(record):
    rows = billing_guardrail_rows(record)
    if record.get("bill_generated_at"):
        return "Bill drafted", "green"
    if not rows:
        return "Not coded", "gray"
    blocked = sum(row["guardrail"] in {"BLOCKED", "UNVERIFIED"} for row in rows)
    if blocked:
        return f"{blocked} line{'s' if blocked > 1 else ''} held", "red"
    if record.get("status") != "Approved":
        return "Awaiting signature", "amber"
    return "Ready to bill", "amber"


def _render_patient_directory(records):
    patients = {}
    for record in records:
        patients.setdefault(_patient_key(record), []).append(record)
    with st.container(border=True):
        card_title("Patient Directory", f"{len(patients)} patients · local consultations linked to the EHR")
        search = st.text_input("Search", placeholder="Search name / MRN / ID", key="billing_search", label_visibility="collapsed").strip().lower()
        view = st.segmented_control("Filter", ["All", "OPD", "IPD", "Unsynced"], default="All", key="billing_filter", label_visibility="collapsed") or "All"
        shown = 0
        for key, patient_records in patients.items():
            latest = patient_records[0]
            chart = record_chart(latest)
            coverage = primary_coverage(chart) if chart else {}
            unsynced = any(not r.get("ehr_encounter_id") for r in patient_records)
            types = {"IPD" if str(r.get("ehr_encounter_type", "")).lower().startswith("inp") else "OPD" for r in patient_records}
            haystack = f'{latest.get("name", "")} {key} {latest.get("mrn", "")} {" ".join(r.get("id", "") for r in patient_records)}'.lower()
            if search and search not in haystack:
                continue
            if (view in {"OPD", "IPD"} and view not in types) or (view == "Unsynced" and not unsynced):
                continue
            shown += 1
            is_selected = st.session_state.billing_patient_key == key
            with st.container(key=f"dir_{'on' if is_selected else 'off'}_{re.sub(r'[^A-Za-z0-9]', '_', key)}", border=True):
                info_col, action_col = st.columns([4, 1.4], vertical_alignment="center")
                meta = " · ".join(part for part in (record_patient_id(latest) or "Local only", coverage.get("Payer_Name", "")) if part)
                info_col.markdown(
                    f'<div class="nc-patient"><div class="nc-initials">{escape(initials(latest.get("name")))}</div>'
                    f'<div><b>{escape(latest.get("name", ""))}</b><small>{escape(meta)}</small></div></div>'
                    + (f'<div style="margin-top:.3rem">{badge("Unsynced", "amber")}</div>' if unsynced else ""),
                    unsafe_allow_html=True,
                )
                action_col.button(
                    "✓" if is_selected else "Open", key=f"billing_open_{key}", type="tertiary",
                    on_click=lambda k=key, r=latest: st.session_state.update(billing_patient_key=k, billing_record_id=r.get("id")),
                )
        if not shown:
            st.caption("No patients match this filter.")
    return patients


def _render_patient_banner(record, chart):
    patient = (chart or {}).get("patient") or {}
    coverage = primary_coverage(chart) if chart else {}
    allergies = ", ".join(str(row.get("Substance")) for row in active_drug_allergies(chart)) if chart else ""
    synced = bool(record.get("ehr_encounter_id"))
    age = record.get("age") or age_from_dob(patient.get("DOB"))
    badges = []
    if coverage:
        badges.append(badge(f'{coverage.get("Payer_Name", "Payer")} · {coverage.get("Plan_Type", "")}'.strip(" ·"), "blue"))
        badges.append(badge(f'Eligibility {str(coverage.get("Status", "unknown")).upper()}', "green" if str(coverage.get("Status", "")).lower() == "active" else "amber"))
    else:
        badges.append(badge("No coverage on file", "gray"))
    if allergies:
        badges.append(badge(f"Allergy: {allergies}", "red"))
    badges.append(badge("EHR synced" if synced else "Unsynced · local only", "green" if synced else "amber"))
    st.markdown(
        '<div class="nc-banner-card">'
        f'<div class="nc-patient"><div class="nc-initials lg">{escape(initials(record.get("name")))}</div>'
        f'<div><b style="font-size:.95rem">{escape(record.get("name", ""))}</b><small>{escape(record_patient_id(record) or record.get("id", ""))} · {escape(str(age))}y · {escape(record.get("gender", ""))}</small></div></div>'
        f'<div class="nc-badge-row">{"".join(badges)}</div></div>',
        unsafe_allow_html=True,
    )


def _render_encounter_list(patient_records, chart, selected_id):
    ehr_encounters = latest_first(chart_rows(chart, "Encounters"), "Date") if chart else []
    local_encounter_ids = {r.get("ehr_encounter_id") for r in patient_records}
    history = [row for row in ehr_encounters if row.get("Encounter_ID") not in local_encounter_ids][:4]
    claims = {row.get("Encounter_ID"): row for row in chart_rows(chart, "Billing_Claims")} if chart else {}
    with st.container(border=True):
        card_title(f"Encounters ({len(patient_records) + len(history)})", "Select an encounter to review its charges")
        widths = [1.5, 1.4, 1.2, 1.6, 1.2, 1.4, 1.3]
        header = st.columns(widths)
        for col, label in zip(header, ("Encounter", "Type", "Date", "Provider", "Note", "Billing status", "Action")):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        for record in patient_records:
            is_selected = record.get("id") == selected_id
            billing_text, billing_tone = _billing_status(record)
            with st.container(key=f"encrow_{'sel' if is_selected else 'off'}_{record.get('id')}"):
                cols = st.columns(widths, vertical_alignment="center")
                cols[0].markdown(f'<div class="nc-cell"><b>{escape(record.get("ehr_encounter_id") or record.get("id"))}</b></div>', unsafe_allow_html=True)
                cols[1].markdown(f'<div class="nc-cell">{escape(record.get("ehr_encounter_type") or "Office visit")}</div>', unsafe_allow_html=True)
                cols[2].markdown(f'<div class="nc-cell muted">{escape(format_date(record.get("date")))}</div>', unsafe_allow_html=True)
                cols[3].markdown(f'<div class="nc-cell muted">{escape(record.get("doctor", ""))}</div>', unsafe_allow_html=True)
                cols[4].markdown(badge("Signed", "green") if record.get("status") == "Approved" else badge("SOAP draft", "amber"), unsafe_allow_html=True)
                cols[5].markdown(badge(billing_text, billing_tone), unsafe_allow_html=True)
                cols[6].button(
                    "Reviewing" if is_selected else "Review charges", key=f"billing_review_{record.get('id')}",
                    type="secondary", disabled=is_selected,
                    on_click=lambda r=record: st.session_state.update(billing_record_id=r.get("id")),
                )
        for row in history:
            claim = claims.get(row.get("Encounter_ID"), {})
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].markdown(f'<div class="nc-cell"><b>{escape(str(row.get("Encounter_ID", "")))}</b></div>', unsafe_allow_html=True)
            cols[1].markdown(f'<div class="nc-cell">{escape(str(row.get("Type", "")))}</div>', unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell muted">{escape(format_date(row.get("Date")))}</div>', unsafe_allow_html=True)
            cols[3].markdown(f'<div class="nc-cell muted">{escape(str(row.get("Provider", "")))}</div>', unsafe_allow_html=True)
            cols[4].markdown(badge(row.get("Status") or "—", "green" if str(row.get("Status")).lower() == "completed" else "gray"), unsafe_allow_html=True)
            claim_status = claim.get("Claim_Status")
            cols[5].markdown(badge(f"Claim {claim_status}" if claim_status else "No claim", "green" if str(claim_status).lower() == "paid" else "gray"), unsafe_allow_html=True)
            cols[6].markdown('<span class="nc-meta">EHR history</span>', unsafe_allow_html=True)


def show_billing():
    render_sidebar("billing")
    records = st.session_state.records
    if not records:
        page_title("BILLING & RCM", "Charge Review & Billing Guardrail", "Only services performed AND documented reach the bill")
        st.info("No consultations yet. Billing starts from a coded consultation.")
        return
    record = next((r for r in records if r.get("id") == st.session_state.billing_record_id), None)
    if record is None:
        current = st.session_state.current_record
        record = next((r for r in records if current and r.get("id") == current.get("id")), records[0])
        st.session_state.billing_record_id = record.get("id")
    if st.session_state.billing_patient_key != _patient_key(record):
        st.session_state.billing_patient_key = _patient_key(record)

    guardrail = billing_guardrail_rows(record)
    billable = [row for row in guardrail if row["guardrail"] == "BILLABLE"]
    held = [row for row in guardrail if row["guardrail"] in {"BLOCKED", "UNVERIFIED"}]
    encounter_type = "IPD" if str(record.get("ehr_encounter_type", "")).lower().startswith("inp") else "OPD"
    attest_key = f"billing_attest_{record.get('id')}"
    bill_rows, bill_total, unpriced = build_estimated_hospital_bill([row["candidate"] for row in billable], encounter_type) if billable else ([], 0.0, [])

    actions = page_header("BILLING & RCM", "Charge Review & Billing Guardrail", "Only services performed AND documented reach the bill", action_weight=4.2)
    export = {
        "encounter_id": record.get("ehr_encounter_id") or record.get("id"), "patient": record.get("name"),
        "encounter_type": encounter_type, "lines": bill_rows, "gross_total": bill_total,
        "held_lines": [{"code": row["code"], "reason": row["reason"]} for row in held], "notice": BILLING_NOTICE,
    }
    actions.download_button(
        "Download draft (JSON)", data=json.dumps(export, indent=2), file_name=f'bill_{export["encounter_id"]}.json',
        mime="application/json", icon=":material/download:", disabled=not bill_rows, key="billing_download",
    )

    def send_to_rcm():
        selected_codes = [row["code"] for row in billable]
        estimate = {
            "record_id": record.get("id"), "encounter_type": encounter_type,
            "code_results_signature": None, "selected_cpt_codes": selected_codes,
            "rows": bill_rows, "total": bill_total, "unpriced_codes": unpriced,
        }
        st.session_state.estimated_hospital_bill = estimate
        code_results = record.get("code_results") or {"icd10": [], "cpt": []}
        register_generated_bill(build_ambient_bill(record, estimate, code_results), source=encounter_type)
        record["bill_generated_at"] = datetime.now().strftime("%d %b %Y %H:%M")
        save_record(record)
        go_to("claim_generation")

    actions.button(
        "Send to RCM (claim draft) ➤", key="accent_billing_send", on_click=send_to_rcm,
        disabled=not bill_rows or not st.session_state.get(attest_key, False),
    )

    directory_col, main_col = st.columns([2.5, 7.5], gap="medium")
    with directory_col:
        patients = _render_patient_directory(records)
    patient_records = patients.get(_patient_key(record), [record])
    if record not in patient_records:
        record = patient_records[0]
    with main_col:
        chart = record_chart(record)
        _render_patient_banner(record, chart)
        _render_encounter_list(patient_records, chart, record.get("id"))
        if not guardrail:
            render_banner("This encounter has not been coded yet", "Run the Coding & HCC workspace to extract CPT/HCPCS candidates.", tone="warn", icon="&#9888;")
            st.button("Open Coding & HCC →", key="billing_open_coding", type="primary", on_click=open_record, args=(record, "codes"))
            return
        if held:
            codes = ", ".join(row["code"] for row in held)
            render_banner(
                f"{len(held)} line{'s' if len(held) > 1 else ''} held — {codes} excluded until performed evidence is documented",
                "Held lines re-enter the bill once the note documents the service as performed.",
                tone="danger", icon="&#9888;", trailing=badge("BLOCKED", "red"),
            )
        with st.container(border=True):
            card_title(
                "Performed-Service Guardrail",
                f'{record.get("ehr_encounter_id") or record.get("id")} · order, evidence and documentation reconciliation',
                trailing=badge(f"{len(billable)} billable · {len(held)} held", "amber" if held else "green"),
            )
            render_html_table(
                ["CPT", "Service", "Order", "Performed evidence", "Documentation", "Guardrail"],
                [
                    [f'<code>{escape(row["code"])}</code>', escape(row["service"]), escape(row["order"]), escape(row["evidence"]),
                     escape(row["documentation"]), badge(row["guardrail"], row["tone"])]
                    for row in guardrail
                ],
                ["blocked" if row["guardrail"] == "BLOCKED" else "" for row in guardrail],
            )
            st.checkbox("I reviewed these CPT/HCPCS codes and confirm the services were performed and documented for this encounter.", key=attest_key)
        with st.container(border=True):
            coverage = primary_coverage(chart) if chart else {}
            bill_id = f'BILL-{re.sub(r"[^A-Za-z0-9]", "", str(record.get("ehr_encounter_id") or record.get("id")))[-6:]}'
            card_title(
                f"Bill {bill_id}",
                " · ".join(part for part in ("Professional claim", coverage.get("Payer_Name", ""), coverage.get("Plan_Type", ""), "Outpatient" if encounter_type == "OPD" else "Inpatient") if part),
                trailing=badge("DRAFTED" if record.get("bill_generated_at") else "DRAFT", "green" if record.get("bill_generated_at") else "amber"),
            )
            dx_codes = [str(item.get("ICD-10 Code")) for item in (record.get("code_results") or {}).get("icd10", []) if isinstance(item, dict) and item.get("ICD-10 Code")]
            render_tiles([
                ("Gross", f"${bill_total:,.2f}"),
                ("Billable lines", len(bill_rows)),
                ("Held lines", len(held)),
                ("Unpriced", len(unpriced)),
                ("Diagnosis codes", ", ".join(dx_codes[:3]) or "—"),
            ])
            if bill_rows:
                st.dataframe(pd.DataFrame(bill_rows), hide_index=True, width="stretch")
            if unpriced:
                st.warning("Unpriced codes: " + ", ".join(unpriced))
            st.caption(BILLING_NOTICE)


# ============================================================
# 10. MEDICATION & RTBC, PRIOR AUTH, KNOWLEDGE BASE
# ============================================================
def _select_ehr_patient(key):
    """Pick an EHR patient, defaulting to the current consultation's patient."""
    patients = fetch_ehr_patients()
    if patients is None:
        render_banner("EHR backend unavailable", "Start the FastAPI backend with: python -m app", tone="warn", icon="&#9888;")
        return None, {}
    options = {f'{p.get("display_name") or p.get("Legal_Name")} · {p.get("patient_id")}': str(p.get("patient_id")) for p in patients}
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
    render_sidebar("medications")
    page_title("MEDICATION & RTBC", "Medication Review & Real-Time Benefit Check", "Active medications, allergy cross-checks and coverage context")
    patient_id, chart = _select_ehr_patient("meds_patient")
    if not patient_id or not chart:
        return
    record = st.session_state.current_record if record_patient_id(st.session_state.current_record) == patient_id else None
    plan_text = f'{(record or {}).get("recommendations", "")}\n{(record or {}).get("summary", "")}'
    left, right = st.columns([6.4, 3.6], gap="medium")
    with left:
        with st.container(border=True):
            medications = chart_rows(chart, "Medications")
            card_title("Medication List", f"{sum(is_active_row(row) for row in medications)} active · from EHR MedicationRequest")
            if medications:
                render_html_table(
                    ["Medication", "Dose", "Route", "Frequency", "Indication", "Prescriber", "Status"],
                    [[escape(str(row.get("Medication", ""))), escape(str(row.get("Dose", ""))), escape(str(row.get("Route", ""))),
                      escape(str(row.get("Frequency", ""))), escape(str(row.get("Indication_ICD_10", ""))), escape(str(row.get("Prescriber", ""))),
                      badge(row.get("Status") or "—", "green" if is_active_row(row) else "gray")] for row in medications],
                )
            else:
                st.caption("No medications recorded.")
        with st.container(border=True):
            allergies = chart_rows(chart, "Allergies")
            card_title("Allergies & Intolerances", f"{len(allergies)} recorded")
            if allergies:
                render_html_table(
                    ["Substance", "Category", "Reaction", "Severity", "Status"],
                    [[escape(str(row.get("Substance", ""))), escape(str(row.get("Category", ""))), escape(str(row.get("Reaction", ""))),
                      escape(str(row.get("Severity", ""))), badge(row.get("Clinical_Status") or "—", "red" if is_active_row(row) else "gray")] for row in allergies],
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
                render_tiles([
                    ("Plan", coverage.get("Plan_Type") or "—"),
                    ("Member ID", coverage.get("Member_ID") or "—"),
                    ("Copay", f'${coverage.get("Copay_USD")}' if coverage.get("Copay_USD") is not None else "—"),
                    ("Deductible", f'${coverage.get("Annual_Deductible_USD"):,}' if isinstance(coverage.get("Annual_Deductible_USD"), (int, float)) else "—"),
                ])
            render_banner("Real-time benefit check feed not connected", "Formulary tier, prior-auth flags and alternatives require a payer RTBC integration; plan copay is shown from the EHR coverage record.", icon="&#9432;")


PRIOR_AUTH_DEMO_RULES = (
    (70450, 70498, "CT head / neck"), (70540, 70559, "MRI head / neck"), (71250, 71275, "CT chest"),
    (72125, 72159, "CT / MRI spine"), (73200, 73725, "CT / MRI extremity"), (74150, 74183, "CT / MRI abdomen"),
    (78459, 78816, "Nuclear medicine / PET"), (95810, 95811, "Polysomnography"),
)


def prior_auth_rule(code):
    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) != 5:
        return None
    value = int(digits)
    return next((label for low, high, label in PRIOR_AUTH_DEMO_RULES if low <= value <= high), None)


def show_prior_auth():
    render_sidebar("prior_auth")
    page_title("PRIOR AUTHORIZATION", "Prior Authorization Queue", "Orders screened against a demo prior-auth rule set (advanced imaging, nuclear medicine, sleep studies)")
    patient_id, chart = _select_ehr_patient("pa_patient")
    if not patient_id or not chart:
        return
    coverage = primary_coverage(chart)
    orders = [
        {"source": "EHR order", "date": row.get("Order_Date"), "code": row.get("CPT_HCPCS"), "description": row.get("Order_Description"), "status": row.get("Status")}
        for row in chart_rows(chart, "Orders_Procedures")
    ]
    record = st.session_state.current_record if record_patient_id(st.session_state.current_record) == patient_id else None
    for item in ((record or {}).get("code_results") or {}).get("cpt", []):
        orders.append({"source": "Current encounter", "date": (record or {}).get("date"), "code": item.get("CPT/HCPCS Code"), "description": item.get("Matched Procedure/Service"), "status": "Coded"})
    rows, flagged = [], 0
    for order in orders:
        rule = prior_auth_rule(order["code"])
        flagged += bool(rule)
        rows.append([
            escape(order["source"]), escape(format_date(order["date"])), f'<code>{escape(str(order["code"] or ""))}</code>',
            escape(str(order["description"] or "")), escape(str(order["status"] or "")),
            badge(f"PA likely · {rule}", "amber") if rule else badge("No PA expected", "green"),
        ])
    render_tiles([
        ("Payer", coverage.get("Payer_Name") or "—"),
        ("Plan", coverage.get("Plan_Type") or "—"),
        ("Orders screened", len(orders)),
        ("PA likely", flagged),
    ])
    with st.container(border=True):
        card_title("Orders & Services", "Rule set is a demo screen, not a payer policy — confirm with the payer before scheduling")
        if rows:
            render_html_table(["Source", "Date", "CPT", "Description", "Status", "Prior auth"], rows)
        else:
            st.caption("No orders on file for this patient.")


def show_knowledge_base():
    render_sidebar("knowledge_base")
    page_title("KNOWLEDGE BASE", "Clinical Guideline Search", "Search the local guideline knowledge base used by CDS (TF-IDF + ChromaDB)")
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
        st.markdown(f'<div class="nc-meta">{len(passages)} passage(s) for “{escape(result.get("query", ""))}”</div>', unsafe_allow_html=True)
        if not passages:
            st.info(result.get("context") or "No results.")
        for passage in passages:
            with st.container(border=True):
                st.markdown(f'<div class="nc-rec-source" style="margin:0 0 .3rem">§ {escape(passage["source"])}</div><div class="nc-rec-body" style="color:var(--text)">{escape(passage["text"])}</div>', unsafe_allow_html=True)


# ============================================================
# 11. ROUTER
# ============================================================
PAGES = {
    "dashboard": show_dashboard,
    "new_consult": show_new_consultation,
    "review": show_review_note,
    "medications": show_medications,
    "prior_auth": show_prior_auth,
    "codes": show_codes,
    "billing": show_billing,
    "knowledge_base": show_knowledge_base,
    "ehr_data": show_ehr_demo_data,
    "ipd_billing": lambda: show_ipd_billing(render_sidebar, render_header),
    "claim_generation": lambda: show_claim_generation_page(render_sidebar, render_header),
    "admin_dashboard": lambda: show_admin_dashboard(
        st.session_state.records,
        load_ehr_demo_workbook,
        EHR_DATA_FILE,
        open_record,
        render_sidebar,
        render_header,
    ),
    "super_admin": lambda: show_super_admin(render_sidebar, render_header),
}

page_renderer = PAGES.get(st.session_state.page)
if page_renderer is None:
    st.session_state.page = "dashboard"
    st.rerun()
else:
    page_renderer()
