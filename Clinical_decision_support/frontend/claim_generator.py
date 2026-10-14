import re
from datetime import date, datetime
from decimal import Decimal

try:
    from frontend.chargemaster import assign_revenue_codes
except ModuleNotFoundError:
    from chargemaster import assign_revenue_codes


def _text(value):
    return "" if value is None else str(value).strip()


def _element(value):
    return re.sub(r"\s+", " ", _text(value).replace("*", " ").replace("~", " ").replace(":", " ")).strip().upper()


def _date(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = _text(value)
    if not text:
        raise ValueError("A required claim date is missing.")
    try:
        return date.fromisoformat(text[:10]).strftime("%Y%m%d")
    except ValueError as error:
        raise ValueError(f"Invalid claim date: {text}") from error


def _amount(value):
    amount = Decimal(str(value).replace("$", "").replace(",", "").strip())
    return f"{amount:.2f}"


def _name_parts(value):
    parts = _element(value).split()
    return (parts[-1], " ".join(parts[:-1])) if parts else ("", "")


def _append_diagnoses(segments, patient):
    diagnoses = []
    for field in ("Primary ICD-10-CM", "Secondary ICD-10-CM"):
        diagnoses.extend(
            code.strip().upper()
            for code in re.split(r"[;,\n]+", _text(patient.get(field)))
            if code.strip()
        )
    for index, code in enumerate(dict.fromkeys(diagnoses)):
        qualifier = "ABK" if index == 0 else "ABF"
        segments.append(f"HI*{qualifier}:{_element(code)}~")


def generate_837(bill, claim_type):
    if claim_type not in {"837I", "837P"}:
        raise ValueError("Claim type must be 837I or 837P.")
    if claim_type == "837I":
        unresolved = assign_revenue_codes(bill)
        if unresolved:
            raise ValueError(
                "REVIEW_REQUIRED: no reliable revenue-code mapping was found for "
                + ", ".join(unresolved)
                + "."
            )

    patient = bill["patient"]
    lines = bill["lines"]
    claim_id = _element(bill["encounter_id"])
    payer_name = _element(patient.get("Payer"))
    patient_id = _element(patient.get("Patient ID"))
    patient_last, patient_first = _name_parts(patient.get("Patient Name"))
    total = _amount(bill["gross"])
    facility_code = "21" if claim_type == "837P" else "11"
    today = datetime.now()
    interchange_date = today.strftime("%y%m%d")
    interchange_time = today.strftime("%H%M")
    transaction_id = "0001"
    group_code = "X222A1" if claim_type == "837P" else "X223A2"
    segments = [
        "ISA*00*          *00*          *ZZ*SUBMITTER      *ZZ*RECEIVER       *"
        f"{interchange_date}*{interchange_time}*^*00501*000000001*0*T*:~",
        f"GS*HC*SUBMITTER*RECEIVER*{today:%Y%m%d}*{interchange_time}*1*X*005010{group_code}~",
        f"ST*837*{transaction_id}*005010{group_code}~",
        f"BHT*0019*00*{claim_id}*{today:%Y%m%d}*{interchange_time}*CH~",
        "NM1*41*2*SUBMITTER*****46*SUBMITTER~",
        "PER*IC*BILLING OFFICE*TE*0000000000~",
        f"NM1*40*2*{payer_name}~",
        "HL*1**20*1~",
        "NM1*85*2*BILLING PROVIDER~",
        "HL*2*1*22*0~",
        "SBR*P*18******CI~",
        f"NM1*IL*1*{patient_last}*{patient_first}****MI*{patient_id}~",
        f"NM1*PR*2*{payer_name}~",
        f"CLM*{claim_id}*{total}***{facility_code}:B:1*Y*A*Y*Y~",
    ]

    if claim_type == "837I":
        segments.append(f"DTP*434*D8*{_date(patient.get('Admission Date'))}~")
        discharge_date = patient.get("Discharge Date")
        if _text(discharge_date):
            segments.append(f"DTP*435*D8*{_date(discharge_date)}~")

    _append_diagnoses(segments, patient)
    for index, line in enumerate(lines, start=1):
        segments.append(f"LX*{index}~")
        if claim_type == "837I":
            codes = re.split(r"[;,\n]+", _text(line.get("CPT / HCPCS")))
            procedure = next((code.strip() for code in codes if code.strip()), "")
            product = f"HC:{_element(procedure)}" if procedure else ""
            segments.append(
                f"SV2*{_element(line.get('Revenue Code'))}*{product}*"
                f"{_amount(line['Line Charge ($)'])}*UN*{_amount(line['Quantity'])}~"
            )
        else:
            codes = re.split(r"[;,\n]+", _text(line.get("CPT / HCPCS")))
            procedure = next((code.strip() for code in codes if code.strip()), "")
            segments.append(
                f"SV1*HC:{_element(procedure)}*{_amount(line['Line Charge ($)'])}"
                f"*UN*{_amount(line['Quantity'])}***1~"
            )
        segments.append(f"DTP*472*D8*{_date(line.get('Service Date'))}~")

    segments.append(f"SE*{len(segments) - 1}*{transaction_id}~")
    segments.extend(("GE*1*1~", "IEA*1*000000001~"))
    return "\n".join(segments) + "\n"
