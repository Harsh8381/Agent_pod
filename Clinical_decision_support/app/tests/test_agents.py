from app.agent.ambient_scribe_agent import AmbientScribeAgent
from app.agent.context import EncounterContext
from app.agent.cds_agent import ClinicalDecisionSupportAgent
from app import llm_client


def test_ambient_scribe_parses_json_code_fence():
    context = AmbientScribeAgent._parse_response(
        '```json\n{"chief_complaint":"cough"}\n```'
    )

    assert context == EncounterContext(chief_complaint="cough")


def test_soap_note_falls_back_to_patient_summary_when_fields_are_empty():
    context = EncounterContext(
        patient_summary="Chief Complaint:\nFever\n\nHistory of Present Illness:\nPatient has fever.\n\nAssessment:\nPossible infection.\n\nPlan:\nOrder labs.",
    )

    note = context.as_soap_note()

    assert "Subjective:" in note
    assert "Fever" in note
    assert "Assessment:" in note
    assert "Order labs." in note


def test_cds_always_returns_recommendation_when_llm_is_empty(monkeypatch):
    monkeypatch.setattr("app.rag.retriever.retrieve_documents", lambda query, top_k: "")
    monkeypatch.setattr("app.agent.cds_agent.call_llm", lambda **kwargs: "")

    context = ClinicalDecisionSupportAgent().analyze_context(
        EncounterContext(patient_summary="Mild cough.")
    )

    assert "Recommendation" in context.recommendations
    assert "Mild cough." in context.recommendations


def test_call_llm_uses_cpt_api_key_override(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "done"}}]}

    class FakeSession:
        def mount(self, *args, **kwargs):
            pass

        def post(self, url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.llm_client.COFORGE_API_KEY", "general-key")
    monkeypatch.setattr("app.llm_client.COFORGE_CPT_API_KEY", "cpt-key")
    monkeypatch.setattr("app.llm_client.COFORGE_API_URL", "https://example.com/router")
    monkeypatch.setattr("app.llm_client.COFORGE_CPT_API_URL", "https://example.com/cpt-router")
    monkeypatch.setattr("app.llm_client.COFORGE_MODEL", "model-a")
    monkeypatch.setattr("app.llm_client.COFORGE_CPT_MODEL", "cpt-model")
    monkeypatch.setattr("app.llm_client.requests.Session", lambda: FakeSession())

    result = llm_client.call_llm_for_cpt("Find CPT code")

    assert result == "done"
    assert captured["headers"]["X-API-KEY"] == "cpt-key"
    assert captured["json"]["model"] == "model-a"