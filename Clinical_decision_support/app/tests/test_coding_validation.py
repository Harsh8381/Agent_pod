from frontend.coding_validation import FAIL, PASS, WARN, missing_soap_sections, post_coding_checks, pre_coding_checks, summarize

SIGNED_NOTE = (
    "SUBJECTIVE:\nFever and cough for four days.\n"
    "OBJECTIVE:\nT 101.8 F, SpO2 91%.\n"
    "ASSESSMENT:\nCommunity-acquired pneumonia suspected; type 2 diabetes stable on metformin.\n"
    "PLAN:\nChest x-ray ordered. Continue metformin."
)
CHART = {
    "patient": {"Patient_ID": "P1"},
    "records": {"Insurance": [{"Priority": "Primary", "Payer_Name": "Athena Health Insurance", "Status": "Active"}]},
}


def _record(**overrides):
    record = {
        "id": "PT-1", "ehr_encounter_id": "ENC1", "ehr_patient_id": "P1", "date": "07/10/2026",
        "doctor": "Dr. Johnson", "status": "Approved", "summary": SIGNED_NOTE, "transcript": "Doctor: hello",
    }
    record.update(overrides)
    return record


def _status(checks, name):
    return next(check["status"] for check in checks if check["name"] == name)


def _row(code, kind="ICD-10-CM", status="Accepted", **extra):
    return {"key": f"{kind}:{code}", "type": kind, "code": code, "term": code, "description": extra.pop("description", code),
            "status": status, "hcc": extra.pop("hcc", ""), "excerpt": extra.pop("excerpt", ""), **extra}


def test_signed_complete_encounter_is_ready_for_coding():
    checks = pre_coding_checks(_record(), CHART, ["Fever"])
    assert summarize(checks)["status"] == PASS


def test_missing_soap_sections_block_coding():
    record = _record(summary="SUBJECTIVE:\nCough.\nPLAN:\nRest.")
    checks = pre_coding_checks(record, CHART, ["Cough"])
    assert _status(checks, "SOAP sections complete") == FAIL
    assert missing_soap_sections(record["summary"]) == ["Objective", "Assessment"]


def test_draft_note_and_unlinked_patient_are_warnings():
    checks = pre_coding_checks(_record(status="Pending", ehr_patient_id=None), {}, ["Fever"])
    assert _status(checks, "Clinical note signed") == WARN
    assert _status(checks, "Patient linked to EHR") == WARN
    assert _status(checks, "Coverage on file") == WARN


def test_unreachable_chart_and_missing_coverage_have_distinct_messages():
    offline = next(c for c in pre_coding_checks(_record(), {}, ["Fever"]) if c["name"] == "Coverage on file")
    uninsured = next(c for c in pre_coding_checks(_record(), {"patient": {"Patient_ID": "P1"}, "records": {}}, ["Fever"]) if c["name"] == "Coverage on file")
    assert offline["status"] == uninsured["status"] == WARN
    assert "unavailable" in offline["detail"] and "No insurance" in uninsured["detail"]


def test_missing_transcript_and_generic_provider_fail():
    checks = pre_coding_checks(_record(transcript="", doctor="Doctor"), CHART, ["Fever"])
    assert _status(checks, "Source documentation") == FAIL
    assert _status(checks, "Encounter identifiers") == FAIL


def test_clean_code_set_passes_post_coding():
    rows = [_row("J18.9"), _row("85025", kind="CPT", procedure_status="performed")]
    checks = post_coding_checks(rows, scrub={"errors": [], "warnings": []})
    assert summarize(checks)["status"] == PASS


def test_no_accepted_diagnosis_blocks_billing():
    rows = [_row("J18.9", status="Suggested"), _row("85025", kind="CPT", procedure_status="performed")]
    checks = post_coding_checks(rows, scrub={"errors": [], "warnings": []})
    assert _status(checks, "Primary diagnosis") == FAIL
    assert _status(checks, "Coder decisions") == WARN


def test_accepted_service_not_performed_fails():
    rows = [_row("J18.9"), _row("71045", kind="CPT", procedure_status="ordered_not_performed")]
    checks = post_coding_checks(rows, scrub={"errors": [], "warnings": []})
    assert _status(checks, "Accepted services performed") == FAIL


def test_accepted_superbill_service_counts_as_performed():
    rows = [_row("J18.9"), _row("99214", kind="CPT · E/M"), _row("85025", kind="CPT", procedure_status="superbill")]
    checks = post_coding_checks(rows, scrub={"errors": [], "warnings": []})
    assert _status(checks, "Accepted services performed") == PASS


def test_procedures_without_diagnosis_fail_linkage():
    rows = [_row("J18.9", status="Rejected"), _row("85025", kind="CPT", procedure_status="performed")]
    checks = post_coding_checks(rows, scrub={"errors": [], "warnings": []})
    assert _status(checks, "Procedure–diagnosis linkage") == FAIL


def test_symptom_with_definitive_and_unspecified_are_warnings():
    rows = [_row("J18.9", description="Pneumonia, unspecified organism"), _row("R50.9", description="Fever, unspecified")]
    checks = post_coding_checks(rows, scrub={"errors": [], "warnings": []})
    assert _status(checks, "Symptom codes with definitive diagnosis") == WARN
    assert _status(checks, "Diagnosis specificity") == WARN


def test_two_em_codes_fail():
    rows = [_row("J18.9"), _row("99213", kind="CPT · E/M"), _row("99214", kind="CPT · E/M")]
    checks = post_coding_checks(rows, scrub={"errors": [], "warnings": []})
    assert _status(checks, "E/M level") == FAIL


def test_hcc_without_meat_support_warns():
    rows = [_row("E11.9", hcc="HCC 38", excerpt="diabetes")]
    checks = post_coding_checks(rows, meat_signals=lambda text: {"M": False, "E": False, "A": True, "T": False},
                                scrub={"errors": [], "warnings": []})
    assert _status(checks, "HCC MEAT support") == WARN


def test_claim_scrub_errors_map_to_blocking_and_warnings():
    scrub = {
        "errors": [
            {"type": "invalid_cpt", "message": "CPT 00000 is not present."},
            {"type": "unpriced_cpt", "message": "CPT 82947 has no price."},
        ],
        "warnings": [{"type": "em_level_review", "message": "Review E/M level."}],
    }
    checks = post_coding_checks([_row("J18.9")], scrub=scrub)
    assert _status(checks, "Claim scrub · invalid cpt") == FAIL
    assert _status(checks, "Claim scrub · unpriced cpt") == WARN
    assert _status(checks, "Claim scrub · em level review") == WARN


def test_unavailable_scrubber_is_a_warning_not_a_block():
    checks = post_coding_checks([_row("J18.9")], scrub=None)
    assert _status(checks, "Claim scrub") == WARN
    assert summarize(checks)["status"] == WARN
