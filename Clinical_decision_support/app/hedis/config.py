"""Static configuration: payer contracts and file specs, practitioner roster, evidence-source classes, paths."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from app.vbc_poc.catalog import MEASURES, PERIOD

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STATE_ENV = "HEDIS_STATE"
OUTBOX_ENV = "HEDIS_OUTBOX"
DEFAULT_STATE = DATA_DIR / "hedis_state.json"
DEFAULT_OUTBOX = DATA_DIR / "hedis_outbox"
ENGINE_VERSION = "HEDIS-ENGINE-1.0"


def state_path() -> Path:
    return Path(os.environ.get(STATE_ENV) or DEFAULT_STATE)


def outbox_dir() -> Path:
    return Path(os.environ.get(OUTBOX_ENV) or DEFAULT_OUTBOX)


# ---------------------------------------------------------------------------
# Evidence-source classes (NCQA terminology)
# ---------------------------------------------------------------------------
ADMINISTRATIVE = "ADMINISTRATIVE"  # claims / encounter data the payer already holds
STANDARD_SUPPLEMENTAL = "STANDARD_SUPPLEMENTAL"  # electronic feeds with a stable, auditable process
NON_STANDARD_SUPPLEMENTAL = "NON_STANDARD_SUPPLEMENTAL"  # manual / document-based; needs primary-source verification
NOT_REPORTABLE = "NOT_REPORTABLE"  # never counts (free text)

SOURCE_CLASS = {
    "claim": ADMINISTRATIVE,
    "lab": STANDARD_SUPPLEMENTAL,
    "ehr": STANDARD_SUPPLEMENTAL,
    "patient_device": STANDARD_SUPPLEMENTAL,
    "external_report": NON_STANDARD_SUPPLEMENTAL,
    "workflow_upload": NON_STANDARD_SUPPLEMENTAL,
    "narrative": NOT_REPORTABLE,
}
SOURCE_CLASS_LABELS = {
    ADMINISTRATIVE: "Administrative (claims)",
    STANDARD_SUPPLEMENTAL: "Standard supplemental",
    NON_STANDARD_SUPPLEMENTAL: "Non-standard supplemental",
    NOT_REPORTABLE: "Not reportable",
}
# Preference when several accepted records support one numerator: what the payer already holds wins.
SOURCE_PRIORITY = (ADMINISTRATIVE, STANDARD_SUPPLEMENTAL, NON_STANDARD_SUPPLEMENTAL)
SUPPLEMENTAL_CLASSES = (STANDARD_SUPPLEMENTAL, NON_STANDARD_SUPPLEMENTAL)


# ---------------------------------------------------------------------------
# Practitioners (IS 3.0): NPIs carry a valid Luhn check digit
# ---------------------------------------------------------------------------
def npi_check_digit(base9: str) -> str:
    """Luhn check digit for an NPI: computed over the '80840' card-issuer prefix plus the first nine digits."""
    digits = [int(d) for d in "80840" + base9]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 0:
            digit *= 2
            digit = digit - 9 if digit > 9 else digit
        total += digit
    return str((10 - total % 10) % 10)


def is_valid_npi(npi: str | None) -> bool:
    npi = str(npi or "")
    return len(npi) == 10 and npi.isdigit() and npi[0] in "12" and npi_check_digit(npi[:9]) == npi[9]


def _npi(base9: str) -> str:
    return base9 + npi_check_digit(base9)


PRACTITIONERS = {
    "Dr. A. Rivera": {"npi": _npi("123456789"), "tin": "84-1234567", "group_npi": _npi("198765432"), "specialty": "Family Medicine"},
    "Dr. S. Bennett": {"npi": _npi("112233445"), "tin": "84-1234567", "group_npi": _npi("198765432"), "specialty": "Internal Medicine"},
}


# ---------------------------------------------------------------------------
# Payers: contract, supplemental file spec, acknowledgement spec
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PayerSpec:
    key: str
    name: str
    product: str
    contract_id: str
    supplemental_format: str  # PIPE-v2 | CSV-v1
    allowed_code_systems: tuple
    max_records_per_batch: int
    ack_sla_days: int
    submitter_id: str
    member_prefix: str
    # Payer roster rule: rendering NPI must be on the payer's roster; otherwise the group NPI is required.
    roster_npis: tuple


PAYERS = {
    "ATH": PayerSpec("ATH", "Athena Health Insurance", "Medicare Advantage", "H9001-ACO", "PIPE-v2",
                     ("LOINC", "CPT", "HCPCS", "ICD-10-CM"), 500, 10, "NUUCARE-ATH-01", "ATH",
                     (PRACTITIONERS["Dr. A. Rivera"]["npi"], PRACTITIONERS["Dr. A. Rivera"]["group_npi"])),
    "CGH": PayerSpec("CGH", "Care Gap Health", "Commercial PPO", "CGH-VBC-77", "CSV-v1",
                     ("LOINC", "CPT", "HCPCS"), 250, 14, "NUUCARE-CGH-01", "CGH",
                     (PRACTITIONERS["Dr. S. Bennett"]["npi"], PRACTITIONERS["Dr. A. Rivera"]["group_npi"])),
}

# Measures whose numerator needs a result value in the supplemental record (outcome measures).
VALUE_REQUIRED = {"GSD-P": ("value",), "BPD-P": ("value", "value2")}
MEASURE_IDS = tuple(MEASURES)


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def payer_for(patient_id: str) -> str:
    """Deterministic payer assignment for the synthetic population (stands in for the eligibility feed)."""
    return "ATH" if int(_digest("payer", patient_id)[:8], 16) % 2 == 0 else "CGH"


def member_id_for(patient_id: str) -> str:
    payer = PAYERS[payer_for(patient_id)]
    return f"{payer.member_prefix}{int(_digest('member', patient_id)[:10], 16) % 10**9:09d}"


__all__ = ["PERIOD"]
