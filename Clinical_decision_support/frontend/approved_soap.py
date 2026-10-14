import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_SECTIONS = ("SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN")


def missing_soap_sections(soap_note: str) -> list[str]:
    return [
        section
        for section in REQUIRED_SECTIONS
        if not re.search(rf"^\s*{section}\s*:", soap_note or "", flags=re.IGNORECASE | re.MULTILINE)
    ]


def _soap_note_path(encounter_id: str, storage_dir: Path) -> Path:
    safe_encounter_id = str(encounter_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", safe_encounter_id):
        raise ValueError("Encounter ID is not valid for an approved SOAP note filename.")
    return Path(storage_dir) / f"{safe_encounter_id}_soap.json"


def save_approved_soap_note(
    encounter_id: str,
    patient_id: str,
    soap_note: str,
    storage_dir: Path,
) -> dict[str, str]:
    missing_sections = missing_soap_sections(soap_note)
    if missing_sections:
        raise ValueError("SOAP note is missing required sections: " + ", ".join(missing_sections))

    payload = {
        "encounter_id": str(encounter_id),
        "patient_id": str(patient_id or ""),
        "status": "approved",
        "approved_timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "soap_note": soap_note,
    }
    path = _soap_note_path(encounter_id, storage_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=4, ensure_ascii=False)
    temporary_path.replace(path)
    return payload


def load_approved_soap_note(encounter_id: str, storage_dir: Path) -> dict[str, str] | None:
    try:
        path = _soap_note_path(encounter_id, storage_dir)
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    if (
        not isinstance(payload, dict)
        or payload.get("encounter_id") != str(encounter_id)
        or payload.get("status") != "approved"
        or not isinstance(payload.get("soap_note"), str)
    ):
        return None
    return payload


def delete_approved_soap_note(encounter_id: str, storage_dir: Path) -> bool:
    path = _soap_note_path(encounter_id, storage_dir)
    if not path.exists():
        return False
    path.unlink()
    return True


def unapprove_soap_record(record: dict[str, Any], storage_dir: Path) -> str:
    soap_note = str(
        record.get("approved_soap_note")
        or record.get("summary")
        or record.get("patient_summary")
        or ""
    )
    encounter_id = record.get("ehr_encounter_id") or record.get("id")
    delete_approved_soap_note(encounter_id, storage_dir)
    record["summary"] = soap_note
    record["status"] = "Pending"
    record.pop("approved_soap_note", None)
    record.pop("approved_timestamp", None)
    record.pop("approval_date", None)
    return soap_note