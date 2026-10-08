from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]

_CODE_ALIASES = {
    "cpthcpcscode",
    "cptcode",
    "hcpcscode",
    "procedurecode",
    "code",
}
_RATE_ALIASES = {
    "rate",
    "charge",
    "estimatedrate",
    "standardcharge",
    "grosscharge",
}
_DESCRIPTION_ALIASES = {
    "proceduredescription",
    "description",
    "servicedescription",
}
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


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["code", "rate", "description", "quantity"])


def _configured_path(path: str | Path | None = None) -> Path | None:
    if path is None:
        load_dotenv(PROJECT_ROOT / ".env")
        configured = os.getenv("CHARGEMASTER_PATH", "").strip()
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
    if suffix not in {".csv", ".xlsx"}:
        return _empty_frame(), "Unsupported format"
    if not source.is_file():
        return _empty_frame(), "Chargemaster unavailable"

    try:
        if suffix == ".csv":
            frame = pd.read_csv(source, dtype=str, keep_default_na=False)
        else:
            frame = pd.read_excel(source, dtype=str, keep_default_na=False, engine="openpyxl")
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
) -> list[dict[str, str]]:
    """Build presentation-only rate lookup rows for already-approved CPT results."""
    if not cpt_results:
        return []

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
