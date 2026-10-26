from datetime import date
from decimal import Decimal

import pytest

from frontend.claim_generation_page import (
    SAMPLE_PAYER_OPTIONS,
    SAMPLE_PROVIDER_NPIS,
    _dob_min_date,
    _claim_generation_missing_fields,
    _claim_information_defaults,
    _claim_text,
    _claim_needs_opd_resplit,
    _find_claim_for_encounter,
    _split_opd_bill,
    build_ambient_bill,
    build_claim_record,
    determine_claim_type,
)


def test_claim_information_provider_options_and_dob_minimum():
    assert "Emory Healthcare" in SAMPLE_PROVIDER_NPIS
    assert "Athena Health" not in SAMPLE_PROVIDER_NPIS
    assert "Athena Health" not in SAMPLE_PAYER_OPTIONS
    assert "VHC" not in SAMPLE_PAYER_OPTIONS
    assert _dob_min_date().year == date.today().year - 100
    assert _dob_min_date(date(2000, 2, 29)) == date(1900, 2, 28)


def test_claim_information_defaults_combine_bill_and_encounter_data():
    bill = {
        "patient": {
            "Patient Name": "Bill Patient",
            "Provider Name": "Bill Provider",
            "Provider NPI": "1234567890",
        },
        "provider": {"Name": "Billing Provider"},
    }
    encounter_record = {
        "DOB": "1980-01-02",
        "payer": "Demo Health",
        "member_id": "MEM-123",
    }

    defaults = _claim_information_defaults(bill, encounter_record)

    assert defaults == {
        "Patient Name": "Bill Patient",
        "DOB": "1980-01-02",
        "Provider Name": "Billing Provider",
        "Provider NPI": "1234567890",
        "Payer Name": "Demo Health",
        "Member ID": "MEM-123",
    }


def test_claim_generation_required_fields_use_manual_form_values():
    bill = {
        "patient": {
            "Patient Name": "Existing Patient",
            "DOB": "1980-01-02",
            "Provider Name": "Existing Provider",
            "Provider NPI": "1234567890",
        },
    }

    assert _claim_generation_missing_fields(bill, {
        "Patient Name": "",
        "DOB": "",
        "Provider Name": "",
        "Provider NPI": "",
    }) == [
        "Patient Name is missing.",
        "DOB is missing.",
        "Provider Name is missing.",
        "NPI is missing.",
    ]


def test_build_claim_record_uses_claim_information_form_values():
    bill = {
        "encounter_id": "ENC-FORM",
        "encounter_type": "OPD",
        "patient": {
            "Patient ID": "P-FORM",
            "Provider Name": "Original Provider",
            "Provider NPI": "0000000000",
        },
        "provider": {"Name": "Original Provider", "NPI": "0000000000"},
        "lines": [{
            "Service Date": "2026-10-15",
            "CPT / HCPCS": "71045",
            "Quantity": 1,
            "Line Charge ($)": Decimal("113.00"),
        }],
        "gross": Decimal("113.00"),
    }

    claim = build_claim_record(
        bill,
        "OPD",
        claim_information={
            "Patient Name": "Manual Patient",
            "DOB": "1980-01-02",
            "Provider Name": "Manual Provider",
            "Provider NPI": "1234567890",
            "Payer Name": "Demo Health",
            "Member ID": "MEM-123",
        },
    )

    assert claim["Patient Information"]["Patient Name"] == "Manual Patient"
    assert claim["Patient Information"]["DOB"] == "1980-01-02"
    assert claim["Patient Information"]["Payer"] == "Demo Health"
    assert claim["Patient Information"]["Member ID"] == "MEM-123"
    assert claim["Provider Information"] == {
        "Name": "Manual Provider",
        "NPI": "1234567890",
    }


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


def test_opd_split_routes_only_em_codes_to_professional_claim():
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
                "Service Description": "Chest imaging",
                "Quantity": 1,
                "Line Charge ($)": Decimal("2013.00"),
            },
            {
                "CPT / HCPCS": "70450",
                "Service Description": "CT scan",
                "Quantity": 1,
                "Line Charge ($)": Decimal("1500.00"),
            },
            {
                "CPT / HCPCS": "80053",
                "Service Description": "Laboratory panel",
                "Quantity": 1,
                "Line Charge ($)": Decimal("75.00"),
            },
        ],
        "gross": Decimal("3903.00"),
    }

    split_bills = _split_opd_bill(bill)
    professional = build_claim_record(split_bills["837P"], claim_type="837P")
    facility = build_claim_record(split_bills["837I"], claim_type="837I")

    assert professional["Professional Charges"][0]["CPT/HCPCS Codes"] == ["99213"]
    assert professional["Claim Amount"] == 315.0
    assert [charge["CPT/HCPCS Codes"] for charge in facility["Facility Charges"]] == [
        ["71045"],
        ["70450"],
        ["80053"],
    ]
    assert facility["Revenue Codes"]
    assert facility["Total Facility Amount"] == 3588.0
    assert professional["Claim Amount"] + facility["Total Facility Amount"] == 3903.0
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


def test_existing_opd_claim_is_marked_stale_for_refresh():
    expected_bill = {"gross": Decimal("315.00")}

    assert _claim_needs_opd_resplit(
        {"Claim Amount": 2400.0},
        expected_bill,
    )
    assert not _claim_needs_opd_resplit(
        {"Claim Amount": 315.0, "OPD Split Version": 2},
        expected_bill,
    )


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
