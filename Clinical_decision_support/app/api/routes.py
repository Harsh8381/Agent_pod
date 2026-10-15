from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.api.schemas import (
    EncounterRequest,
    EncounterContextRequest,
    EncounterResponse,
    CodingRequest,
    CodingResponse,
    KnowledgeBaseRequest,
    KnowledgeBaseResponse,
    PatientAnalysisRequest,
    PatientAnalysisResponse,
)
from app.agent.encounter_agent import EncounterAgent
from app.services.clinical_service import ClinicalAnalysisService
from app.services.ehr_json_service import (
    JSON_PATH,
    WORKBOOK_PATH,
    add_patient_record,
    create_patient,
    delete_patient,
    export_patient_data,
    generate_patient_bill,
    get_patient_by_id,
    get_patient_chart,
    list_bills,
    list_patient_records,
    list_patients,
    replace_patient_records,
    update_patient,
)
from cpt_coder import get_cpt_candidate_sets, get_cpt_codes, infer_documented_procedures
from icd10_coder import get_icd10_codes


router = APIRouter()


def get_encounter_agent() -> EncounterAgent:
    return EncounterAgent()


def get_transcription_service() -> TranscriptionService:
    from app.services.transcription_service import TranscriptionService

    return TranscriptionService()


def retrieve_documents(query: str, top_k: int) -> str:
    from app.rag.retriever import retrieve_documents as retrieve

    return retrieve(query, top_k)


def _ehr_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/", tags=["system"])
def root():
    return {"name": "Clinical Decision Support API", "docs": "/docs", "health": "/health"}


@router.get("/health", tags=["system"])
def health_check():
    return {"status": "ok"}


@router.get("/dev/diagnostics", tags=["system"])
def development_diagnostics():
    from frontend.chargemaster import _configured_path

    chargemaster_path = _configured_path()
    return {
        "loaded_ehr_service_module": inspect.getfile(list_patients),
        "working_directory": str(Path.cwd()),
        "ehr_excel_path": str(WORKBOOK_PATH),
        "ehr_json_path": str(JSON_PATH),
        "chargemaster_path": str(chargemaster_path) if chargemaster_path else None,
        "ehr_json_filename": JSON_PATH.name,
        "patient_count": len(list_patients()),
        "application_version": "1.0.0",
    }


@router.post("/analyze", response_model=PatientAnalysisResponse, tags=["clinical"])
def analyze_patient(request: PatientAnalysisRequest):
    try:
        result, raw_response = ClinicalAnalysisService().analyze(request.patient_summary)
    except ValueError as error:
        raise HTTPException(status_code=502, detail="The model response was not valid JSON.") from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Clinical analysis failed: {error}") from error

    response_fields = {
        "possible_diagnosis",
        "risk_assessment",
        "recommended_tests",
        "treatment_suggestions",
        "follow_up_plan",
    }
    try:
        return PatientAnalysisResponse(
            raw_response=raw_response,
            **{key: result[key] for key in response_fields if key in result},
        )
    except ValidationError as error:
        raise HTTPException(status_code=502, detail="The model response had an invalid structure.") from error


@router.post("/encounters/analyze", response_model=EncounterResponse, tags=["encounters"])
def analyze_encounter(request: EncounterRequest):
    try:
        agent = get_encounter_agent()
        if request.include_cds and hasattr(agent, "process"):
            context = agent.process(request.transcript.strip())
        else:
            context = agent.prepare(request.transcript.strip())
            if request.include_cds:
                context = agent.add_clinical_decision_support(context)
    except ValueError as error:
        raise HTTPException(status_code=502, detail=f"Ambient scribe response was invalid: {error}") from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ConnectionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Encounter analysis failed: {error}") from error

    return EncounterResponse(**context.model_dump())


@router.post("/encounters/scribe", response_model=EncounterResponse, tags=["encounters"])
def scribe_encounter(request: EncounterRequest):
    try:
        context = get_encounter_agent().prepare(request.transcript.strip())
    except ValueError as error:
        raise HTTPException(status_code=502, detail=f"Ambient scribe response was invalid: {error}") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Scribe analysis failed: {error}") from error

    return EncounterResponse(**context.model_dump())


@router.post("/encounters/cds", response_model=EncounterResponse, tags=["encounters"])
def complete_encounter_with_cds(request: EncounterContextRequest):
    try:
        context = get_encounter_agent().add_clinical_decision_support(request)
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ConnectionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"CDS analysis failed: {error}") from error

    return EncounterResponse(**context.model_dump())


@router.post("/encounters/analyze-audio", response_model=EncounterResponse, tags=["encounters"])
async def analyze_audio(request: Request):
    try:
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise ValueError("An audio file is required.")
        transcript = get_transcription_service().transcribe(await file.read())
        context = get_encounter_agent().process(transcript.strip())
    except ValueError as error:
        raise HTTPException(status_code=502, detail=f"Encounter processing failed: {error}") from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Audio encounter analysis failed: {error}") from error

    return EncounterResponse(**context.model_dump())


@router.post("/retrieve", response_model=KnowledgeBaseResponse, tags=["knowledge base"])
def retrieve_knowledge(request: KnowledgeBaseRequest):
    try:
        context = retrieve_documents(request.query.strip(), top_k=request.top_k)
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Knowledge-base retrieval failed: {error}") from error

    return KnowledgeBaseResponse(query=request.query, top_k=request.top_k, context=context)


@router.post("/codes", response_model=CodingResponse, tags=["coding"])
def match_codes(request: CodingRequest):
    """Return diagnosis codes and CPT/HCPCS codes for explicitly supplied items."""
    try:
        icd10 = get_icd10_codes(request.conditions, threshold=80)
        procedures = list(request.procedures) + infer_documented_procedures(request.documentation)
        documentation = request.documentation.lower()
        if any(term in documentation for term in ("a1c", "hba1c", "hemoglobin a1c")):
            procedures.append("A1C Test")
        if (
            ("chest x-ray" in documentation or "chest x ray" in documentation)
            and any(term in documentation for term in ("two view", "to view", "2 view"))
        ):
            procedures.append("Chest x ray 2 views")
        procedures = list(dict.fromkeys(procedures))
        if request.documentation:
            details = get_cpt_candidate_sets(
                procedures,
                request.documentation,
                threshold=80,
            )
            cpt = [
                {
                    "Extracted Procedure": item["term"],
                    "Matched Procedure/Service": item["selected_description"],
                    "CPT/HCPCS Code": item["selected_code"],
                }
                for item in details
                if item["status"] == "suggested"
            ]
        else:
            cpt = get_cpt_codes(procedures, threshold=80)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Code matching failed: {error}") from error

    return CodingResponse(icd10=icd10, cpt=cpt)


@router.get("/ehr/patients", tags=["ehr"])
@router.get("/patients", tags=["ehr"])
def list_ehr_patients():
    patients = list_patients()
    return {"patients": patients, "count": len(patients)}


@router.get("/patients/export", tags=["ehr"])
def export_patient_data_route(patient_id: str | None = None):
    return _ehr_call(export_patient_data, patient_id)


@router.get("/ehr/patients/{patient_id}", tags=["ehr"])
@router.get("/patients/{patient_id}", tags=["ehr"])
def get_ehr_patient(patient_id: str):
    return _ehr_call(get_patient_by_id, patient_id)


@router.get("/ehr/patients/{patient_id}/chart", tags=["ehr"])
@router.get("/patients/{patient_id}/chart", tags=["ehr"])
def get_ehr_chart(patient_id: str):
    return _ehr_call(get_patient_chart, patient_id)


@router.get("/ehr/export", tags=["ehr"])
def export_ehr_data(patient_id: str | None = None):
    return _ehr_call(export_patient_data, patient_id)


@router.post("/ehr/patients", tags=["ehr"])
@router.post("/patients", tags=["ehr"])
def create_ehr_patient(payload: dict[str, Any]):
    return _ehr_call(create_patient, payload or {})


@router.put("/ehr/patients/{patient_id}", tags=["ehr"])
@router.put("/patients/{patient_id}", tags=["ehr"])
def update_ehr_patient(patient_id: str, payload: dict[str, Any]):
    return _ehr_call(update_patient, patient_id, payload or {})


@router.delete("/ehr/patients/{patient_id}", tags=["ehr"])
@router.delete("/patients/{patient_id}", tags=["ehr"])
def delete_ehr_patient(patient_id: str):
    deleted = _ehr_call(delete_patient, patient_id)
    return {"patient_id": patient_id, "deleted": deleted}


@router.get("/ehr/patients/{patient_id}/records", tags=["ehr"])
def list_ehr_records(patient_id: str, record_type: str | None = None):
    records = _ehr_call(list_patient_records, patient_id, record_type=record_type)
    return {"patient_id": patient_id, "record_type": record_type, "records": records, "count": len(records)}


@router.post("/ehr/patients/{patient_id}/records", tags=["ehr"])
def add_ehr_record(patient_id: str, payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object payload.")
    record_type = str(payload.get("record_type") or payload.get("type") or "general").strip()
    record_payload = payload.get("payload") or payload.get("record") or payload.get("data") or {}
    if not isinstance(record_payload, dict):
        raise HTTPException(status_code=400, detail="Record payload must be an object.")
    return _ehr_call(add_patient_record, patient_id, record_type, record_payload)


@router.put("/ehr/patients/{patient_id}/records/{record_type}", tags=["ehr"])
def replace_ehr_records(patient_id: str, record_type: str, payload: dict[str, Any] | list[dict[str, Any]]):
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("records") or payload.get("data") or []
    else:
        records = []
    return _ehr_call(replace_patient_records, patient_id, record_type, records)


@router.put("/patients/{patient_id}/records/{sheet_name}", tags=["ehr"])
def replace_patient_sheet_records(patient_id: str, sheet_name: str, payload: dict[str, Any] | list[dict[str, Any]]):
    records = payload if isinstance(payload, list) else payload.get("records", []) if isinstance(payload, dict) else []
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise HTTPException(status_code=400, detail="Records must be a list of JSON objects.")
    return _ehr_call(replace_patient_records, patient_id, sheet_name, records)


@router.post("/patients/{patient_id}/records/{sheet_name}", tags=["ehr"])
def create_patient_sheet_record(patient_id: str, sheet_name: str, payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Record payload must be a JSON object.")
    return _ehr_call(add_patient_record, patient_id, sheet_name, payload)


@router.get("/ehr/bills", tags=["ehr"])
def list_ehr_bills(patient_id: str | None = None):
    bills = list_bills(patient_id)
    return {"bills": bills, "count": len(bills)}


@router.post("/ehr/patients/{patient_id}/bills", tags=["ehr"])
def create_ehr_bill(patient_id: str, payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object payload.")
    encounter_id = payload.get("encounter_id")
    services = payload.get("services") or payload.get("bill_lines") or []
    if not isinstance(services, list):
        raise HTTPException(status_code=400, detail="The services list is required.")
    return _ehr_call(generate_patient_bill, patient_id, {**payload, "encounter_id": encounter_id, "services": services})


@router.post("/patients/{patient_id}/bills/ipd", tags=["ehr"])
def create_ipd_patient_bill(patient_id: str, payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object payload.")
    payload.setdefault("encounter_type", "IPD")
    payload.setdefault("services", payload.get("bill_lines") or [])
    return _ehr_call(generate_patient_bill, patient_id, payload)


@router.post("/patients/{patient_id}/bills/opd", tags=["ehr"])
def create_opd_patient_bill(patient_id: str, payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object payload.")
    payload.setdefault("encounter_type", "OPD")
    payload.setdefault("services", payload.get("bill_lines") or [])
    return _ehr_call(generate_patient_bill, patient_id, payload)
