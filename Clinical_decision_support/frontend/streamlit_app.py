"""NuuCare Clinical Decision Platform — Streamlit entry point.

Run from the Clinical_decision_support folder (so .streamlit/config.toml applies):
    streamlit run frontend/streamlit_app.py

Layout of the frontend package:
    settings.py            paths and constants
    encounter_pipeline.py  code matching and billing-line logic (no UI)
    clinical_text.py       transcript / SOAP / CDS text helpers (pure)
    ehr_chart.py           chart context, medication safety, care gaps (pure)
    ui/                    theme, session state, backend data, components, app shell
    views/                 one module per page, plus the router
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="NuuCare | Clinical Decision Platform",
    page_icon=":material/health_and_safety:",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items=None,
)

from frontend.ui.state import init_session  # noqa: E402
from frontend.ui.theme import inject_theme  # noqa: E402
from frontend.views.router import run_app  # noqa: E402

inject_theme()
init_session()
run_app()
