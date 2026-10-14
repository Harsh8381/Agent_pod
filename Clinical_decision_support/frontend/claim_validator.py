import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

try:
    from frontend.chargemaster import assign_revenue_codes
except ModuleNotFoundError:
    from chargemaster import assign_revenue_codes


def _text(value):
    return "" if value is None else str(value).strip()


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _number(value):
    try:
        number = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _codes(value):
    return [code.strip() for code in re.split(r"[;,\n]+", _text(value)) if code.strip()]


def validate_bill(bill, claim_type):
    issues = []
    if claim_type not in {"837I", "837P"}:
        return ["Claim type must be 837I or 837P."]
    if not isinstance(bill, dict):
        return ["Generated bill data is missing or invalid."]

    patient = bill.get("patient")
    lines = bill.get("lines")
    if not isinstance(patient, dict):
        return ["Patient details are missing from the generated bill."]
    if not isinstance(lines, list) or not lines:
        return ["At least one bill line is required."]

    unresolved_revenue_lines = assign_revenue_codes(bill) if claim_type == "837I" else []

    required_patient_fields = {
        "Encounter ID": bill.get("encounter_id"),
        "Patient ID": patient.get("Patient ID"),
        "Patient Name": patient.get("Patient Name"),
        "Payer": patient.get("Payer"),
    }
    for label, value in required_patient_fields.items():
        if not _text(value):
            issues.append(f"{label} is required.")

    diagnoses = _codes(patient.get("Primary ICD-10-CM"))
    diagnoses.extend(_codes(patient.get("Secondary ICD-10-CM")))
    if not diagnoses:
        issues.append("At least one ICD-10-CM diagnosis code is required.")
    if not _date(patient.get("Admission Date")):
        issues.append("A valid admission date is required.")

    for index, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            issues.append(f"Service line {index} is invalid.")
            continue
        quantity = _number(line.get("Quantity"))
        amount = _number(line.get("Line Charge ($)"))
        if quantity is None or quantity <= 0:
            issues.append(f"Service line {index} needs a positive quantity.")
        if amount is None or amount < 0:
            issues.append(f"Service line {index} needs a valid non-negative charge.")
        if not _date(line.get("Service Date")):
            issues.append(f"Service line {index} needs a valid service date.")
        if claim_type == "837I" and not _text(line.get("Revenue Code")):
            issues.append(f"Service line {index} needs a revenue code for 837I.")
        if claim_type == "837P" and not _codes(line.get("CPT / HCPCS")):
            issues.append(f"Service line {index} needs a CPT/HCPCS code for 837P.")

    if unresolved_revenue_lines:
        issues.append(
            "REVIEW_REQUIRED: no reliable revenue-code mapping was found for "
            + ", ".join(unresolved_revenue_lines)
            + "."
        )

    gross = _number(bill.get("gross"))
    line_total = sum(
        (_number(line.get("Line Charge ($)")) or Decimal("0") for line in lines if isinstance(line, dict)),
        Decimal("0"),
    )
    if gross is None or gross < 0 or gross != line_total:
        issues.append("Bill total must equal the sum of valid service-line charges.")

    return issues
