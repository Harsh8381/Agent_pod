from decimal import Decimal

from frontend.claim_generation_page import build_claim_record
from frontend.claim_validator import validate_claim


def _claim():
    return {
        "Claim ID": "CLM-123",
        "Patient Information": {
            "Patient Name": "Demo Patient",
            "DOB": "1980-01-02",
        },
        "Provider Information": {
            "Name": "Demo Provider",
            "NPI": "1234567890",
        },
        "ICD-10 Codes": ["R05.9"],
        "CPT/HCPCS Codes": ["71045"],
        "Charges": [{"Date of Service": "2026-10-15"}],
        "Claim Amount": 113.0,
        "Supporting Documents Validation": {
            "Results": [{"CPT Code": "71045", "Status": "Present"}],
        },
    }


def test_validate_claim_passes_when_required_fields_are_present():
    report = validate_claim(_claim())

    expected = {
        "Claim ID": "CLM-123",
        "Validation Score": 100,
        "Status": "PASS",
        "Errors": [],
        "Warnings": [],
    }
    assert {key: report[key] for key in expected} == expected
    assert report["Checks"]


def test_generated_claim_includes_validation_fields():
    bill = {
        "encounter_id": "ENC-123",
        "encounter_type": "OPD",
        "patient": {
            "Patient ID": "P-123",
            "Patient Name": "Demo Patient",
            "DOB": "1980-01-02",
            "Primary ICD-10-CM": "R05.9",
            "Provider Name": "Demo Provider",
            "Provider NPI": "1234567890",
        },
        "lines": [{
            "Service Date": "2026-10-15",
            "CPT / HCPCS": "71045",
            "Line Charge ($)": Decimal("113.00"),
        }],
        "gross": Decimal("113.00"),
    }

    claim = build_claim_record(
        bill,
        "OPD",
        supporting_documents={
            "Overall Status": "Complete",
            "Results": [{"CPT Code": "71045", "Status": "Present"}],
        },
    )
    report = validate_claim(claim)

    assert claim["Provider Information"] == {
        "Name": "Demo Provider",
        "NPI": "1234567890",
    }
    assert claim["Charges"][0]["Date of Service"] == "2026-10-15"
    assert report["Status"] == "PASS"
    assert report["Validation Score"] == 100


def test_validate_claim_reports_missing_required_fields_and_scores_errors():
    claim = _claim()
    claim["Patient Information"]["Patient Name"] = ""
    claim["Patient Information"]["DOB"] = ""
    claim["Provider Information"]["Name"] = ""
    claim["Provider Information"]["NPI"] = ""
    claim["Charges"] = []
    claim["ICD-10 Codes"] = []
    claim["CPT/HCPCS Codes"] = []
    claim["Claim Amount"] = 0

    report = validate_claim(claim)

    assert report["Status"] == "FAIL"
    assert report["Validation Score"] == 0
    assert len(report["Errors"]) == 8
    assert report["Warnings"] == []


def test_validate_claim_warns_for_nonstandard_npi_and_invalid_date():
    claim = _claim()
    claim["Provider Information"]["NPI"] = "DEMO-NPI"
    claim["Patient Information"]["DOB"] = "not-a-date"

    report = validate_claim(claim)

    assert report["Status"] == "PASS"
    assert report["Validation Score"] == 90
    assert len(report["Warnings"]) == 2
    assert report["Errors"] == []


def test_validate_claim_checks_documentation_ncci_mue_and_sample_lcd_rules():
    claim = _claim()
    claim["ICD-10 Codes"] = ["R05.9"]
    claim["CPT/HCPCS Codes"] = ["70450", "99213", "99214"]
    claim["Charges"] = [
        {"CPT/HCPCS Codes": ["70450"], "Quantity": 1, "Date of Service": "2026-10-15"},
        {"CPT/HCPCS Codes": ["99213"], "Quantity": 2, "Date of Service": "2026-10-15"},
        {"CPT/HCPCS Codes": ["99214"], "Quantity": 1, "Date of Service": "2026-10-15"},
    ]
    claim["Supporting Documents Validation"] = {
        "Results": [
            {"CPT Code": code, "Status": "Present"}
            for code in claim["CPT/HCPCS Codes"]
        ],
    }

    report = validate_claim(claim)

    assert any("Potential NCCI conflict" in warning for warning in report["Warnings"])
    assert any("local sample MUE limit" in warning for warning in report["Warnings"])
    assert any("sample LCD requirements" in warning for warning in report["Warnings"])
    assert not any("Required supporting document not found." in warning for warning in report["Warnings"])
    assert {
        check["Category"] for check in report["Checks"]
    } >= {
        "Required Field Validation",
        "Documentation Validation",
        "NCCI Validation",
        "MUE Validation",
        "LCD Validation",
    }


def test_validate_claim_warns_when_supporting_document_is_missing():
    claim = _claim()
    claim["Supporting Documents Validation"] = {"Results": []}

    report = validate_claim(claim)

    assert "Required supporting document not found." in report["Warnings"]
    assert any(
        check["Category"] == "Documentation Validation"
        and check["Status"] == "WARNING"
        for check in report["Checks"]
    )


def test_validate_claim_uses_repository_document_status_messages():
    claim = _claim()

    available_report = validate_claim(claim, document_results=[{
        "CPT Code": "71045",
        "Status": "✅ Document Available",
    }])
    missing_report = validate_claim(claim, document_results=[{
        "CPT Code": "71045",
        "Status": "⚠ Missing Supporting Document",
    }])

    available_check = next(
        check for check in available_report["Checks"]
        if check["Category"] == "Documentation Validation"
    )
    missing_check = next(
        check for check in missing_report["Checks"]
        if check["Category"] == "Documentation Validation"
    )
    assert available_check["Message"] == "✅ Document Available"
    assert available_check["Status"] == "PASS"
    assert missing_check["Message"] == "⚠ Missing Supporting Document"
    assert missing_check["Status"] == "WARNING"
