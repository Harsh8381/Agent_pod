from typing import Any

from pydantic import BaseModel, Field

from app.agent.context import EncounterContext


class PatientAnalysisRequest(BaseModel):
    patient_summary: str = Field(..., min_length=1, description="Patient summary to analyze")


class PatientAnalysisResponse(BaseModel):
    possible_diagnosis: Any = "Not available"
    risk_assessment: Any = "Not available"
    recommended_tests: Any = "Not available"
    treatment_suggestions: Any = "Not available"
    follow_up_plan: Any = "Not available"
    raw_response: str


class KnowledgeBaseRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class KnowledgeBaseResponse(BaseModel):
    query: str
    top_k: int
    context: str


class CodingRequest(BaseModel):
    conditions: list[str] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)
    documentation: str = ""


class CodingResponse(BaseModel):
    icd10: list[dict[str, Any]] = Field(default_factory=list)
    cpt: list[dict[str, Any]] = Field(default_factory=list)


class EncounterRequest(BaseModel):
    transcript: str = Field(..., min_length=1, description="Doctor-patient conversation transcript")
    include_cds: bool = True


class EncounterResponse(EncounterContext):
    pass


class EncounterContextRequest(EncounterContext):
    pass