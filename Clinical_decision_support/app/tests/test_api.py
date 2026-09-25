from fastapi.testclient import TestClient

from app.main import app
from app.api import routes


def test_health_check():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_retrieve_endpoint(monkeypatch):
    monkeypatch.setattr(routes, "retrieve_documents", lambda query, top_k: "context")
    client = TestClient(app)

    response = client.post("/retrieve", json={"query": "chest pain", "top_k": 3})

    assert response.status_code == 200
    assert response.json()["context"] == "context"


def test_codes_endpoint_returns_cpt_for_treatment_procedure(monkeypatch):
    monkeypatch.setattr(routes, "get_cpt_codes", lambda procedures, threshold: [{
        "Extracted Procedure": procedures[0],
        "Matched Procedure/Service": "Physical therapy treatment",
        "CPT/HCPCS Code": "97110",
    }])
    client = TestClient(app)

    response = client.post("/codes", json={"procedures": ["Physical therapy treatment"]})

    assert response.status_code == 200
    assert response.json()["cpt"][0]["CPT/HCPCS Code"] == "97110"


def test_analyze_endpoint(monkeypatch):
    class FakeService:
        def analyze(self, patient_summary):
            return {"risk_assessment": "low"}, '{"risk_assessment":"low"}'

    monkeypatch.setattr(routes, "ClinicalAnalysisService", FakeService)
    client = TestClient(app)

    response = client.post("/analyze", json={"patient_summary": "patient summary"})

    assert response.status_code == 200
    assert response.json()["risk_assessment"] == "low"


def test_empty_patient_summary_is_rejected():
    client = TestClient(app)

    response = client.post("/analyze", json={"patient_summary": ""})

    assert response.status_code == 422


def test_encounter_endpoint(monkeypatch):
    class FakeEncounterAgent:
        def process(self, transcript):
            from app.agent.context import EncounterContext

            return EncounterContext(
                transcript=transcript,
                patient_summary="Cough reported.",
                soap_note="Subjective: Cough.",
                recommendations="Monitor symptoms.",
                metadata={"agents_invoked": ["ambient_scribe", "clinical_decision_support"]},
            )

        def prepare(self, transcript):
            return self.process(transcript)

        def add_clinical_decision_support(self, context):
            return context

    monkeypatch.setattr(routes, "get_encounter_agent", lambda: FakeEncounterAgent())
    client = TestClient(app)

    response = client.post("/encounters/analyze", json={"transcript": "Patient reports cough."})

    assert response.status_code == 200
    assert response.json()["patient_summary"] == "Cough reported."
    assert response.json()["soap_note"] == "Subjective: Cough."
    assert response.json()["metadata"]["agents_invoked"] == ["ambient_scribe", "clinical_decision_support"]


def test_cds_endpoint_uses_existing_encounter_context(monkeypatch):
    class FakeEncounterAgent:
        def add_clinical_decision_support(self, context):
            from app.agent.context import EncounterContext

            assert context.transcript == "Patient reports cough."
            assert context.patient_summary == "Cough reported."
            context.recommendations = "Monitor symptoms."
            return context

    monkeypatch.setattr(routes, "get_encounter_agent", lambda: FakeEncounterAgent())
    client = TestClient(app)

    response = client.post(
        "/encounters/cds",
        json={
            "transcript": "Patient reports cough.",
            "patient_summary": "Cough reported.",
        },
    )

    assert response.status_code == 200
    assert response.json()["recommendations"] == "Monitor symptoms."


def test_append_selected_insights_to_note_adds_values_under_heading():
    from frontend.streamlit_app import append_selected_insights_to_note

    summary = "Subjective: Cough.\n- **MAJOR DIAGNOSIS / ISSUES**\nAwaiting manual input."

    updated = append_selected_insights_to_note(summary, ["Hyperlipidemia", "Hypertension"])

    assert "Hyperlipidemia" in updated
    assert "Hypertension" in updated
    assert "- **MAJOR DIAGNOSIS / ISSUES**" in updated


def test_code_suggestions_separate_diagnoses_and_procedures():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Hypertension", "Complete blood count", "Color Blindness"])

    assert result["icd10"]
    assert result["cpt"]
    assert all("ICD-10 Code" in item for item in result["icd10"])
    assert all("CPT/HCPCS Code" in item for item in result["cpt"])


def test_hypertension_uses_general_icd10_code():
    from code_matcher import get_icd10_codes

    result = get_icd10_codes(["Hypertension"])

    assert result == [{
        "Extracted Condition": "Hypertension",
        "Matched Disease/Injury": "Essential (primary) hypertension",
        "ICD-10 Code": "I10",
    }]


def test_procedure_insights_return_cpt_codes():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Complete Blood Count"])

    assert result["icd10"] == []
    assert result["cpt"]
    assert result["cpt"][0]["CPT/HCPCS Code"] == "85025"


def test_generic_encounter_returns_office_visit_cpt_code():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Established Patient Office Visit"], "Patient evaluated.")

    assert result["cpt"]
    assert result["cpt"][0]["CPT/HCPCS Code"] == "99213"


def test_code_suggestions_route_office_visit_to_cpt():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Established Patient Office Visit"], "Patient evaluated.")

    assert result["icd10"] == []
    assert any(item["CPT/HCPCS Code"] == "99213" for item in result["cpt"])


def test_codes_endpoint_returns_office_visit_cpt_code():
    client = TestClient(app)

    response = client.post(
        "/codes",
        json={"procedures": ["Established Patient Office Visit"]},
    )

    assert response.status_code == 200
    assert response.json()["cpt"][0]["CPT/HCPCS Code"] == "99213"


def test_physical_therapy_is_detected_as_a_cpt_insight():
    from frontend.streamlit_app import get_detected_insights

    assert "Physical Therapy" in get_detected_insights("Begin physical therapy twice weekly.")


def test_planned_a1c_returns_cpt_code():
    from code_matcher import get_cpt_candidate_sets

    results = get_cpt_candidate_sets(["A1C Test"], "Completing an hba1c test is recommended.")

    assert results[0]["selected_code"] == "83036"
    assert results[0]["status"] == "suggested"


def test_ordered_head_ct_and_two_view_chest_xray_use_specific_cpt_codes():
    from code_matcher import get_cpt_candidate_sets

    results = get_cpt_candidate_sets(
        ["CT scan of head without contrast", "Chest x ray 2 views"],
        "I am ordering a two view chest X-ray and a CT scan of the head without contrast.",
    )

    assert [item["selected_code"] for item in results] == ["70450", "71046"]


def test_codes_endpoint_forces_common_a1c_and_two_view_xray_codes():
    client = TestClient(app)
    response = client.post(
        "/codes",
        json={
            "documentation": "Complete hba1c test and order a to view chest X-ray.",
        },
    )

    assert response.status_code == 200
    codes = {item["CPT/HCPCS Code"] for item in response.json()["cpt"]}
    assert {"83036", "71046"}.issubset(codes)