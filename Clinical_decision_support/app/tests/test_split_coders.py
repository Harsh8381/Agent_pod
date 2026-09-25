import json
from pathlib import Path

import code_matcher
import cpt_coder
from fastapi.testclient import TestClient

from app.main import app


def _dataset_descriptions():
    path = Path(__file__).parents[2] / "CPT_CODES.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_icd_output_remains_unchanged():
    assert code_matcher.get_icd10_codes(["Hypertension"]) == [{
        "Extracted Condition": "Hypertension",
        "Matched Disease/Injury": "Essential (primary) hypertension",
        "ICD-10 Code": "I10",
    }]


def test_cpt_common_procedures():
    assert cpt_coder.get_cpt_codes(["Chest x-ray 2 views"])[0]["CPT/HCPCS Code"] == "71046"
    assert cpt_coder.get_cpt_codes(["Complete blood count"])[0]["CPT/HCPCS Code"] == "85025"
    assert cpt_coder.get_cpt_codes(["Hemoglobin A1c test"])[0]["CPT/HCPCS Code"] == "83036"


def test_dataset_conditional_breast_ultrasound_and_psa():
    descriptions = " ".join(item.get("description", "").lower() for item in _dataset_descriptions())
    if "breast" in descriptions and "ultrasound" in descriptions:
        assert cpt_coder.get_cpt_codes(["Breast ultrasound"])
    if "psa" in descriptions or "prostate specific antigen" in descriptions:
        assert cpt_coder.get_cpt_codes(["PSA screening"])


def test_procedure_dictionary_key_variants():
    values = [
        {"procedure": "Hemoglobin A1c test"},
        {"procedure_name": "Chest x-ray 2 views"},
        {"procedureName": "Complete blood count"},
    ]
    codes = {item["CPT/HCPCS Code"] for item in cpt_coder.get_cpt_codes(values)}
    assert {"83036", "71046", "85025"}.issubset(codes)


def test_explicit_cpt_code_type_routes_all_items():
    result = code_matcher.get_medical_codes(
        [{"procedure_name": "Hemoglobin A1c test"}],
        code_type="cpt",
    )
    assert result[0]["CPT/HCPCS Code"] == "83036"


def test_combined_response_preserves_cpt_key():
    result = code_matcher.match_clinical_document(
        conditions=["Hypertension"],
        procedures=["Complete blood count"],
    )
    assert set(result) == {"icd10_matches", "cpt_hcpcs_matches"}
    assert result["cpt_hcpcs_matches"][0]["CPT/HCPCS Code"] == "85025"


def test_api_response_contains_cpt_codes():
    response = TestClient(app).post(
        "/codes",
        json={"procedures": ["Hemoglobin A1c test"]},
    )
    assert response.status_code == 200
    assert response.json()["cpt"][0]["CPT/HCPCS Code"] == "83036"


def test_common_documented_services_receive_cpt_codes():
    response = TestClient(app).post(
        "/codes",
        json={
            "documentation": "Order comprehensive metabolic panel, lipid panel, urinalysis, PSA screening, and breast ultrasound."
        },
    )
    codes = {item["CPT/HCPCS Code"] for item in response.json()["cpt"]}
    assert {"80053", "80061", "81001", "84153", "76641"}.issubset(codes)


def test_medications_do_not_receive_cpt_codes():
    assert cpt_coder.get_cpt_codes(["metformin", "ibuprofen", "paracetamol"]) == []
