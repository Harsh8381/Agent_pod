"""Encounter coding and billing logic (no page rendering).

Turns transcripts into clinical insights, matches ICD-10/CPT codes (backend first,
local matcher as fallback), and decides which CPT lines are billable and how they price.
"""

import math
import re
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from frontend.api_client import HealthcareApiClient
from frontend.chargemaster import build_cpt_bill_rows
from frontend.settings import PROJECT_ROOT

try:
    from cpt_coder import (
        classify_medical_item,
        classify_procedure_status,
        extract_performed_procedures,
        get_cpt_candidate_sets,
        get_cpt_codes,
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


MASTER_INSIGHTS = [
    "None",
    "Diabetes (Type 2)",
    "Hypertension",
    "Hyperlipidemia",
    "Asthma",
    "Osteoarthritis",
    "Obesity",
    "Smoking History",
    "Alcohol Use",
    "Anxiety",
    "Depression",
    "Fever",
    "Chronic Cough",
    "Fatigue",
    "Headache",
    "Chest Pain",
    "Nausea",
    "Color Blindness",
    "Complete Blood Count",
    "A1C Test",
    "Chest X-ray",
    "CT Scan",
    "MRI",
    "Ultrasound",
    "Blood Glucose Test",
]


def get_detected_insights(transcript_text):
    text = (transcript_text or "").lower()
    keyword_map = {
        "diabetes": "Diabetes (Type 2)",
        "hypertension": "Hypertension",
        "high bp": "Hypertension",
        "cholesterol": "Hyperlipidemia",
        "asthma": "Asthma",
        "arthritis": "Osteoarthritis",
        "obesity": "Obesity",
        "smok": "Smoking History",
        "alcohol": "Alcohol Use",
        "anxiety": "Anxiety",
        "depression": "Depression",
        "fever": "Fever",
        "cough": "Chronic Cough",
        "fatigue": "Fatigue",
        "headache": "Headache",
        "chest pain": "Chest Pain",
        "nausea": "Nausea",
        "color blind": "Color Blindness",
        "complete blood count": "Complete Blood Count",
        "cbc": "Complete Blood Count",
        "a1c": "A1C Test",
        "hba1c": "A1C Test",
        "chest x-ray": "Chest X-ray",
        "chest x ray": "Chest X-ray",
        "chest xray": "Chest X-ray",
        "xray": "Chest X-ray",
        "x-ray": "Chest X-ray",
        "x ray": "Chest X-ray",
        "ct scan": "CT Scan",
        "mri": "MRI",
        "ultrasound": "Ultrasound",
        "blood glucose": "Blood Glucose Test",
        "physical therapy": "Physical Therapy",
        "physiotherapy": "Physical Therapy",
        "injection": "Injection",
        "injections": "Injection",
    }
    return list(dict.fromkeys(value for keyword, value in keyword_map.items() if keyword in text))


def get_record_insights(record):
    transcript_insights = get_detected_insights(record.get("transcript", ""))
    manual_insights = record.get("manual_insights", [])
    return list(
        dict.fromkeys(
            transcript_insights + [item for item in manual_insights if item != "None"] + ["Established Patient Office Visit"]
        )
    )


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

    candidates = list(dict.fromkeys(str(item).strip() for item in insights if item and item != "None"))
    if not candidates:
        st.session_state.code_generation_error = "No codable clinical insights were detected."
        return {"icd10": [], "cpt": []}

    icd_items = []
    cpt_items = []
    classification_errors = []

    procedure_terms = (
        "test",
        "x-ray",
        "x ray",
        "scan",
        "mri",
        "ultrasound",
        "blood",
        "cbc",
        "a1c",
        "hba1c",
        "procedure",
        "office visit",
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
            cpt_items = [item for item in cpt_items if "chest x-ray" not in item.lower() and "chest x ray" not in item.lower()]
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
            performed_cpt_items = [item for item in cpt_items if is_documented_cpt_candidate(item, documentation)]
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
        st.session_state.code_generation_error = "Some items could not be classified automatically: " + "; ".join(
            classification_errors
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
            "test",
            "x-ray",
            "x ray",
            "scan",
            "mri",
            "ultrasound",
            "blood",
            "cbc",
            "a1c",
            "hba1c",
            "procedure",
            "office visit",
            "therapy",
            "injection",
            "surgery",
            "biopsy",
            "endoscopy",
            "evaluation",
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
            procedures = [item for item in procedures if "chest x-ray" not in item.lower() and "chest x ray" not in item.lower()]
            procedures.append("Chest x ray 2 views")
        return HealthcareApiClient().match_codes(conditions, procedures, documentation)
    except (RuntimeError, requests.exceptions.HTTPError):
        return get_code_suggestions(insights, documentation)


def get_code_results_signature(insights, transcript):
    """Key cached suggestions on both the detected insights and the transcript text."""
    normalized_insights = tuple(str(item).strip() for item in (insights or []) if item and item != "None")
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
        status = classify_procedure_status(status_procedure, status_documentation) if classify_procedure_status else "unknown"
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
    billing_eligible_cpt_results, excluded_count, unverifiable_count = filter_billing_eligible_cpt_results(
        cpt_results, documentation
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
def load_chargemaster_department_map(file_path, sheet_name):
    path = Path(file_path)
    if not path.is_file():
        return {}
    try:
        frame = pd.read_excel(path, sheet_name=sheet_name, header=3, dtype=str, keep_default_na=False, engine="openpyxl")
    except (OSError, ValueError, ImportError):
        return {}
    code_column = next(
        (column for column in frame.columns if re.sub(r"[^a-z0-9]", "", str(column).lower()) in {"cpthcpcs", "cpthcpcscode"}),
        None,
    )
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
        table_rows.append(
            {
                "CPT/HCPCS Code": code,
                "Service Description": row.get("Procedure Description", "Not specified"),
                "Department": departments.get(normalize_billing_code(code), "Not specified"),
                "Quantity": quantity,
                "Unit Charge": unit_display,
                "Gross Charge": gross_display,
                "Pricing Status": pricing_status,
            }
        )
    return table_rows, round(total, 2), list(dict.fromkeys(unpriced_codes))
