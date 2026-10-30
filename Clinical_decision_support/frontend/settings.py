"""Paths and constants shared by the frontend."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DB_FILE = PROJECT_ROOT / "clinical_records.json"

EHR_DATA_FILE = PROJECT_ROOT / "Epic_Inspired_USA_50_Patient_Demo.xlsx"

EHR_JSON_FILE = PROJECT_ROOT / "Epic_Inspired_USA_50_Patient_Demo.json"

BILLING_NOTICE = (
    "This draft contains gross chargemaster-based demo charges only. It is not a finalized claim, "
    "payer-allowed amount, or patient responsibility amount. Professional coding and billing validation is required."
)

ANALYSIS_VERSION = "2"

CODE_MATCHER_VERSION = "4"

ACCESS_STORE_PATH = PROJECT_ROOT / "data" / "access_control.json"

SESSION_IDLE_MINUTES = 30
