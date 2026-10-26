import json
from pathlib import Path

from frontend.claim_generation_page import _document_requirement
from frontend.supporting_documents import (
    available_documents_for_claim,
    attach_document_ids_to_claim,
    documents_for_claim,
    load_document_repository,
    validate_claim_documents,
)


def test_document_repository_scans_files_from_type_and_scope_folders(tmp_path):
    document_root = tmp_path / "supporting_documents"
    report_path = (
        document_root
        / "X-Ray Reports"
        / "PAT-1"
        / "ENC-1"
        / "report.pdf"
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(b"sample report")

    documents = load_document_repository(
        document_root=document_root,
        legacy_repository_path=None,
        approved_soap_path=None,
    )

    assert len(documents) == 1
    assert documents[0]["document_type"] == "X-Ray Report"
    assert documents[0]["patient_id"] == "PAT-1"
    assert documents[0]["encounter_id"] == "ENC-1"
    assert Path(documents[0]["file_path"]) == report_path
    assert documents[0]["size_bytes"] == len(b"sample report")
    assert (document_root / "SOAP Notes").is_dir()
    assert (document_root / "CT Scan Reports").is_dir()
    assert (document_root / "Lab Reports").is_dir()
    assert (document_root / "Procedure Notes").is_dir()


def test_repository_recognizes_document_type_after_patient_and_encounter_folders(
    tmp_path,
):
    document_root = tmp_path / "supporting_documents"
    report_path = (
        document_root
        / "PAT-1"
        / "ENC-1"
        / "CT Reports"
        / "report.pdf"
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(b"sample CT report")

    documents = load_document_repository(
        document_root=document_root,
        legacy_repository_path=None,
        approved_soap_path=None,
    )

    assert len(documents) == 1
    assert documents[0]["document_type"] == "CT Scan Report"
    assert documents[0]["patient_id"] == "PAT-1"
    assert documents[0]["encounter_id"] == "ENC-1"


def test_documents_for_claim_only_returns_matching_or_unscoped_documents():
    claim = {
        "Encounter ID": "ENC-1",
        "Patient Information": {"Patient ID": "PAT-1"},
    }
    documents = [
        {"document_id": "DOC-MATCH", "patient_id": "PAT-1", "encounter_id": "ENC-1"},
        {"document_id": "DOC-OTHER", "patient_id": "PAT-2", "encounter_id": "ENC-2"},
        {"document_id": "DOC-GLOBAL"},
    ]

    selected = documents_for_claim(claim, documents)

    assert [document["document_id"] for document in selected] == [
        "DOC-MATCH",
        "DOC-GLOBAL",
    ]


def test_document_ids_are_attached_to_claim_for_its_patient_and_encounter():
    claim = {
        "Encounter ID": "ENC-1",
        "Patient Information": {"Patient ID": "PAT-1"},
    }
    documents = [
        {
            "document_id": "DOC-MATCH",
            "patient_id": "PAT-1",
            "encounter_id": "ENC-1",
        },
        {
            "document_id": "DOC-OTHER",
            "patient_id": "PAT-2",
            "encounter_id": "ENC-2",
        },
    ]

    attached_ids = attach_document_ids_to_claim(claim, documents)

    assert attached_ids == ["DOC-MATCH"]
    assert claim["Attached Supporting Document IDs"] == ["DOC-MATCH"]


def test_claim_document_validation_checks_attached_document_files_by_cpt(tmp_path):
    document_root = tmp_path / "supporting_documents"
    xray_path = document_root / "X-Ray Reports" / "PAT-1" / "ENC-1" / "report.pdf"
    lab_path = document_root / "Lab Reports" / "PAT-1" / "ENC-1" / "results.pdf"
    unrelated_ct_path = (
        document_root / "CT Scan Reports" / "PAT-2" / "ENC-2" / "ct.pdf"
    )
    for path in (xray_path, lab_path, unrelated_ct_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"document")
    documents = load_document_repository(
        document_root=document_root,
        legacy_repository_path=None,
        approved_soap_path=None,
    )
    claim = {
        "Encounter ID": "ENC-1",
        "Patient Information": {"Patient ID": "PAT-1"},
        "Attached Supporting Document IDs": [
            document["document_id"] for document in documents
        ],
        "Charges": [
            {"CPT/HCPCS Codes": ["71045"], "Service Description": "Chest X-ray"},
            {"CPT/HCPCS Codes": ["70450"], "Service Description": "CT Scan"},
            {"CPT/HCPCS Codes": ["80053"], "Service Description": "Lab panel"},
        ],
    }

    results = validate_claim_documents(claim, documents, _document_requirement)

    assert [row["Status"] for row in results] == [
        "✅ Document Available",
        "⚠ Missing Supporting Document",
        "✅ Document Available",
    ]
    assert results[0]["Document IDs"]
    assert results[1]["Document IDs"] == []
    assert results[2]["Document IDs"]
    assert [row["Required Document"] for row in results] == [
        "X-Ray Report",
        "CT Report",
        "Lab Report",
    ]


def test_files_added_after_claim_generation_are_found_when_claim_is_validated(
    tmp_path,
):
    document_root = tmp_path / "supporting_documents"
    ct_path = document_root / "CT Scan Reports" / "CT_Scan_Report.pdf"
    xray_path = document_root / "X-Ray Reports" / "XRay_Report.pdf"
    for path in (ct_path, xray_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"report")
    documents = load_document_repository(
        document_root=document_root,
        legacy_repository_path=None,
        approved_soap_path=None,
    )
    claim = {
        "Encounter ID": "ENC-1",
        "Patient Information": {"Patient ID": "PAT-1"},
        "Attached Supporting Document IDs": [],
        "Charges": [
            {"CPT/HCPCS Codes": ["70450"], "Service Description": "CT Scan"},
            {"CPT/HCPCS Codes": ["71045"], "Service Description": "Chest X-ray"},
        ],
    }

    attach_document_ids_to_claim(claim, documents)
    results = validate_claim_documents(claim, documents, _document_requirement)

    assert [row["Status"] for row in results] == [
        "✅ Document Available",
        "✅ Document Available",
    ]
    assert all(row["Document IDs"] for row in results)


def test_validation_inventory_shows_folder_documents_even_when_not_billed(tmp_path):
    document_root = tmp_path / "supporting_documents"
    report_paths = [
        document_root / "CT Scan Reports" / "CT_Scan_Report.pdf",
        document_root / "X-Ray Reports" / "XRay_Report.pdf",
        document_root / "SOAP Notes" / "SOAP_Note.pdf",
    ]
    for path in report_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"report")
    documents = load_document_repository(
        document_root=document_root,
        legacy_repository_path=None,
        approved_soap_path=None,
    )
    claim = {
        "Encounter ID": "ENC-1",
        "Patient Information": {"Patient ID": "PAT-1"},
        "Charges": [
            {"CPT/HCPCS Codes": ["99213"], "Service Description": "Office visit"}
        ],
    }

    results = available_documents_for_claim(claim, documents)

    assert {row["Document Type"] for row in results} == {
        "CT Scan Report",
        "X-Ray Report",
        "SOAP Note",
    }
    assert all(row["Result"] == "✅ Document Available" for row in results)


def test_approved_soap_notes_are_copied_into_the_document_folder(tmp_path):
    document_root = tmp_path / "supporting_documents"
    soap_directory = tmp_path / "approved_soap_notes"
    soap_directory.mkdir()
    (soap_directory / "ENC-1_soap.json").write_text(
        json.dumps({
            "encounter_id": "ENC-1",
            "patient_id": "PAT-1",
            "status": "approved",
            "soap_note": "Assessment and plan.",
        }),
        encoding="utf-8",
    )

    documents = load_document_repository(
        document_root=document_root,
        legacy_repository_path=None,
        approved_soap_path=soap_directory,
    )

    assert len(documents) == 1
    assert documents[0]["document_id"] == "DOC-SOAP-ENC-1"
    assert documents[0]["document_type"] == "SOAP Note"
    assert documents[0]["patient_id"] == "PAT-1"
    assert documents[0]["encounter_id"] == "ENC-1"
    assert Path(documents[0]["file_path"]).read_text(encoding="utf-8") == (
        "Assessment and plan."
    )
    assert not (tmp_path / "supporting_documents.json").exists()


def test_legacy_json_documents_are_migrated_to_files_and_read_from_folder(tmp_path):
    document_root = tmp_path / "supporting_documents"
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps([{
            "document_id": "DOC-XRAY-1",
            "document_type": "X-Ray Report",
            "file_name": "report.pdf",
            "patient_id": "PAT-1",
            "encounter_id": "ENC-1",
            "content_base64": "c2FtcGxl",
        }]),
        encoding="utf-8",
    )

    documents = load_document_repository(
        document_root=document_root,
        legacy_repository_path=legacy_path,
        approved_soap_path=None,
    )

    assert len(documents) == 1
    assert documents[0]["document_id"] == "DOC-XRAY-1"
    assert Path(documents[0]["file_path"]).read_bytes() == b"sample"
    assert legacy_path.exists()
