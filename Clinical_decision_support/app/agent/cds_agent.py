from app.llm_client import call_llm
from app.agent.context import EncounterContext


class ClinicalDecisionSupportAgent:
    name = "clinical_decision_support"

    @staticmethod
    def _fallback_recommendation(patient_summary: str) -> str:
        return (
            "Recommendation\n"
            "Review the documented symptoms, relevant history, medications, and vital signs; "
            "confirm the assessment and arrange appropriate follow-up based on clinical judgment.\n\n"
            "Reasoning\n"
            f"The available encounter information was: {patient_summary.strip() or 'limited'}.\n\n"
            "Safety Note\n"
            "This is educational decision support and does not replace evaluation by a qualified clinician."
        )

    def analyze(self, patient_summary):
        encounter = EncounterContext(patient_summary=patient_summary)
        self.analyze_context(encounter)
        return encounter.recommendations

    def analyze_context(self, encounter: EncounterContext) -> EncounterContext:
        from app.rag.retriever import retrieve_documents

        guideline_context = retrieve_documents(encounter.patient_summary, top_k=5)
        encounter.retrieved_guidelines = guideline_context
        prompt = (
            "You are a Clinical Decision Support Assistant.\n"
            "Use the patient summary and retrieved medical guideline context only.\n"
            "Provide a clinically useful recommendation, explain the reasoning, and include a brief safety note that this is educational and decision-support only.\n\n"
            f"Patient Summary:\n{encounter.patient_summary}\n\n"
            f"Retrieved guideline context:\n{guideline_context}\n"
        )
        try:
            recommendation = call_llm(
                prompt=prompt,
                system_prompt="You are a Clinical Decision Support Assistant.",
            )
        except Exception:
            recommendation = ""
        encounter.recommendations = str(recommendation).strip() or self._fallback_recommendation(
            encounter.patient_summary
        )
        return encounter

    def generate(self, patient_summary):
        return self.analyze(patient_summary)
