"""Pure helpers over an EHR chart: dates, context strip, medication safety and care gaps."""

import math
import re
from datetime import datetime

import pandas as pd


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
        row
        for row in chart_rows(chart, "Allergies")
        if is_active_row(row) and str(row.get("Category", "Drug")).lower() in {"drug", "medication", ""}
    ]


def chart_context(chart):
    problems = [
        row.get("Problem_Name") for row in chart_rows(chart, "Problems") if is_active_row(row) and row.get("Problem_Name")
    ]
    medications = [
        row.get("Medication") for row in chart_rows(chart, "Medications") if is_active_row(row) and row.get("Medication")
    ]
    allergies = [row.get("Substance") for row in chart_rows(chart, "Allergies") if is_active_row(row) and row.get("Substance")]
    vitals_rows = latest_first(chart_rows(chart, "Vitals"), "Observed_Date")
    vitals = "—"
    if vitals_rows:
        latest = vitals_rows[0]
        parts = []
        if latest.get("Systolic_mmHg") and latest.get("Diastolic_mmHg"):
            parts.append(f"BP {latest['Systolic_mmHg']}/{latest['Diastolic_mmHg']}")
        if latest.get("Temperature_F"):
            parts.append(f"T {latest['Temperature_F']}°F")
        if latest.get("SpO2_Percent"):
            parts.append(f"SpO₂ {latest['SpO2_Percent']}%")
        if latest.get("Respiratory_Rate"):
            parts.append(f"RR {latest['Respiratory_Rate']}")
        if latest.get("Heart_Rate_bpm"):
            parts.append(f"HR {latest['Heart_Rate_bpm']}")
        vitals = " · ".join(parts) or "—"
    lab_rows = latest_first(chart_rows(chart, "Labs"), "Result_Date")
    a1c = next((row for row in lab_rows if "a1c" in str(row.get("Test_Name", "")).lower()), None)
    flagged = [row for row in lab_rows if str(row.get("Flag", "")).lower() not in {"", "normal"}]
    lab_parts = []
    if a1c:
        lab_parts.append(f"HbA1c {a1c.get('Result')}{a1c.get('Unit', '')} · {format_date(a1c.get('Result_Date'))}")
    for row in flagged[:1]:
        if row is not a1c:
            lab_parts.append(f"{row.get('Test_Name')} {row.get('Result')} {row.get('Unit', '')} ({row.get('Flag')})")
    return {
        "problems": " · ".join(problems[:3]) or "None recorded",
        "medications": " · ".join(medications[:3]) or "None recorded",
        "allergies": " · ".join(allergies) or "NKDA",
        "vitals": vitals,
        "labs": " · ".join(lab_parts) or "—",
    }


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
            [
                row
                for row in chart_rows(chart, "Labs")
                if re.search(r"egfr|creatinine", str(row.get("Test_Name", "")), re.IGNORECASE)
            ],
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
    problem_text = " ".join(
        f"{row.get('Problem_Name', '')} {row.get('ICD_10_CM', '')}" for row in problems if is_active_row(row)
    ).lower()
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
            gaps.append(
                (
                    "HbA1c control",
                    "Met",
                    "green",
                    f"Latest HbA1c {a1c_value:g}%" if a1c_value is not None else "Recent HbA1c on file",
                )
            )
        activity = " ".join(
            f"{row.get('Order_Description', '')} {row.get('Type', '')} {row.get('Department', '')}"
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
