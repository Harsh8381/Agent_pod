from decimal import Decimal

import pytest

from frontend.claim_generation_page import (
    _claim_text,
    _find_claim_for_encounter,
    _split_opd_bill,
    build_ambient_bill,
    build_claim_record,
    determine_claim_type,
)


def test_build_ambient_bill_reuses_codes_charges_and_encounter_data():
    record = {
        "id": "PT-123",
        "ehr_patient_id": "P-123",
        "ehr_encounter_id": "ENC-123",
        "name": "Demo Patient",
        "date": "15/10/2026",
    }
    estimate = {
        "encounter_type": "OPD",
        "total": 113.0,
        "rows": [{
            "CPT/HCPCS Code": "71045",
            "Service Description": "Chest X-ray, one view",
            "Quantity": 1,
            "Unit Charge": "$113.00",
            "Gross Charge": "$113.00",
        }],
    }
    code_results = {
        "icd10": [
            {"ICD-10 Code": "R05.9"},
            {"ICD-10 Code": "I10"},
        ],
    }

    bill = build_ambient_bill(record, estimate, code_results)

    assert bill["encounter_id"] == "ENC-123"
    assert bill["encounter_type"] == "OPD"
    assert bill["patient"]["Patient ID"] == "P-123"
    assert bill["patient"]["Primary ICD-10-CM"] == "R05.9"
    assert bill["patient"]["Secondary ICD-10-CM"] == "I10"
    assert bill["lines"][0]["Line Charge ($)"] == Decimal("113.00")
    assert bill["gross"] == Decimal("113.00")


@pytest.mark.parametrize(
    ("encounter_type", "source", "expected"),
    [
        ("OPD", "OPD", "837P"),
        ("Outpatient", "", "837P"),
        ("IPD", "IPD", "837I"),
        ("Inpatient", "", "837I"),
        ("", "hospital", "837I"),
    ],
)
def test_claim_type_is_determined_from_encounter_type(encounter_type, source, expected):
    assert determine_claim_type({"encounter_type": encounter_type}, source) == expected


def test_claim_record_includes_bill_summary_and_code_lists():
    bill = {
        "encounter_id": "ENC-456",
        "encounter_type": "IPD",
        "patient": {
            "Patient ID": "P-456",
            "Patient Name": "Demo Patient",
            "Primary ICD-10-CM": "I10",
            "Secondary ICD-10-CM": "E11.9",
        },
        "lines": [{
            "CPT / HCPCS": "99223",
            "Service Description": "Initial hospital care",
            "Quantity": 1,
            "Line Charge ($)": Decimal("250.00"),
        }],
        "gross": Decimal("250.00"),
    }

    claim = build_claim_record(bill, "IPD")

    assert claim["Claim ID"].startswith("CLM-")
    assert claim["Encounter ID"] == "ENC-456"
    assert claim["Claim Type"] == "837I"
    assert claim["Encounter Type"] == "IPD"
    assert claim["Generated Format"] == "837I"
    assert claim["Status"] == "Pending Review"
    assert claim["Claim Amount"] == 250.0
    assert claim["ICD-10 Codes"] == ["I10", "E11.9"]
    assert claim["Facility Information"]["Name"] == "DEMO FACILITY"
    assert claim["Charges"][0]["Charge Amount ($)"] == 250.0
    assert claim["CPT/HCPCS Codes"] == ["99223"]
    assert "Rendering Provider" not in claim


def test_opd_and_ipd_claims_have_distinct_download_structures():
    bill = {
        "encounter_id": "ENC-100",
        "patient": {
            "Patient ID": "P-100",
            "Patient Name": "Demo Patient",
            "Primary ICD-10-CM": "R05.9",
            "Admission Date": "2026-10-01",
            "Discharge Date": "2026-10-02",
        },
        "lines": [{
            "CPT / HCPCS": "71045",
            "ICD-10-CM Pointer": "R05.9",
            "Revenue Code": "0305",
            "Service Description": "Chest imaging",
            "Quantity": 1,
            "Unit Charge ($)": Decimal("113.00"),
            "Line Charge ($)": Decimal("113.00"),
        }],
        "gross": Decimal("113.00"),
    }

    professional = build_claim_record({**bill, "encounter_type": "OPD"})
    institutional = build_claim_record({**bill, "encounter_type": "IPD"})

    assert {
        "Patient Information", "Rendering Provider", "ICD-10 Codes",
        "CPT/HCPCS Codes", "Diagnosis Pointers", "Charges", "Claim Amount",
        "Place of Service",
    } <= professional.keys()
    assert not {
        "Facility Information", "Admission Date", "Discharge Date",
        "Revenue Codes", "Bill Type",
    } & professional.keys()
    assert "Revenue Code" not in professional["Charges"][0]
    assert professional["Place of Service"] == "11"
    assert professional["Diagnosis Pointers"][0]["Diagnosis Pointers"] == ["R05.9"]
    assert {
        "Patient Information", "Facility Information", "Admission Date",
        "Discharge Date", "Revenue Codes", "ICD-10 Codes", "Charges",
        "CPT/HCPCS Codes", "Claim Amount", "Bill Type",
    } <= institutional.keys()
    assert not {
        "Rendering Provider", "Place of Service", "Diagnosis Pointers",
    } & institutional.keys()
    assert institutional["Revenue Codes"] == ["0305"]
    assert institutional["CPT/HCPCS Codes"] == ["71045"]
    assert institutional["Charges"][0]["Revenue Code"] == "0305"
    assert institutional["Bill Type"] == "111"

    professional_text = _claim_text(professional)
    institutional_text = _claim_text(institutional)
    assert "Charges:" in professional_text
    assert "Place of Service: 11" in professional_text
    assert "Revenue Codes:" not in professional_text
    assert "CPT/HCPCS Codes: 71045" in professional_text
    assert "Claim Amount: $113.00" in professional_text
    assert "Facility Information:" in institutional_text
    assert "Revenue Codes: 0305" in institutional_text
    assert "CPT/HCPCS Codes: 71045" in institutional_text
    assert "Bill Type: 111" in institutional_text
    assert "Claim Amount: $113.00" in institutional_text
    assert "Diagnosis Pointers:" not in institutional_text


def test_opd_split_claims_use_revenue_code_and_keep_amounts_separate():
    bill = {
        "encounter_id": "ENC-SPLIT",
        "encounter_type": "OPD",
        "patient": {"Patient ID": "P-SPLIT", "Patient Name": "Demo Patient"},
        "lines": [
            {
                "CPT / HCPCS": "99213",
                "Service Description": "Office visit",
                "Quantity": 1,
                "Line Charge ($)": Decimal("315.00"),
            },
            {
                "CPT / HCPCS": "71045",
                "Revenue Code": "0305",
                "Service Description": "Chest imaging",
                "Quantity": 1,
                "Line Charge ($)": Decimal("2013.00"),
            },
        ],
        "gross": Decimal("2328.00"),
    }

    split_bills = _split_opd_bill(bill)
    professional = build_claim_record(split_bills["837P"], claim_type="837P")
    facility = build_claim_record(split_bills["837I"], claim_type="837I")

    assert professional["Professional Charges"][0]["CPT/HCPCS Codes"] == ["99213"]
    assert professional["Claim Amount"] == 315.0
    assert facility["Facility Charges"][0]["CPT/HCPCS Codes"] == ["71045"]
    assert facility["Revenue Codes"] == ["0305"]
    assert facility["Total Facility Amount"] == 2013.0
    assert professional["Claim Amount"] + facility["Total Facility Amount"] == 2328.0
    assert not set(map(id, professional["Professional Charges"])) & set(map(id, facility["Facility Charges"]))


def test_encounter_claim_lookup_is_unique_per_claim_type():
    claims = [
        {"Encounter ID": "ENC-1", "Claim Type": "837P"},
        {"Encounter ID": "ENC-1", "Claim Type": "837I"},
        {"Encounter ID": "ENC-2", "Claim Type": "837P"},
    ]

    assert _find_claim_for_encounter(claims, "ENC-1", "837P") is claims[0]
    assert _find_claim_for_encounter(claims, "ENC-1", "837I") is claims[1]
    assert _find_claim_for_encounter(claims, "ENC-1", "837D") is None


def test_claim_record_rejects_total_that_does_not_match_charge_lines():
    bill = {
        "encounter_id": "ENC-789",
        "encounter_type": "OPD",
        "patient": {"Patient ID": "P-789", "Patient Name": "Demo Patient"},
        "lines": [{"CPT / HCPCS": "71045", "Line Charge ($)": Decimal("100.00")}],
        "gross": Decimal("113.00"),
    }

    with pytest.raises(ValueError, match="Bill total must equal"):
        build_claim_record(bill, "OPD")
