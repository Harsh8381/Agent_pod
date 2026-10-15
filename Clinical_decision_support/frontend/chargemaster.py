from __future__ import annotations

import os
import json
import re
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]

_CODE_ALIASES = {
    "cpthcpcscode",
    "cpthcpcs",
    "cptcode",
    "hcpcscode",
    "procedurecode",
    "code",
}
_RATE_ALIASES = {
    "rate",
    "charge",
    "unitcharge",
    "grossunitcharge",
    "unitchargeusd",
    "estimatedrate",
    "standardcharge",
    "grosscharge",
}
_DESCRIPTION_ALIASES = {
    "proceduredescription",
    "chargedescription",
    "description",
    "servicedescription",
}
_REVENUE_CODE_ALIASES = {"revenuecode"}
_DEPARTMENT_ALIASES = {"department", "servicedepartment"}
_CATEGORY_ALIASES = {"category", "servicecategory", "chargetype", "servicearea"}
_QUANTITY_ALIASES = {"quantity", "units", "quantityunits"}
_RESULT_FIELDS = (
    "Date",
    "CPT/HCPCS Code",
    "Procedure Description",
    "Quantity/Units",
    "Estimated Rate",
    "Rate Source Status",
)


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _clean_cell(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _normalize_code(value: Any) -> str:
    return re.sub(r"[\s.-]", "", _clean_cell(value).upper())


def _find_column(frame: pd.DataFrame, aliases: set[str]) -> Any | None:
    return next(
        (column for column in frame.columns if _normalize_header(column) in aliases),
        None,
    )


def _revenue_code(value: Any) -> str:
    text = _clean_cell(value)
    match = re.fullmatch(r"(\d+)(?:\.0+)?", text)
    if not match:
        return ""
    digits = match.group(1)
    return digits.zfill(4) if 1 <= len(digits) <= 4 else ""


def _mapping_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _clean_cell(value).casefold())


def _revenue_frames(source: Path, encounter_type: str) -> list[pd.DataFrame]:
    if _is_structured_chargemaster(source):
        frames = []
        for sheet_name in (_encounter_sheet(encounter_type), "CDM Master"):
            try:
                frame = _read_structured_sheet(source, sheet_name)
            except (OSError, ValueError, KeyError, json.JSONDecodeError, ImportError):
                continue
            frames.append(frame)
        return frames
    try:
        if source.suffix.lower() == ".csv":
            return [pd.read_csv(source, dtype=str, keep_default_na=False)]
        if source.suffix.lower() == ".xlsx":
            return [pd.read_excel(source, dtype=str, keep_default_na=False, engine="openpyxl")]
        if source.suffix.lower() == ".json":
            payload = json.loads(source.read_text(encoding="utf-8"))
            frames = []
            for sheet in payload.get("sheets", {}).values():
                rows = sheet.get("rows", [])
                if rows and len(rows) > 3 and rows[3]:
                    headers = [
                        str(value).strip() if value is not None else f"Column_{index + 1}"
                        for index, value in enumerate(rows[3])
                    ]
                    frames.append(pd.DataFrame(rows[4:], columns=headers, dtype=str).fillna(""))
                elif sheet.get("records"):
                    frames.append(pd.DataFrame(sheet["records"], dtype=str).fillna(""))
            return frames
    except (OSError, ValueError, json.JSONDecodeError, ImportError):
        return []
    return []


def _unique_revenue_code(
    frames: list[pd.DataFrame],
    aliases: set[str],
    key: Any,
) -> str:
    normalized_key = _mapping_key(key)
    if not normalized_key:
        return ""
    for frame in frames:
        key_column = _find_column(frame, aliases)
        revenue_column = _find_column(frame, _REVENUE_CODE_ALIASES)
        if key_column is None or revenue_column is None:
            continue
        matches = frame[frame[key_column].map(_mapping_key) == normalized_key]
        codes = {
            code for code in matches[revenue_column].map(_revenue_code)
            if code
        }
        if len(codes) == 1:
            return next(iter(codes))
    return ""


def assign_revenue_codes(bill: dict[str, Any]) -> list[str]:
    """Populate 837I revenue codes in-place and return unresolved service lines."""
    lines = bill.get("lines")
    if not isinstance(lines, list):
        return []
    source = _configured_path()
    if source is None or not source.is_file():
        frames = []
    else:
        frames = _revenue_frames(source, str(bill.get("encounter_type") or "OPD"))

    unresolved = []
    for index, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            continue
        line["Revenue Code"] = _revenue_code(line.get("Revenue Code"))
        if not line["Revenue Code"]:
            lookup_order = (
                ({"cpthcpcscode", "cpthcpcs", "cptcode", "hcpcscode", "procedurecode", "code"},
                 line.get("CPT / HCPCS")),
                (_DEPARTMENT_ALIASES, line.get("Department") or line.get("Service Department")),
                (_CATEGORY_ALIASES, line.get("Service Category") or line.get("Category")),
                (_DESCRIPTION_ALIASES, line.get("Service Description") or line.get("Description")),
            )
            for aliases, value in lookup_order:
                code = _unique_revenue_code(frames, aliases, value)
                if code:
                    line["Revenue Code"] = code
                    break
        if not line["Revenue Code"]:
            line["Revenue Code"] = "REVIEW_REQUIRED"
            cpt_code = _clean_cell(line.get("CPT / HCPCS")) or "not provided"
            unresolved.append(f"Service line {index} (CPT/HCPCS {cpt_code})")
    return unresolved


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["code", "rate", "description", "quantity"])


def _workbook_sheet_names(source: Path) -> set[str]:
    try:
        if source.suffix.lower() == ".xlsx":
            return set(pd.ExcelFile(source, engine="openpyxl").sheet_names)
        if source.suffix.lower() == ".json":
            payload = json.loads(source.read_text(encoding="utf-8"))
            return set(payload.get("sheets", {}))
    except (OSError, ValueError, json.JSONDecodeError, ImportError):
        return set()
    return set()


def _is_structured_chargemaster(source: Path) -> bool:
    sheets = _workbook_sheet_names(source)
    return {"OPD Charges", "IPD Charges", "CDM Master"}.issubset(sheets)


def _read_structured_sheet(source: Path, sheet_name: str) -> pd.DataFrame:
    if source.suffix.lower() == ".xlsx":
        return pd.read_excel(source, sheet_name=sheet_name, header=3, dtype=str, keep_default_na=False, engine="openpyxl")

    payload = json.loads(source.read_text(encoding="utf-8"))
    sheet = payload.get("sheets", {}).get(sheet_name, {})
    rows = sheet.get("rows", [])
    if rows and len(rows) > 3 and rows[3]:
        headers = [str(value).strip() if value is not None else f"Column_{index + 1}" for index, value in enumerate(rows[3])]
        return pd.DataFrame(rows[4:], columns=headers, dtype=str).fillna("")
    headers = sheet.get("headers", [])
    records = sheet.get("records", [])
    return pd.DataFrame(records, columns=headers or None, dtype=str).fillna("")


def _configured_path(path: str | Path | None = None) -> Path | None:
    if path is None:
        load_dotenv(PROJECT_ROOT / ".env")
        if "CHARGEMASTER_PATH" in os.environ:
            configured = os.getenv("CHARGEMASTER_PATH", "").strip()
            if not configured:
                return None
        else:
            athena_xlsx = PROJECT_ROOT / "Hospital_IPD_OPD_Charges_Athena_Care_Gap.xlsx"
            athena_json = PROJECT_ROOT / "Hospital_IPD_OPD_Charges_Athena_Care_Gap.json"
            legacy_xlsx = PROJECT_ROOT / "Original_Hospital_IPD_OPD_Charges_200_Demo.xlsx"
            configured = next(
                (candidate.name for candidate in (athena_xlsx, athena_json, legacy_xlsx) if candidate.is_file()),
                athena_xlsx.name,
            )
    else:
        configured = str(path).strip()
    if not configured:
        return None

    source = Path(configured).expanduser()
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    return source.resolve()


def load_chargemaster(path: str | Path | None = None) -> tuple[pd.DataFrame, str]:
    """Load and normalize a configured CSV or XLSX chargemaster."""
    source = _configured_path(path)
    if source is None:
        return _empty_frame(), "Chargemaster unavailable"

    suffix = source.suffix.lower()
    if suffix not in {".csv", ".xlsx", ".json"}:
        return _empty_frame(), "Unsupported format"
    if not source.is_file():
        return _empty_frame(), "Chargemaster unavailable"

    try:
        if suffix == ".csv":
            frame = pd.read_csv(source, dtype=str, keep_default_na=False)
        elif _is_structured_chargemaster(source):
            encounter_type = (os.getenv("NUUCARE_ENCOUNTER_TYPE") or "OPD").strip().upper()
            sheet_name = "IPD Charges" if encounter_type in {"IPD", "INPATIENT"} else "OPD Charges"
            frame = _read_structured_sheet(source, sheet_name)
        elif suffix == ".xlsx":
            frame = pd.read_excel(source, dtype=str, keep_default_na=False, engine="openpyxl")
        else:
            return _empty_frame(), "Unsupported format"
    except Exception:
        return _empty_frame(), "Chargemaster unavailable"

    code_column = _find_column(frame, _CODE_ALIASES)
    rate_column = _find_column(frame, _RATE_ALIASES)
    if code_column is None or rate_column is None:
        return _empty_frame(), "Missing required columns"

    description_column = _find_column(frame, _DESCRIPTION_ALIASES)
    quantity_column = _find_column(frame, _QUANTITY_ALIASES)
    normalized = pd.DataFrame({
        "code": frame[code_column].map(_normalize_code),
        "rate": frame[rate_column].map(_clean_cell),
        "description": frame[description_column].map(_clean_cell) if description_column is not None else "",
        "quantity": frame[quantity_column].map(_clean_cell) if quantity_column is not None else "",
    })
    return normalized[normalized["code"] != ""], "Found"


def _encounter_sheet(encounter_type: str) -> str:
    normalized = re.sub(r"[^a-z]", "", str(encounter_type or "").lower())
    return "IPD Charges" if normalized in {"ipd", "inpatient", "inpatientdemo"} else "OPD Charges"


def _decimal_or_none(value: Any) -> Decimal | None:
    return _parse_rate(_clean_cell(value))


def _active_cdm_codes(source: Path) -> tuple[set[str], set[str]]:
    """Return currently active codes and codes present in CDM, using CDM as a gate when available."""
    try:
        cdm = _read_structured_sheet(source, "CDM Master")
    except (OSError, ValueError, KeyError, json.JSONDecodeError, ImportError):
        return set(), set()
    code_column = _find_column(cdm, {"cpthcpcs", "cpthcpcscode"})
    status_column = _find_column(cdm, {"status"})
    effective_column = _find_column(cdm, {"effectivedate"})
    termination_column = _find_column(cdm, {"terminationdate"})
    if code_column is None:
        return set(), set()
    present: set[str] = set()
    active: set[str] = set()
    today = date.today()
    for _, row in cdm.iterrows():
        code = _normalize_code(row[code_column])
        if not code:
            continue
        present.add(code)
        status = _clean_cell(row[status_column]).casefold() if status_column is not None else "active"
        try:
            effective = pd.to_datetime(row[effective_column], errors="coerce").date() if effective_column is not None and _clean_cell(row[effective_column]) else None
            termination = pd.to_datetime(row[termination_column], errors="coerce").date() if termination_column is not None and _clean_cell(row[termination_column]) else None
        except (TypeError, ValueError):
            effective, termination = None, None
        if status == "active" and (effective is None or effective <= today) and (termination is None or termination >= today):
            active.add(code)
    return active, present


def price_cpt_bill_lines(
    cpt_results: list[dict[str, Any]],
    encounter_type: str = "OPD",
) -> list[dict[str, Any]]:
    """Price reviewed CPT lines; missing/invalid prices are returned but never totaled."""
    if not cpt_results:
        return []
    source = _configured_path()
    source_sheet = _encounter_sheet(encounter_type)
    frame = pd.DataFrame()
    load_status = "Chargemaster unavailable"
    active_cdm: set[str] = set()
    known_cdm: set[str] = set()
    if source is not None and source.is_file() and _is_structured_chargemaster(source):
        try:
            frame = _read_structured_sheet(source, source_sheet)
            load_status = "Found"
            active_cdm, known_cdm = _active_cdm_codes(source)
        except (OSError, ValueError, KeyError, json.JSONDecodeError, ImportError):
            load_status = "Chargemaster unavailable"
    elif source is not None and source.is_file():
        frame, load_status = load_chargemaster(source)

    code_column = _find_column(frame, _CODE_ALIASES)
    rate_column = _find_column(frame, _RATE_ALIASES)
    description_column = _find_column(frame, _DESCRIPTION_ALIASES)
    quantity_column = _find_column(frame, _QUANTITY_ALIASES)
    output = []
    for candidate in cpt_results:
        code = _clean_cell(candidate.get("CPT/HCPCS Code", ""))
        normalized_code = _normalize_code(code)
        description = _clean_cell(candidate.get("Matched Procedure/Service", "")) or "Not specified"
        raw_quantity = candidate.get("quantity", candidate.get("Quantity", 1))
        quantity = _decimal_or_none(raw_quantity)
        if quantity is None or quantity <= 0:
            quantity = Decimal("1")
        unit_charge = None
        gross_line_total = None
        status = load_status
        if not normalized_code:
            status = "Missing CPT/HCPCS code"
        elif load_status == "Found" and code_column is not None and rate_column is not None:
            if normalized_code in known_cdm and normalized_code not in active_cdm:
                status = "Inactive CDM entry"
            else:
                matches = frame[frame[code_column].map(_normalize_code) == normalized_code]
                if matches.empty:
                    status = "Not found in chargemaster"
                else:
                    prices = {_decimal_or_none(value) for value in matches[rate_column].tolist()}
                    prices.discard(None)
                    if len(prices) > 1:
                        status = "Ambiguous rate"
                    elif not prices:
                        status = "Invalid rate"
                    else:
                        unit_charge = next(iter(prices))
                        if description_column is not None:
                            descriptions = [_clean_cell(value) for value in matches[description_column].tolist() if _clean_cell(value)]
                            if descriptions:
                                description = descriptions[0]
                        gross_line_total = (unit_charge * quantity).quantize(Decimal("0.01"))
                        status = "Found"
        elif load_status == "Found":
            status = "Missing required columns"

        output.append({
            "cpt_hcpcs": code,
            "description": description,
            "quantity": format(quantity, "f"),
            "unit_charge_usd": float(unit_charge) if unit_charge is not None else None,
            "gross_line_total_usd": float(gross_line_total) if gross_line_total is not None else None,
            "rate_source_status": status,
            "encounter_type": encounter_type,
            "chargemaster_sheet": source_sheet,
        })
    return output


def _parse_rate(value: str) -> Decimal | None:
    normalized = value.strip().replace("$", "").replace(",", "")
    if not normalized:
        return None
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _valid_quantity(value: str) -> bool:
    if not value:
        return True
    try:
        quantity = Decimal(value)
    except InvalidOperation:
        return False
    return quantity.is_finite() and quantity > 0


def _billing_row(
    date_value: str,
    code: str,
    description: str,
    quantity: str,
    rate: str,
    status: str,
) -> dict[str, str]:
    return dict(zip(_RESULT_FIELDS, (date_value, code, description, quantity, rate, status)))


def build_cpt_bill_rows(
    cpt_results: list[dict[str, Any]],
    encounter_date: str | None = None,
    encounter_type: str | None = None,
) -> list[dict[str, str]]:
    """Build presentation-only rate lookup rows for already-approved CPT results."""
    if not cpt_results:
        return []

    if encounter_type is not None:
        priced = price_cpt_bill_lines(cpt_results, encounter_type)
        rows = []
        for item in priced:
            row = _billing_row(
                str(encounter_date or "").strip() or "Not recorded",
                item["cpt_hcpcs"] or "Not specified",
                item["description"],
                item["quantity"],
                str(item["unit_charge_usd"]) if item["unit_charge_usd"] is not None else "Not available",
                item["rate_source_status"],
            )
            row["Gross Line Total"] = str(item["gross_line_total_usd"]) if item["gross_line_total_usd"] is not None else "Not included"
            rows.append(row)
        return rows

    chargemaster, load_status = load_chargemaster()
    duplicate_codes = set(
        chargemaster.loc[chargemaster["code"].duplicated(keep=False), "code"]
    )
    grouped = {
        code: group.iloc[0]
        for code, group in chargemaster.groupby("code", sort=False)
        if code not in duplicate_codes
    }
    default_date = str(encounter_date or "").strip() or "Not recorded"
    rows = []

    for result in cpt_results:
        raw_code = _clean_cell(result.get("CPT/HCPCS Code", ""))
        lookup_code = _normalize_code(raw_code)
        description = _clean_cell(result.get("Matched Procedure/Service", "")) or "Not specified"
        quantity = "Not specified"
        estimated_rate = "Not available"
        status = load_status

        if load_status == "Found":
            if lookup_code in duplicate_codes:
                status = "Duplicate code"
            elif lookup_code not in grouped:
                status = "Not found in chargemaster"
            else:
                charge = grouped[lookup_code]
                description = charge["description"] or description
                quantity = charge["quantity"] or "Not specified"
                amount = _parse_rate(charge["rate"])
                if amount is None:
                    status = "Invalid rate"
                elif charge["quantity"] and not _valid_quantity(charge["quantity"]):
                    status = "Invalid quantity"
                else:
                    estimated_rate = format(amount, "f")
                    status = "Found"

        rows.append(_billing_row(
            default_date,
            raw_code or "Not specified",
            description,
            quantity,
            estimated_rate,
            status,
        ))

    return rows
