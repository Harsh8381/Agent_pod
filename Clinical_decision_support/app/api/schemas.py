from typing import Any

from pydantic import BaseModel, Field

from app.agent.context import EncounterContext


class EhrPatientPayload(BaseModel):
    patient_id: str | None = None
    mrn: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    legal_name: str | None = None
    dob: str | None = None
    age: int | None = None
    sex_at_birth: str | None = None
    pronouns: str | None = None
    marital_status: str | None = None
    race: str | None = None
    ethnicity: str | None = None
    preferred_language: str | None = None
    secondary_language: str | None = None
    pcp: str | None = None
    record_status: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    bmi: float | None = None
    city: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EhrRecordPayload(BaseModel):
    record_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EhrBillPayload(BaseModel):
    encounter_id: str | None = None
    services: list[dict[str, Any]] = Field(default_factory=list)


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