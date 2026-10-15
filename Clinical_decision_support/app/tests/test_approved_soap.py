import json

import pytest

from frontend.approved_soap import (
    delete_approved_soap_note,
    load_approved_soap_note,
    missing_soap_sections,
    save_approved_soap_note,
    unapprove_soap_record,
)


SOAP_NOTE = (
    "SUBJECTIVE:\nPatient reports a cough.\n\n"
    "OBJECTIVE:\nTemperature is 37 C.\n\n"
    "ASSESSMENT:\nAcute cough.\n\n"
    "PLAN:\nFollow up in one week.\n"
)


def test_save_and_load_approved_soap_note_preserves_exact_text(tmp_path):
    payload = save_approved_soap_note("ENC001", "P001", SOAP_NOTE, tmp_path)

    saved_path = tmp_path / "ENC001_soap.json"
    saved_json = json.loads(saved_path.read_text(encoding="utf-8"))
    loaded_payload = load_approved_soap_note("ENC001", tmp_path)

    assert payload["soap_note"] == SOAP_NOTE
    assert saved_json == payload
    assert loaded_payload == payload
    assert payload["status"] == "approved"
    assert payload["encounter_id"] == "ENC001"
    assert payload["patient_id"] == "P001"
    assert payload["approved_timestamp"]


def test_save_rejects_soap_note_missing_required_sections(tmp_path):
    with pytest.raises(ValueError, match="OBJECTIVE"):
        save_approved_soap_note("ENC001", "P001", "SUBJECTIVE:\nOnly subjective.", tmp_path)

    assert not (tmp_path / "ENC001_soap.json").exists()


def test_missing_soap_sections_requires_section_headings():
    note = "SUBJECTIVE:\nOBJECTIVE:\nASSESSMENT:\n"

    assert missing_soap_sections(note) == ["PLAN"]


def test_delete_approved_soap_note_removes_only_target_file(tmp_path):
    save_approved_soap_note("ENC001", "P001", SOAP_NOTE, tmp_path)
    save_approved_soap_note("ENC002", "P002", SOAP_NOTE, tmp_path)

    assert delete_approved_soap_note("ENC001", tmp_path)
    assert load_approved_soap_note("ENC001", tmp_path) is None
    assert load_approved_soap_note("ENC002", tmp_path) is not None
    assert not delete_approved_soap_note("ENC001", tmp_path)


def test_unapprove_restores_note_and_clears_approval_state(tmp_path):
    save_approved_soap_note("ENC001", "P001", SOAP_NOTE, tmp_path)
    record = {
        "id": "ENC001",
        "status": "Approved",
        "summary": "short summary",
        "approved_soap_note": SOAP_NOTE,
        "approved_timestamp": "2026-10-11T00:00:00+00:00",
        "approval_date": "11/10/2026",
    }

    restored_note = unapprove_soap_record(record, tmp_path)

    assert restored_note == SOAP_NOTE
    assert record["summary"] == SOAP_NOTE
    assert record["status"] == "Pending"
    assert "approved_soap_note" not in record
    assert "approved_timestamp" not in record
    assert "approval_date" not in record
    assert load_approved_soap_note("ENC001", tmp_path) is None