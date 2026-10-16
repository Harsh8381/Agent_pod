from __future__ import annotations

import json
import math
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from html import escape

import pandas as pd
import streamlit as st

from frontend.chargemaster import assign_revenue_codes
from frontend.claim_generator import determine_claim_type, determine_encounter_type
from frontend.ocr_service import merge_code_results
from frontend.ui.components import badge, card_title, page_title, render_banner, render_html_table, render_tiles
from frontend.ui.shell import render_page
from frontend.ui.state import can_open, go_to

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _amount(value: Any) -> Decimal | None:
    try:
        amount = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() else None


def _codes(value: Any) -> list[str]:
    return [
        code.strip()
        for code in re.split(r"[;,\n]+", _text(value))
        if code.strip()
    ]


def _diagnosis_codes(bill: dict[str, Any]) -> list[str]:
    patient = bill.get("patient", {})
    diagnoses = []
    if isinstance(patient, dict):
        diagnoses.extend(_codes(patient.get("Primary ICD-10-CM")))
        diagnoses.extend(_codes(patient.get("Secondary ICD-10-CM")))
    for line in bill.get("lines", []):
        if isinstance(line, dict):
            diagnoses.extend(_codes(line.get("ICD-10-CM Pointer")))
    return list(dict.fromkeys(diagnoses))


def _document_requirement(code: str, line: dict[str, Any]) -> tuple[str, str]:
    service_text = " ".join((
        _text(line.get("Service Description")),
        _text(line.get("Department")),
        _text(line.get("Service Category")),
    )).casefold()
    normalized_code = re.sub(r"\D", "", code)
    code_number = int(normalized_code) if normalized_code else 0

    if re.search(r"\bct\b", service_text) or "computed tomography" in service_text or code_number in range(70450, 70500) or code_number in range(71250, 71300) or code_number in range(72125, 72135) or code_number in range(74150, 74180):
        return "CT Scan", "CT Report"
    if "mri" in service_text or "magnetic resonance" in service_text or code_number in range(70540, 70560) or code_number in range(71550, 71556) or code_number in range(72141, 72160) or code_number in range(72195, 72198) or code_number in range(73218, 73224) or code_number in range(73718, 73726) or code_number in range(74181, 74184):
        return "MRI", "MRI Report"
    if any(term in service_text for term in ("x-ray", "x ray", "xray", "radiograph")) or code_number in range(70010, 70400) or code_number in range(71010, 71050):
        return "X-Ray", "X-Ray Report"
    if any(term in service_text for term in ("ecg", "ekg", "electrocardiogram")) or code_number in range(93000, 93043):
        return "ECG", "ECG Report"
    if any(term in service_text for term in ("lab", "laboratory", "blood test", "specimen")) or code_number in range(80000, 90000):
        return "Laboratory", "Lab Result"
    if (
        normalized_code.startswith(("992", "993", "994"))
        or any(term in service_text for term in ("office visit", "clinic visit", "evaluation and management"))
    ):
        return "Office Visit", "SOAP Note"
    return "Procedure", "Procedure Note"


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            f"{key} {_flatten_text(item)}"
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(item) for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return _text(value)


def _document_kind(value: Any) -> str | None:
    text = re.sub(r"[^a-z0-9]+", " ", _text(value).casefold()).strip()
    if not text:
        return None
    if "soap" in text or "clinical note" in text or "provider note" in text or "office note" in text:
        return "SOAP Note"
    if re.search(r"\bct\b", text) or "computed tomography" in text:
        return "CT Report"
    if any(term in text for term in ("mri", "magnetic resonance")):
        return "MRI Report"
    if any(term in text for term in ("x-ray", "x ray", "xray", "radiograph")):
        return "X-Ray Report"
    if any(term in text for term in ("ecg", "ekg", "electrocardiogram")):
        return "ECG Report"
    if re.search(r"\blab(?:oratory)? (?:results?|reports?)\b", text):
        return "Lab Result"
    if "procedure note" in text or "operative note" in text:
        return "Procedure Note"
    return None


def _supporting_document_sources(
    record: dict[str, Any],
    session_state: Any,
    encounter_ids: list[str],
    project_root: Path,
) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    document_key_terms = (
        "document", "report", "result", "attachment", "upload", "lab",
        "imaging", "radiology", "ecg", "procedure_note", "clinical_note",
    )
    excluded_key_terms = (
        "transcript", "recommendation", "guideline", "highlight", "order",
    )

    def collect_fields(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key).casefold()
                if any(term in key_text for term in excluded_key_terms):
                    continue
                if any(term in key_text for term in document_key_terms):
                    text = _flatten_text(item)
                    if text:
                        sources.append((str(key), f"{key} {text}"))
                else:
                    collect_fields(item, key_text)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect_fields(item, parent_key)

    collect_fields(record)

    soap_text = next(
        (
            _text(record.get(field))
            for field in ("approved_soap_note", "soap_note", "summary")
            if _text(record.get(field))
        ),
        "",
    )
    if soap_text:
        sources.append(("Provider SOAP Note", f"SOAP Note {soap_text}"))

    storage_dir = project_root / "data" / "approved_soap_notes"
    for encounter_id in dict.fromkeys(value for value in encounter_ids if value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", encounter_id):
            continue
        path = storage_dir / f"{encounter_id}_soap.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("encounter_id") == encounter_id
            and payload.get("status") == "approved"
            and _text(payload.get("soap_note"))
        ):
            sources.append(("Approved SOAP Note", f"SOAP Note {payload['soap_note']}"))

    if session_state is not None:
        try:
            state_items = session_state.items()
        except AttributeError:
            state_items = ()
        for key, value in state_items:
            key_text = str(key).casefold()
            if (
                not any(
                    term in key_text
                    for term in (
                        "upload", "document", "report", "attachment", "result",
                        "lab", "imaging", "radiology", "ecg", "procedure_note",
                    )
                )
                or any(term in key_text for term in excluded_key_terms)
            ):
                continue
            if isinstance(value, (list, tuple)):
                uploaded_items = value
            else:
                uploaded_items = (value,)
            for uploaded in uploaded_items:
                filename = _text(getattr(uploaded, "name", ""))
                content = ""
                getvalue = getattr(uploaded, "getvalue", None)
                if callable(getvalue):
                    content = _flatten_text(getvalue())
                source_text = " ".join(part for part in (key_text, filename, content) if part)
                if source_text:
                    sources.append((filename or str(key), source_text))

    return sources


def validate_supporting_documents(
    bill: dict[str, Any],
    record: dict[str, Any] | None = None,
    session_state: Any = None,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    patient = bill.get("patient", {})
    patient = patient if isinstance(patient, dict) else {}
    encounter_ids = [
        _text(bill.get("encounter_id")),
        _text(record.get("ehr_encounter_id")),
        _text(record.get("id")),
    ]
    sources = _supporting_document_sources(record, session_state, encounter_ids, project_root)
    results = []
    for line in bill.get("lines", []):
        if not isinstance(line, dict):
            continue
        codes = _codes(
            line.get("CPT / HCPCS")
            or line.get("CPT/HCPCS Code")
            or line.get("CPT_HCPCS")
        )
        for code in codes:
            procedure, required_document = _document_requirement(code, line)
            present = any(
                required_document.casefold() in source_text.casefold()
                or _document_kind(source_text) == required_document
                for _, source_text in sources
            )
            results.append({
                "CPT Code": code,
                "Procedure": procedure,
                "Required Document": required_document,
                "Status": "Present" if present else "Missing",
            })

    complete = all(row["Status"] == "Present" for row in results) and bool(results)
    return {
        "Overall Status": "Complete" if complete else "Incomplete",
        "Results": results,
    }


def _find_encounter_record(
    bill: dict[str, Any],
    records: Any,
) -> dict[str, Any]:
    if not isinstance(records, list):
        return {}
    patient = bill.get("patient", {})
    patient_id = _text(patient.get("Patient ID")) if isinstance(patient, dict) else ""
    encounter_id = _text(bill.get("encounter_id"))
    patient_matches = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if encounter_id and encounter_id in {
            _text(record.get("id")),
            _text(record.get("ehr_encounter_id")),
        }:
            return record
        if patient_id and patient_id in {
            _text(record.get("id")),
            _text(record.get("ehr_patient_id")),
        }:
            patient_matches.append(record)
    return patient_matches[0] if len(patient_matches) == 1 else {}


def _load_approved_soap_note(
    bill: dict[str, Any],
    record: dict[str, Any],
    project_root: Path = PROJECT_ROOT,
) -> str:
    encounter_ids = [
        _text(bill.get("encounter_id")),
        _text(record.get("ehr_encounter_id")),
        _text(record.get("id")),
    ]
    storage_dir = project_root / "data" / "approved_soap_notes"
    for encounter_id in dict.fromkeys(value for value in encounter_ids if value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", encounter_id):
            continue
        path = storage_dir / f"{encounter_id}_soap.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("encounter_id") == encounter_id
            and payload.get("status") == "approved"
            and _text(payload.get("soap_note"))
        ):
            return _text(payload["soap_note"])
    return ""


def _bill_issues(bill: dict[str, Any]) -> list[str]:
    lines = bill.get("lines")
    if not isinstance(lines, list) or not lines:
        return ["The bill has no charge lines."]
    total = _amount(bill.get("gross"))
    amounts = [
        _amount(line.get("Line Charge ($)"))
        for line in lines
        if isinstance(line, dict)
    ]
    if len(amounts) != len(lines) or any(amount is None for amount in amounts):
        return ["Every bill line must have a valid charge."]
    if total is None:
        return ["The bill total is not a valid amount."]
    if sum(amounts, Decimal("0.00")) != total:
        return ["Bill total must equal the sum of its charge lines."]
    return []


def build_ambient_bill(record: dict[str, Any], estimate: dict[str, Any], code_results: dict[str, Any]) -> dict[str, Any]:
    try:
        service_date = datetime.strptime(_text(record.get("date")), "%d/%m/%Y").date().isoformat()
    except ValueError:
        service_date = _text(record.get("date"))

    diagnosis_codes = list(dict.fromkeys(
        _text(item.get("ICD-10 Code"))
        for item in code_results.get("icd10", [])
        if isinstance(item, dict) and _text(item.get("ICD-10 Code"))
    ))
    lines = []
    for row in estimate.get("rows", []):
        if not isinstance(row, dict):
            continue
        gross_charge = _amount(row.get("Gross Charge"))
        lines.append({
            "Service Date": service_date,
            "CPT / HCPCS": _text(row.get("CPT/HCPCS Code")),
            "Revenue Code": _text(row.get("Revenue Code")),
            "Service Category": _text(row.get("Service Category")),
            "Service Description": _text(row.get("Service Description")),
            "Department": _text(row.get("Department")),
            "Quantity": row.get("Quantity"),
            "Unit Charge ($)": _amount(row.get("Unit Charge")),
            "Line Charge ($)": gross_charge,
        })

    return {
        "encounter_id": _text(record.get("ehr_encounter_id") or record.get("id")),
        "encounter_type": _text(estimate.get("encounter_type") or record.get("ehr_encounter_type")),
        "patient": {
            "Patient ID": _text(record.get("ehr_patient_id") or record.get("id")),
            "Patient Name": _text(record.get("name")),
            "Payer": _text(record.get("payer") or record.get("insurance")),
            "Admission Date": service_date,
            "Discharge Date": service_date,
            "Primary ICD-10-CM": diagnosis_codes[0] if diagnosis_codes else "",
            "Secondary ICD-10-CM": ";".join(diagnosis_codes[1:]),
        },
        "lines": lines,
        "gross": _amount(estimate.get("total")) or Decimal("0.00"),
    }


def register_generated_bill(bill: dict[str, Any], source: str) -> None:
    encounter_id = _text(bill.get("encounter_id"))
    patient = bill.get("patient")
    patient_id = _text(patient.get("Patient ID")) if isinstance(patient, dict) else ""
    if not encounter_id or not patient_id:
        raise ValueError("Generated bill requires patient and encounter identifiers.")

    generated_bills = st.session_state.setdefault("claim_generation_bills", {})
    generated_bills[f"{source}:{encounter_id}"] = {
        "source": source,
        "bill": _json_value(bill),
    }


def _rendering_provider(bill: dict[str, Any], patient: dict[str, Any]) -> dict[str, str]:
    provider = bill.get("rendering_provider") or bill.get("provider") or {}
    if not isinstance(provider, dict):
        provider = {"Name": _text(provider)}
    return {
        "Name": _text(
            provider.get("Name")
            or provider.get("Provider Name")
            or patient.get("Rendering Provider")
            or patient.get("Provider Name")
        ) or "DEMO RENDERING PROVIDER",
        "NPI": _text(provider.get("NPI") or patient.get("Provider NPI")) or "DEMO-NPI",
    }


def _facility_information(bill: dict[str, Any], patient: dict[str, Any]) -> dict[str, str]:
    facility = bill.get("facility") or bill.get("hospital") or {}
    if not isinstance(facility, dict):
        facility = {"Name": _text(facility)}
    return {
        "Name": _text(
            facility.get("Name")
            or facility.get("Facility Name")
            or patient.get("Facility Name")
            or patient.get("Hospital Name")
        ) or "DEMO FACILITY",
        "Facility ID": _text(
            facility.get("Facility ID")
            or facility.get("Provider ID")
            or patient.get("Facility ID")
        ) or "DEMO-FACILITY",
    }


def build_claim_record(
    bill: dict[str, Any],
    source: str = "",
    supporting_documents: dict[str, Any] | None = None,
    claim_type: str | None = None,
) -> dict[str, Any]:
    patient = bill.get("patient")
    lines = bill.get("lines")
    if not isinstance(patient, dict) or not isinstance(lines, list):
        raise ValueError("A generated bill with patient details and a charge-line list is required.")
    if not lines and claim_type is None:
        raise ValueError("A generated bill must contain at least one charge line.")

    total = _amount(bill.get("gross"))
    issues = _bill_issues(bill) if lines else []
    if issues:
        raise ValueError(" ".join(issues))
    encounter_id = _text(bill.get("encounter_id"))
    if not encounter_id:
        raise ValueError("A claim requires an encounter ID.")
    encounter_type = determine_encounter_type(bill, source)
    expected_claim_type = determine_claim_type(bill, source)
    claim_type = claim_type or expected_claim_type
    if claim_type not in {"837P", "837I"} or (encounter_type == "IPD" and claim_type != "837I"):
        raise ValueError(f"Encounter type {encounter_type} cannot generate {claim_type}.")
    revenue_mapping_issues = assign_revenue_codes(bill) if claim_type == "837I" else []

    diagnoses = _diagnosis_codes(bill)
    cpt_codes = []
    charges = []
    diagnosis_pointers = []
    revenue_codes = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        codes = _codes(
            line.get("CPT / HCPCS")
            or line.get("CPT/HCPCS Code")
            or line.get("CPT_HCPCS")
        )
        pointers = _codes(line.get("ICD-10-CM Pointer"))
        cpt_codes.extend(codes)
        charge = {
            "Service Description": _text(line.get("Service Description")),
            "CPT/HCPCS Codes": codes,
            "Quantity": _json_value(line.get("Quantity")),
            "Unit Charge ($)": _json_value(line.get("Unit Charge ($)")),
            "Charge Amount ($)": _json_value(line.get("Line Charge ($)")),
        }
        if claim_type == "837P":
            charge["Diagnosis Pointers"] = pointers
            diagnosis_pointers.append({
                "CPT/HCPCS Codes": codes,
                "Diagnosis Pointers": pointers,
            })
        else:
            revenue_code = _text(line.get("Revenue Code"))
            charge["Revenue Code"] = revenue_code
            if revenue_code:
                revenue_codes.append(revenue_code)
        charges.append(charge)

    claim_id = f"CLM-{uuid.uuid4().hex[:12].upper()}"
    supporting_documents = supporting_documents or validate_supporting_documents(bill)
    is_documentation_complete = supporting_documents.get("Overall Status") == "Complete"
    claim = {
        "Claim ID": claim_id,
        "Encounter ID": encounter_id,
        "Encounter Type": encounter_type,
        "Claim Type": claim_type,
        "Generated Format": claim_type,
        "Status": "Ready for Submission" if is_documentation_complete else "Pending Review",
        "Claim Amount": float(total),
        "Patient Information": _json_value(patient),
        "ICD-10 Codes": list(dict.fromkeys(diagnoses)),
        "CPT/HCPCS Codes": list(dict.fromkeys(cpt_codes)),
        "Charges": charges,
        "Supporting Documents Validation": _json_value(supporting_documents),
    }
    if claim["Claim Type"] == "837P":
        claim.update({
            "Rendering Provider": _rendering_provider(bill, patient),
            "Place of Service": _text(
                bill.get("place_of_service")
                or bill.get("Place of Service")
                or patient.get("Place of Service")
            ) or "11",
            "Diagnosis Pointers": diagnosis_pointers,
            "Professional Charges": charges,
        })
    else:
        claim.update({
            "Facility Information": _facility_information(bill, patient),
            "Admission Date": _json_value(patient.get("Admission Date")),
            "Discharge Date": _json_value(patient.get("Discharge Date")),
            "Revenue Codes": list(dict.fromkeys(revenue_codes)),
            "Bill Type": _text(
                bill.get("bill_type")
                or bill.get("Bill Type")
                or patient.get("Bill Type")
            ) or "111",
            "Facility Charges": charges,
            "Total Facility Amount": float(total),
        })
        if revenue_mapping_issues:
            claim["Revenue Code Mapping Issues"] = revenue_mapping_issues
    return claim


def _find_claim_for_encounter(
    claims: list[dict[str, Any]],
    encounter_id: str,
    claim_type: str | None = None,
) -> dict[str, Any] | None:
    return next(
        (
            claim for claim in reversed(claims)
            if _text(claim.get("Encounter ID")) == encounter_id
            and (claim_type is None or claim.get("Claim Type") == claim_type)
        ),
        None,
    )


def _split_opd_bill(bill: dict[str, Any]) -> dict[str, dict[str, Any]]:
    professional_lines = []
    facility_lines = []
    for line in bill.get("lines", []):
        revenue_code = _text(line.get("Revenue Code")).casefold()
        if revenue_code and revenue_code != "review_required":
            facility_lines.append(line)
        else:
            professional_lines.append(line)

    split_bills = {}
    for claim_type, lines in (
        ("837P", professional_lines),
        ("837I", facility_lines),
    ):
        split_bills[claim_type] = {
            **bill,
            "lines": lines,
            "gross": sum(
                (_amount(line.get("Line Charge ($)")) or Decimal("0.00") for line in lines),
                Decimal("0.00"),
            ),
        }
    return split_bills


def _claim_text(claim: dict[str, Any]) -> str:
    encounter_type = claim.get("Encounter Type")
    if not encounter_type:
        encounter_type = "IPD" if claim.get("Claim Type") == "837I" else "OPD"
    lines = [
        f"Claim ID: {claim['Claim ID']}",
        f"Encounter ID: {claim['Encounter ID']}",
        f"Encounter Type: {encounter_type}",
        f"Claim Type: {claim['Claim Type']}",
        f"Generated Format: {claim.get('Generated Format', claim['Claim Type'])}",
        f"Status: {claim['Status']}",
        f"Supporting Documents Status: {claim['Supporting Documents Validation']['Overall Status']}",
        f"Patient Information: {json.dumps(claim['Patient Information'], ensure_ascii=False)}",
        f"ICD-10 Codes: {', '.join(claim['ICD-10 Codes']) or 'Not recorded'}",
        f"CPT/HCPCS Codes: {', '.join(claim['CPT/HCPCS Codes']) or 'Not recorded'}",
    ]
    if claim["Claim Type"] == "837P":
        lines.extend((
            f"Rendering Provider: {json.dumps(claim['Rendering Provider'], ensure_ascii=False)}",
            f"Place of Service: {claim['Place of Service']}",
            f"Diagnosis Pointers: {json.dumps(claim['Diagnosis Pointers'], ensure_ascii=False)}",
        ))
    else:
        lines.extend((
            f"Facility Information: {json.dumps(claim['Facility Information'], ensure_ascii=False)}",
            f"Admission Date: {_text(claim['Admission Date']) or 'Not recorded'}",
            f"Discharge Date: {_text(claim['Discharge Date']) or 'Not recorded'}",
            f"Bill Type: {claim['Bill Type']}",
            f"Total Facility Amount: ${claim['Total Facility Amount']:.2f}",
            f"Revenue Codes: {', '.join(claim['Revenue Codes']) or 'Not recorded'}",
            *(
                [f"Revenue Code Mapping Review: {', '.join(claim['Revenue Code Mapping Issues'])}"]
                if claim.get("Revenue Code Mapping Issues")
                else []
            ),
        ))
    lines.append("Professional Charges:" if claim["Claim Type"] == "837P" else "Facility Charges:")
    lines.extend(
        "  - {codes} | {description} | Qty {quantity} | ${amount:.2f}{details}".format(
            codes=", ".join(charge["CPT/HCPCS Codes"]) or "No CPT/HCPCS",
            description=charge["Service Description"] or "Service",
            quantity=charge["Quantity"],
            amount=float(charge["Charge Amount ($)"]),
            details=(
                (
                    " | Diagnosis Pointers: "
                    + (", ".join(charge.get("Diagnosis Pointers", [])) or "None")
                )
                if claim["Claim Type"] == "837P"
                else (
                    " | Revenue Code: "
                    + (charge.get("Revenue Code") or "Not recorded")
                )
            ),
        )
        for charge in claim["Charges"]
    )
    lines.extend((
        f"Claim Amount: ${claim['Claim Amount']:.2f}",
        "Supporting Clinical Documents:",
        *(
            f"  - {row['CPT Code']} | {row['Procedure']} | "
            f"{row['Required Document']} | {row['Status']}"
            for row in claim["Supporting Documents Validation"]["Results"]
        ),
        "",
        "POC draft claim record; not a payer-ready X12 submission.",
        "",
    ))
    return "\n".join(lines)


def _document_table(results: list[dict[str, Any]]) -> None:
    rows = [
        [f"<code>{escape(_text(row['CPT Code']))}</code>", escape(_text(row["Procedure"])), escape(_text(row["Required Document"])),
         badge(row["Status"], "red" if row["Status"] == "Missing" else "green")]
        for row in results
    ]
    render_html_table(["CPT", "Procedure", "Required document", "Status"], rows,
                      ["blocked" if row["Status"] == "Missing" else "" for row in results])


def _key_value_tiles(values: dict[str, Any]) -> None:
    render_tiles([(label, _text(value) or "Not recorded") for label, value in values.items()])


def _charge_rows(bill: dict[str, Any], encounter_type: str) -> list[dict[str, Any]]:
    rows = []
    for line in bill.get("lines", []):
        if not isinstance(line, dict):
            continue
        has_revenue_code = _text(line.get("Revenue Code")).casefold() not in ("", "review_required")
        row = {}
        if encounter_type == "OPD":
            row["Billing Entity"] = "Facility" if has_revenue_code else "Professional"
        row.update({
            "Service Date": line.get("Service Date"),
            "CPT / HCPCS": line.get("CPT / HCPCS") or line.get("CPT/HCPCS Code"),
            "Service Description": line.get("Service Description"),
            "Quantity": line.get("Quantity"),
            "Unit Charge ($)": line.get("Unit Charge ($)"),
            "Line Charge ($)": line.get("Line Charge ($)"),
        })
        if encounter_type == "IPD" or has_revenue_code:
            row["Revenue Code"] = line.get("Revenue Code")
        rows.append(row)
    return rows


def _register_session_bills() -> None:
    """Make bills produced on the Billing and IPD pages available for claim generation.

    Consultation bills are persisted on the encounter record ("bill_estimate") when sent to
    RCM, so they survive new sessions and restarts.
    """
    for record in st.session_state.get("records", []):
        estimate = record.get("bill_estimate")
        if isinstance(estimate, dict):
            code_results = merge_code_results(record.get("code_results"), record.get("imported_codes"))
            try:
                register_generated_bill(build_ambient_bill(record, estimate, code_results), source=_text(estimate.get("encounter_type")) or "OPD")
            except ValueError:
                continue  # a saved bill without patient/encounter identifiers cannot become a claim
    ipd_bill = st.session_state.get("ipd_generated_bill")
    if isinstance(ipd_bill, dict):
        register_generated_bill(ipd_bill, source="IPD")


def _generate_claims(bill, selected_entry, encounter_type, existing_claims, supporting_documents, document_context, claims):
    missing_claim_types = [target_type for target_type, existing in existing_claims.items() if existing is None]
    if not missing_claim_types:
        st.warning("Claim already exists for this encounter and claim type.")
        return
    split_bills = _split_opd_bill(bill) if encounter_type == "OPD" else {}
    for target_type in missing_claim_types:
        try:
            claim = build_claim_record(
                split_bills.get(target_type, bill), selected_entry.get("source", ""),
                supporting_documents=supporting_documents, claim_type=target_type,
            )
        except ValueError as error:
            st.error(f"{target_type} claim was not generated: {error}")
            continue
        claim["Billing Entity"] = "Professional" if target_type == "837P" else "Facility"
        claim["Clinical Context"] = {
            "SOAP Note": _text(
                document_context.get("approved_soap_note") or document_context.get("soap_note") or document_context.get("summary")
            ) or _load_approved_soap_note(bill, document_context),
            "CDS Recommendations": _text(document_context.get("recommendations")),
            "Retrieved Guidelines": _text(document_context.get("retrieved_guidelines")),
        }
        claims.append(claim)
        st.session_state.latest_generated_claim_id = claim["Claim ID"]


def show_claim_generation_page() -> None:
    render_page("claim_generation")
    page_title("Billing & RCM", "Claim Generation", "Turn a generated bill into draft 837P / 837I claim records")
    _register_session_bills()
    generated_bills = st.session_state.get("claim_generation_bills", {})
    if not generated_bills:
        render_banner(
            "No generated bills yet",
            "Send an encounter to RCM from Billing & RCM, or generate a bill on IPD Billing; it will appear here.",
            icon="&#9432;",
        )
        with st.container(horizontal=True, gap="small"):
            st.button("Open Billing & RCM", key="claims_go_billing", type="primary", on_click=go_to, args=("billing",), disabled=not can_open("billing"))
            st.button("Open IPD Billing", key="claims_go_ipd", type="secondary", on_click=go_to, args=("ipd_billing",), disabled=not can_open("ipd_billing"))
        return

    patient_entries: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, entry in generated_bills.items():
        patient = entry.get("bill", {}).get("patient", {})
        patient_id = _text(patient.get("Patient ID"))
        if patient_id:
            patient_entries.setdefault(f"{_text(patient.get('Patient Name')) or 'Unnamed patient'} · {patient_id}", []).append((key, entry))
    if not patient_entries:
        render_banner("No generated bill contains the patient identifiers needed for claim generation.", tone="warn", icon="&#9888;")
        return

    patient_col, encounter_col = st.columns(2)
    patient_label = patient_col.selectbox("Patient", list(patient_entries), key="claim_generation_patient")
    encounter_options = {}
    for key, entry in patient_entries[patient_label]:
        label = f"{_text(entry['bill'].get('encounter_id'))} · {entry.get('source', 'OPD')}"
        encounter_options[f"{label} · {key}" if label in encounter_options else label] = key
    encounter_label = encounter_col.selectbox("Encounter", list(encounter_options), key=f"claim_generation_encounter_{patient_label}")
    selected_entry = generated_bills[encounter_options[encounter_label]]
    bill = selected_entry["bill"]
    encounter_type = determine_encounter_type(bill, selected_entry.get("source", ""))
    target_claim_types = ["837P", "837I"] if encounter_type == "OPD" else ["837I"]
    generated_format = " + ".join(target_claim_types)
    revenue_mapping_issues = assign_revenue_codes(bill) if encounter_type == "IPD" else []
    patient = bill.get("patient", {})
    document_context = {**patient, **_find_encounter_record(bill, st.session_state.get("records", []))}
    supporting_documents = validate_supporting_documents(bill, record=document_context, session_state=st.session_state)
    documents_complete = supporting_documents["Overall Status"] == "Complete"
    total = _amount(bill.get("gross"))

    render_tiles((
        ("Encounter type", encounter_type),
        ("Claim format", generated_format),
        ("Supporting documents", supporting_documents["Overall Status"]),
        ("Total bill", f"${total:,.2f}" if total is not None else "Unavailable"),
    ))

    details_col, entity_col = st.columns(2, gap="medium")
    with details_col:
        with st.container(border=True):
            card_title("Patient & encounter", _text(bill.get("encounter_id")))
            _key_value_tiles({
                "Patient ID": patient.get("Patient ID"), "Patient name": patient.get("Patient Name"),
                "Gender": patient.get("Gender"), "DOB": patient.get("DOB"), "Payer": patient.get("Payer"),
                "Admission": patient.get("Admission Date"), "Discharge": patient.get("Discharge Date"),
            })
    with entity_col:
        with st.container(border=True):
            if encounter_type == "OPD":
                card_title("Rendering provider")
                _key_value_tiles(_rendering_provider(bill, patient))
            card_title("Facility")
            _key_value_tiles(_facility_information(bill, patient))
            if revenue_mapping_issues:
                render_banner("Revenue codes need review", ", ".join(revenue_mapping_issues), tone="warn", icon="&#9888;")

    diagnosis_codes = list(dict.fromkeys(_diagnosis_codes(bill)))
    cpt_codes = list(dict.fromkeys(
        code for line in bill.get("lines", []) if isinstance(line, dict)
        for code in _codes(line.get("CPT / HCPCS") or line.get("CPT/HCPCS Code"))
    ))
    with st.container(border=True):
        card_title("Codes and charges", f"{len(diagnosis_codes)} diagnosis · {len(cpt_codes)} procedure code(s)")
        st.markdown(
            '<div class="nc-badge-row">'
            + ("".join(badge(code, "violet") for code in diagnosis_codes) or badge("No ICD-10 codes", "gray"))
            + ("".join(badge(code, "blue") for code in cpt_codes) or badge("No CPT/HCPCS codes", "gray"))
            + "</div>",
            unsafe_allow_html=True,
        )
        charge_rows = _charge_rows(bill, encounter_type)
        if charge_rows:
            st.dataframe(pd.DataFrame(charge_rows), hide_index=True, width="stretch")

    with st.container(border=True):
        card_title(
            "Supporting clinical documents", "Documentation each billed procedure needs before submission",
            trailing=badge(supporting_documents["Overall Status"], "green" if documents_complete else "red"),
        )
        if not documents_complete:
            render_banner("Claim may be denied", "Some required supporting documents are missing.", tone="danger", icon="&#9888;")
        if supporting_documents["Results"]:
            _document_table(supporting_documents["Results"])
        else:
            st.info("No CPT/HCPCS codes are present on this bill to validate.")

    claims = st.session_state.setdefault("generated_claim_records", [])
    encounter_id = _text(bill.get("encounter_id"))
    existing_claims = {target_type: _find_claim_for_encounter(claims, encounter_id, target_type) for target_type in target_claim_types}
    for target_type, existing_claim in existing_claims.items():
        if existing_claim is not None:
            render_banner(f"{target_type} claim already generated", existing_claim["Claim ID"], tone="ok", icon="&#10003;")
    bill_issues = _bill_issues(bill)
    if bill_issues:
        render_banner("Fix these before generating claims", " · ".join(bill_issues), tone="warn", icon="&#9888;")
    if st.button("Generate claims", type="primary", key="generate_claim_record", icon=":material/description:", disabled=bool(bill_issues)):
        _generate_claims(bill, selected_entry, encounter_type, existing_claims, supporting_documents, document_context, claims)

    if not claims:
        return
    with st.container(border=True):
        card_title("Claim summary", f"{len(claims)} draft claim record(s) this session")
        st.dataframe(pd.DataFrame([
            {
                "Claim ID": claim["Claim ID"],
                "Claim Type": claim["Claim Type"],
                "Billing Entity": claim.get("Billing Entity", "Professional" if claim["Claim Type"] == "837P" else "Facility"),
                "Status": claim["Status"],
                "Amount": f"${claim.get('Claim Amount', claim.get('Total Facility Amount', claim.get('Total Claim Amount', 0))):,.2f}",
            }
            for claim in claims
        ]), hide_index=True, width="stretch")
        selected_claims = [
            claim for target_type in target_claim_types
            if (claim := _find_claim_for_encounter(claims, encounter_id, target_type)) is not None
        ]
        if selected_claims:
            claim_validation = selected_claims[0]["Supporting Documents Validation"]
            st.markdown(f'<div class="nc-label" style="margin-top:.6rem">Claim documentation · {escape(claim_validation["Overall Status"])}</div>', unsafe_allow_html=True)
            if claim_validation["Results"]:
                _document_table(claim_validation["Results"])
        for selected_claim in selected_claims:
            patient_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", _text(selected_claim["Patient Information"].get("Patient Name")) or "patient").strip("_") or "patient"
            with st.container(horizontal=True, gap="small"):
                st.download_button(
                    f"Download {selected_claim['Claim Type']} (JSON)", data=json.dumps(selected_claim, indent=2, ensure_ascii=False),
                    file_name=f"{selected_claim['Claim ID']}_{patient_slug}.json", mime="application/json",
                    key=f"download_claim_json_{selected_claim['Claim ID']}", icon=":material/download:",
                )
                st.download_button(
                    f"Download {selected_claim['Claim Type']} (TXT)", data=_claim_text(selected_claim),
                    file_name=f"{selected_claim['Claim ID']}_{patient_slug}.txt", mime="text/plain",
                    key=f"download_claim_text_{selected_claim['Claim ID']}", icon=":material/download:",
                )
