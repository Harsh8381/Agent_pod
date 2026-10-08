from frontend.admin_service import (
    DEFAULT_PERMISSIONS,
    MODULES,
    ROLES,
    add_admin_user,
    ensure_admin_state,
    save_role_permissions,
)
from frontend.admin_console import build_admin_workflow_rows


def test_admin_roles_have_permission_rows_for_each_module():
    assert set(ROLES) == {
        "Coding Specialist",
        "RCM Analyst",
        "Clinical Reviewer",
        "Operations Admin",
        "Super Admin",
    }
    assert all(set(DEFAULT_PERMISSIONS[role]) == set(MODULES) for role in ROLES)


def test_permissions_are_session_scoped_and_editable():
    state = {}
    ensure_admin_state(state)
    save_role_permissions(state, "Coding Specialist", [
        {"Module": "Codes", "View": True, "Edit": True, "Approve": False, "Admin": False},
    ])
    assert state["admin_role_permissions"]["Coding Specialist"]["Codes"]["Approve"] is False
    assert state["admin_role_permissions"]["Super Admin"]["Codes"]["Approve"] is True


def test_add_admin_user_rejects_duplicate_name():
    state = {}
    ensure_admin_state(state)
    add_admin_user(state, "Taylor Clinician", "taylor@example.test", "Clinical Reviewer")
    try:
        add_admin_user(state, "taylor clinician", "other@example.test", "RCM Analyst")
    except ValueError as error:
        assert "already listed" in str(error)
    else:
        raise AssertionError("Duplicate user names should be rejected.")


def test_admin_consultation_row_uses_name_from_linked_consultation():
    ehr_payload = {
        "sheets": {
            "Patient_Master": {"records": [{"Patient_ID": "P1001", "Legal_Name": "Chart Name", "MRN": "MRN-1"}]},
            "Encounters": {"records": [{"Patient_ID": "P1001", "Encounter_ID": "ENC-1", "Date": "2026-09-01", "Status": "Completed"}]},
        }
    }
    local_records = [{
        "id": "PT-1", "name": "Consultation Name", "ehr_patient_id": "P1001",
        "date": "02/09/2026", "time": "10:00", "status": "Pending", "doctor": "Dr. Reviewer",
    }]

    rows = build_admin_workflow_rows(local_records, ehr_payload, {})

    assert len(rows) == 1
    assert rows[0]["Patient"] == "Consultation Name"
    assert rows[0]["MRN"] == "MRN-1"
    assert rows[0]["_origin"] == "consultation"