import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

try:
    from frontend.chargemaster import assign_revenue_codes
except ModuleNotFoundError:
    from chargemaster import assign_revenue_codes

SAMPLE_NCCI_CONFLICTS = {
    frozenset(("99213", "99214")),
    frozenset(("99213", "99215")),
    frozenset(("99214", "99215")),
}
SAMPLE_MUE_LIMITS = {
    "70450": 1,
    "71045": 1,
    "80053": 1,
    "99213": 1,
    "99214": 1,
    "99215": 1,
}
SAMPLE_LCD_DIAGNOSIS_PREFIXES = {
    "70450": ("R51", "R29", "I63", "S09"),
}


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


def validate_claim(claim, document_results=None):
    """Run lightweight required-field, documentation, and sample-rule checks."""
    claim = claim if isinstance(claim, dict) else {}
    errors = []
    warnings = []
    checks = []

    def add_check(category, label, passed, message, failure_status="ERROR"):
        status = "PASS" if passed else failure_status
        checks.append({
            "Category": category,
            "Check": label,
            "Status": status,
            "Message": message,
        })
        if not passed:
            (errors if status == "ERROR" else warnings).append(message)

    patient = claim.get("Patient Information")
    patient = patient if isinstance(patient, dict) else {}
    provider = claim.get("Provider Information")
    if not isinstance(provider, dict):
        provider = claim.get("Rendering Provider") or claim.get("Provider")
    provider = provider if isinstance(provider, dict) else {}

    required_fields = (
        ("Patient Name", _text(patient.get("Patient Name")), "Patient Name is missing."),
        ("DOB", _text(patient.get("DOB") or patient.get("Date of Birth")), "DOB is missing."),
        (
            "Provider Name",
            _text(provider.get("Name") or provider.get("Provider Name")),
            "Provider Name is missing.",
        ),
        ("Provider NPI", _text(provider.get("NPI")), "NPI is missing."),
    )
    for label, value, message in required_fields:
        add_check("Required Field Validation", label, bool(value), message)

    dob = patient.get("DOB") or patient.get("Date of Birth")
    if _text(dob) and not _date(dob):
        warnings.append("DOB is present but is not a valid ISO date.")
        checks.append({
            "Category": "Required Field Validation",
            "Check": "DOB format",
            "Status": "WARNING",
            "Message": "DOB is present but is not a valid ISO date.",
        })

    charges = claim.get("Charges")
    charges = charges if isinstance(charges, list) else []
    service_dates = [
        charge.get("Date of Service") or charge.get("Service Date")
        for charge in charges
        if isinstance(charge, dict)
    ]
    date_of_service = claim.get("Date of Service") or next(
        (value for value in service_dates if _text(value)),
        None,
    )
    if not _text(date_of_service):
        add_check("Required Field Validation", "Date of Service", False, "Date of Service is missing.")
    elif not _date(date_of_service):
        warnings.append("Date of Service is present but is not a valid ISO date.")
        checks.append({
            "Category": "Required Field Validation",
            "Check": "Date of Service format",
            "Status": "WARNING",
            "Message": "Date of Service is present but is not a valid ISO date.",
        })

    diagnosis_codes = claim.get("ICD-10 Codes")
    if not isinstance(diagnosis_codes, (list, tuple, set)):
        diagnosis_codes = _codes(diagnosis_codes)
    diagnosis_codes = [_text(code).upper() for code in diagnosis_codes if _text(code)]
    cpt_codes = claim.get("CPT/HCPCS Codes")
    if not isinstance(cpt_codes, (list, tuple, set)):
        cpt_codes = _codes(cpt_codes)
    cpt_codes = list(dict.fromkeys(_text(code) for code in cpt_codes if _text(code)))
    add_check(
        "Required Field Validation",
        "ICD Codes Present",
        bool(diagnosis_codes),
        "At least one ICD code is required.",
    )
    add_check(
        "Required Field Validation",
        "CPT/HCPCS Codes Present",
        bool(cpt_codes),
        "At least one CPT code is required.",
    )

    total = _number(
        claim.get("Claim Amount")
        if claim.get("Claim Amount") is not None
        else claim.get("Total Facility Amount")
    )
    add_check(
        "Required Field Validation",
        "Charge Amount Greater Than Zero",
        total is not None and total > 0,
        "Total charge amount must be greater than zero.",
    )

    npi = _text(provider.get("NPI"))
    if npi and (not npi.isdigit() or len(npi) != 10):
        warnings.append("NPI is present but is not a 10-digit number.")
        checks.append({
            "Category": "Required Field Validation",
            "Check": "Provider NPI format",
            "Status": "WARNING",
            "Message": "NPI is present but is not a 10-digit number.",
        })

    uses_repository_documents = document_results is not None
    if document_results is None:
        document_validation = claim.get("Supporting Documents Validation")
        document_results = (
            document_validation.get("Results", [])
            if isinstance(document_validation, dict)
            else []
        )
        document_results = document_results if isinstance(document_results, list) else []
    for code in cpt_codes:
        matching_documents = [
            row for row in document_results
            if isinstance(row, dict) and _text(row.get("CPT Code")) == code
        ]
        if uses_repository_documents and matching_documents and any(
            "Document Available" in _text(row.get("Status"))
            for row in matching_documents
        ):
            document_present = True
            document_message = "✅ Document Available"
        elif uses_repository_documents:
            document_present = False
            document_message = "⚠ Missing Supporting Document"
        else:
            document_present = any(
                _text(row.get("Status")).casefold() == "present"
                for row in matching_documents
            )
            document_message = "Required supporting document not found."
        add_check(
            "Documentation Validation",
            f"{code} supporting document",
            document_present,
            document_message,
            "WARNING",
        )

    normalized_codes = {re.sub(r"\D", "", code) for code in cpt_codes}
    ncci_conflicts = [
        sorted(pair)
        for pair in SAMPLE_NCCI_CONFLICTS
        if pair.issubset(normalized_codes)
    ]
    if ncci_conflicts:
        for first_code, second_code in sorted(ncci_conflicts):
            add_check(
                "NCCI Validation",
                f"{first_code} + {second_code}",
                False,
                f"Potential NCCI conflict between CPT {first_code} and {second_code}; review before submission.",
                "WARNING",
            )
    else:
        add_check(
            "NCCI Validation",
            "Sample code-pair edits",
            True,
            "No conflicts found in the configured sample NCCI rules.",
        )

    for line in charges:
        if not isinstance(line, dict):
            continue
        line_codes = line.get("CPT/HCPCS Codes")
        if not isinstance(line_codes, (list, tuple, set)):
            line_codes = _codes(line_codes)
        for raw_code in line_codes:
            code = _text(raw_code)
            limit = SAMPLE_MUE_LIMITS.get(re.sub(r"\D", "", code))
            units = _number(line.get("Quantity"))
            if limit is None or units is None:
                continue
            exceeds_limit = units > limit
            add_check(
                "MUE Validation",
                f"{code} units",
                not exceeds_limit,
                (
                    f"CPT {code} has {units} units, exceeding the local sample MUE limit of {limit}."
                    if exceeds_limit
                    else f"CPT {code} units are within the local sample MUE limit of {limit}."
                ),
                "WARNING",
            )

    for code in cpt_codes:
        prefixes = SAMPLE_LCD_DIAGNOSIS_PREFIXES.get(re.sub(r"\D", "", code))
        if prefixes is None:
            continue
        diagnosis_supported = any(
            diagnosis.startswith(prefix) for diagnosis in diagnosis_codes
            for prefix in prefixes
        )
        add_check(
            "LCD Validation",
            f"{code} diagnosis support",
            diagnosis_supported,
            (
                f"CPT {code} diagnosis may not meet configured sample LCD requirements; review the diagnosis and payer policy."
                if not diagnosis_supported
                else f"CPT {code} has a diagnosis matching the configured sample LCD rule."
            ),
            "WARNING",
        )

    score = max(0, 100 - 20 * len(errors) - 5 * len(warnings))
    return {
        "Claim ID": _text(claim.get("Claim ID")) or "Unknown",
        "Validation Score": score,
        "Status": "PASS" if not errors else "FAIL",
        "Errors": errors,
        "Warnings": warnings,
        "Checks": checks,
    }
