from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

DOCUMENT_FOLDERS = {
    "SOAP Notes": "SOAP Note",
    "CT Scan Reports": "CT Scan Report",
    "X-Ray Reports": "X-Ray Report",
    "Lab Reports": "Lab Report",
    "Procedure Notes": "Procedure Note",
}
DOCUMENT_FOLDER_ALIASES = {
    "soap note": "SOAP Note",
    "soap notes": "SOAP Note",
    "ct report": "CT Scan Report",
    "ct reports": "CT Scan Report",
    "ct scan report": "CT Scan Report",
    "ct scan reports": "CT Scan Report",
    "x-ray report": "X-Ray Report",
    "x-ray reports": "X-Ray Report",
    "x ray report": "X-Ray Report",
    "x ray reports": "X-Ray Report",
    "xray report": "X-Ray Report",
    "xray reports": "X-Ray Report",
    "lab report": "Lab Report",
    "lab reports": "Lab Report",
    "procedure note": "Procedure Note",
    "procedure notes": "Procedure Note",
}

DEFAULT_DOCUMENT_ROOT = Path(__file__).resolve().parents[1] / "data" / "supporting_documents"
DEFAULT_LEGACY_REPOSITORY = Path(__file__).resolve().parents[1] / "data" / "supporting_documents.json"
DEFAULT_APPROVED_SOAP_PATH = Path(__file__).resolve().parents[1] / "data" / "approved_soap_notes"


def _safe_scope(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text == "_shared":
        return "_shared"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def _document_type(path: Path, document_root: Path) -> str | None:
    relative_parts = path.relative_to(document_root).parts
    if not relative_parts:
        return None
    for part in relative_parts[:-1]:
        document_type = DOCUMENT_FOLDER_ALIASES.get(part.casefold())
        if document_type:
            return document_type

    filename = path.stem.casefold().replace("_", " ").replace("-", " ")
    if "soap" in filename or "clinical note" in filename:
        return "SOAP Note"
    if re.search(r"\bct\b", filename) or "computed tomography" in filename:
        return "CT Scan Report"
    if any(term in filename for term in ("x ray", "xray", "radiograph")):
        return "X-Ray Report"
    if any(term in filename for term in ("lab", "laboratory")):
        return "Lab Report"
    if any(term in filename for term in ("procedure", "operative", "surgery")):
        return "Procedure Note"
    return None


def _copy_legacy_documents(
    document_root: Path,
    legacy_repository_path: Path | None,
    approved_soap_path: Path | None,
) -> None:
    known_ids = {
        match.group(1)
        for path in document_root.rglob("*")
        if path.is_file()
        for match in [re.match(r"(DOC-[A-Za-z0-9-]+)__", path.name)]
        if match is not None
    }

    legacy_documents = []
    if legacy_repository_path is not None and legacy_repository_path.exists():
        payload = json.loads(legacy_repository_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise ValueError("Legacy supporting document JSON must contain a list of documents.")
        legacy_documents = payload

    for document in legacy_documents:
        document_id = str(document.get("document_id") or "").strip()
        document_type = str(document.get("document_type") or "").strip()
        encoded_content = str(document.get("content_base64") or "").strip()
        folder_name = next(
            (
                folder
                for folder, label in DOCUMENT_FOLDERS.items()
                if label == document_type
            ),
            None,
        )
        if (
            not document_id
            or document_id in known_ids
            or folder_name is None
            or not encoded_content
        ):
            continue
        content = base64.b64decode(encoded_content, validate=True)
        patient_dir = _safe_scope(document.get("patient_id"))
        encounter_dir = _safe_scope(document.get("encounter_id"))
        filename = Path(str(document.get("file_name") or "document.bin")).name
        destination_dir = document_root / folder_name / patient_dir / encounter_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        (destination_dir / f"{document_id}__{filename}").write_bytes(content)
        known_ids.add(document_id)

    if approved_soap_path is None or not approved_soap_path.exists():
        return
    for path in approved_soap_path.glob("*_soap.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "approved"
            or not str(payload.get("encounter_id") or "").strip()
            or not str(payload.get("soap_note") or "").strip()
        ):
            continue
        encounter_id = str(payload["encounter_id"]).strip()
        document_id = f"DOC-SOAP-{encounter_id}"
        if document_id in known_ids:
            continue
        patient_dir = _safe_scope(payload.get("patient_id"))
        encounter_dir = _safe_scope(encounter_id)
        destination_dir = document_root / "SOAP Notes" / patient_dir / encounter_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        (destination_dir / f"{document_id}__{path.stem}.txt").write_text(
            str(payload["soap_note"]),
            encoding="utf-8",
        )
        known_ids.add(document_id)


def load_document_repository(
    document_root: Path = DEFAULT_DOCUMENT_ROOT,
    legacy_repository_path: Path | None = DEFAULT_LEGACY_REPOSITORY,
    approved_soap_path: Path | None = DEFAULT_APPROVED_SOAP_PATH,
) -> list[dict[str, Any]]:
    document_root.mkdir(parents=True, exist_ok=True)
    for folder_name in DOCUMENT_FOLDERS:
        (document_root / folder_name).mkdir(parents=True, exist_ok=True)
    _copy_legacy_documents(
        document_root,
        legacy_repository_path,
        approved_soap_path,
    )

    documents = []
    for path in sorted(document_root.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep" or path.stat().st_size == 0:
            continue
        document_type = _document_type(path, document_root)
        if document_type is None:
            continue
        relative_path = path.relative_to(document_root)
        parts = relative_path.parts
        scope_parts = [
            part for part in parts[:-1]
            if part.casefold() not in DOCUMENT_FOLDER_ALIASES
        ]
        patient_id = scope_parts[0] if scope_parts else ""
        encounter_id = scope_parts[1] if len(scope_parts) > 1 else ""
        patient_id = "" if patient_id == "_shared" else patient_id
        encounter_id = "" if encounter_id == "_shared" else encounter_id
        id_match = re.match(r"(DOC-[A-Za-z0-9-]+)__", path.name)
        document_id = (
            id_match.group(1)
            if id_match
            else f"DOC-{hashlib.sha256(str(relative_path).encode('utf-8')).hexdigest()[:12].upper()}"
        )
        documents.append({
            "document_id": document_id,
            "document_type": document_type,
            "file_name": path.name,
            "file_path": str(path.resolve()),
            "relative_path": str(relative_path),
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "size_bytes": path.stat().st_size,
        })
    return documents


def documents_for_claim(
    claim: dict[str, Any],
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    patient = claim.get("Patient Information")
    patient = patient if isinstance(patient, dict) else {}
    patient_id = str(patient.get("Patient ID") or "").strip()
    encounter_id = str(claim.get("Encounter ID") or "").strip()

    return [
        document for document in documents
        if isinstance(document, dict)
        and (
            not str(document.get("patient_id") or "").strip()
            or str(document.get("patient_id")).strip() == patient_id
        )
        and (
            not str(document.get("encounter_id") or "").strip()
            or str(document.get("encounter_id")).strip() == encounter_id
        )
    ]


def attach_document_ids_to_claim(
    claim: dict[str, Any],
    documents: list[dict[str, Any]],
) -> list[str]:
    document_ids = [
        str(document.get("document_id")).strip()
        for document in documents_for_claim(claim, documents)
        if str(document.get("document_id") or "").strip()
    ]
    claim["Attached Supporting Document IDs"] = document_ids
    return document_ids


def available_documents_for_claim(
    claim: dict[str, Any],
    documents: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {
            "Document ID": str(document.get("document_id") or ""),
            "Document Type": str(document.get("document_type") or ""),
            "File Name": str(document.get("file_name") or ""),
            "Result": "✅ Document Available",
        }
        for document in documents_for_claim(claim, documents)
        if Path(str(document.get("file_path") or "")).is_file()
    ]


def validate_claim_documents(
    claim: dict[str, Any],
    documents: list[dict[str, Any]],
    document_requirement: Callable[[str, dict[str, Any]], tuple[str, str]],
) -> list[dict[str, Any]]:
    attached_document_ids = claim.get("Attached Supporting Document IDs", [])
    if not isinstance(attached_document_ids, (list, tuple, set)):
        attached_document_ids = []
    attached_ids = {
        str(document_id).strip()
        for document_id in attached_document_ids
        if str(document_id).strip()
    }
    scoped_documents = documents_for_claim(claim, documents)
    attached_documents = [
        document for document in scoped_documents
        if isinstance(document, dict)
        and str(document.get("document_id") or "").strip() in attached_ids
        and Path(str(document.get("file_path") or "")).is_file()
    ]
    result_rows = []
    charges = claim.get("Charges")
    for charge in charges if isinstance(charges, list) else []:
        if not isinstance(charge, dict):
            continue
        codes = charge.get("CPT/HCPCS Codes", [])
        if not isinstance(codes, (list, tuple, set)):
            codes = re.split(r"[;,\n]+", str(codes or ""))
        for raw_code in codes:
            code = str(raw_code).strip()
            if not code:
                continue
            procedure, required_document = document_requirement(
                code,
                {"Service Description": charge.get("Service Description", "")},
            )
            repository_type = {
                "CT Report": "CT Scan Report",
                "X-Ray Report": "X-Ray Report",
                "Lab Result": "Lab Report",
                "SOAP Note": "SOAP Note",
                "Procedure Note": "Procedure Note",
            }.get(required_document, required_document)
            required_label = {
                "CT Report": "CT Report",
                "Lab Result": "Lab Report",
            }.get(required_document, required_document)
            matching_documents = [
                document for document in attached_documents
                if document.get("document_type") == repository_type
            ]
            result_rows.append({
                "CPT Code": code,
                "Procedure": procedure,
                "Required Document": required_label,
                "Status": (
                    "✅ Document Available"
                    if matching_documents
                    else "⚠ Missing Supporting Document"
                ),
                "Document IDs": [
                    str(document.get("document_id"))
                    for document in matching_documents
                ],
            })
    return result_rows
