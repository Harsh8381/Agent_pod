"""CPT/HCPCS coding independent from ICD-10 matching."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
CPT_FILE_NAMES = [
    "CPT_CODES.json",
    "structured_cpt_hcpcs_2026.json",
    "cpt_codes.json",
]
DEFAULT_THRESHOLD = 90
DEFAULT_MAX_MATCHES = 1
DEFAULT_TOP_CANDIDATES = 5

COMMON_CPT_FALLBACKS = {
    "chest x ray 2 views": {"code": "71046", "description": "X-ray exam, chest, 2 views", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "x ray exam chest 2 views": {"code": "71046", "description": "X-ray exam, chest, 2 views", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "chest x ray": {"code": "71045", "description": "X-ray exam, chest, 1 view", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "x ray exam chest 1 view": {"code": "71045", "description": "X-ray exam, chest, 1 view", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "ct scan of chest": {"code": "71250", "description": "CT scan of chest without contrast", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "ct chest": {"code": "71250", "description": "CT scan of chest without contrast", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "complete blood count": {"code": "85025", "description": "Blood count; complete (CBC), automated", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "blood count": {"code": "85025", "description": "Blood count; complete (CBC), automated", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "blood count test": {"code": "85025", "description": "Blood count; complete (CBC), automated", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "cbc": {"code": "85025", "description": "Blood count; complete (CBC), automated", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "cbc test": {"code": "85025", "description": "Blood count; complete (CBC), automated", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "blood glucose test": {"code": "82947", "description": "Glucose; blood, reagent strip", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "blood glucose": {"code": "82947", "description": "Glucose; blood, reagent strip", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "hemoglobin a1c test": {"code": "83036", "description": "Hemoglobin A1c", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "hemoglobin a1 test": {"code": "83036", "description": "Hemoglobin A1c", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "hba1c": {"code": "83036", "description": "Hemoglobin A1c", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "hba1c test": {"code": "83036", "description": "Hemoglobin A1c", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "a1c test": {"code": "83036", "description": "Hemoglobin A1c", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "a1 c test": {"code": "83036", "description": "Hemoglobin A1c", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "sputum laboratory test": {"code": "87070", "description": "Culture, bacterial; any source except urine, blood or stool", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "sputum culture": {"code": "87070", "description": "Culture, bacterial; any source except urine, blood or stool", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "abdominal ultrasound": {"code": "76700", "description": "Ultrasound, abdomen, complete", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "ultrasound abdomen": {"code": "76700", "description": "Ultrasound, abdomen, complete", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "physical therapy": {"code": "97110", "description": "Therapeutic exercises to develop strength and endurance, range of motion and flexibility", "code_system": "CPT/HCPCS", "service_category": "Rehabilitation"},
    "physical therapy treatment": {"code": "97110", "description": "Therapeutic exercises to develop strength and endurance, range of motion and flexibility", "code_system": "CPT/HCPCS", "service_category": "Rehabilitation"},
    "established patient office visit": {"code": "99213", "description": "Established patient office or other outpatient visit, 15 minutes", "code_system": "CPT/HCPCS", "service_category": "Evaluation and Management"},
    "office visit": {"code": "99213", "description": "Established patient office or other outpatient visit, 15 minutes", "code_system": "CPT/HCPCS", "service_category": "Evaluation and Management"},
}

# Supplemental service mappings cover common procedures absent from the small
# local dataset. Medication names intentionally do not map to CPT codes.
ADDITIONAL_CPT_FALLBACKS = {
    "comprehensive metabolic panel": {"code": "80053", "description": "Comprehensive metabolic panel", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "basic metabolic panel": {"code": "80048", "description": "Basic metabolic panel", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "renal function panel": {"code": "80069", "description": "Renal function panel", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "urinalysis": {"code": "81001", "description": "Urinalysis, automated, with microscopy", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "lipid panel": {"code": "80061", "description": "Lipid panel", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "psa screening": {"code": "84153", "description": "Prostate specific antigen", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "breast ultrasound": {"code": "76641", "description": "Ultrasound, breast, complete", "code_system": "CPT/HCPCS", "service_category": "Radiology"},
    "therapeutic injection administration": {"code": "96372", "description": "Therapeutic, prophylactic, or diagnostic injection administration", "code_system": "CPT/HCPCS", "service_category": "Injection Administration"},
}

DOCUMENTATION_PROCEDURE_ALIASES = {
    "comprehensive metabolic panel": "comprehensive metabolic panel",
    "basic metabolic panel": "basic metabolic panel",
    "kidney function test": "renal function panel",
    "renal function test": "renal function panel",
    "renal function panel": "renal function panel",
    "urinalysis": "urinalysis",
    "urine analysis": "urinalysis",
    "lipid panel": "lipid panel",
    "psa screening": "psa screening",
    "prostate specific antigen": "psa screening",
    "breast ultrasound": "breast ultrasound",
}

_cpt_dataframe = None

PROCEDURE_KEYWORDS = ("x ray", "x-ray", "scan", "surgery", "biopsy", "therapy", "injection", "procedure", "endoscopy", "ultrasound", "mri", "ct", "cbc", "a1c", "hba1c", "blood test", "office visit")


def classify_medical_item(item: Any) -> str | None:
    normalized = normalize_text(item)
    return "cpt" if any(keyword in normalized for keyword in PROCEDURE_KEYWORDS) else None


def is_explicitly_performed(term: Any, evidence: str = "") -> bool:
    normalized_term = normalize_text(term)
    normalized_evidence = normalize_text(evidence)
    if not normalized_evidence:
        return False
    if normalized_term in {"office visit", "established patient office visit"}:
        return True
    if normalized_term in {"cbc", "complete blood count", "a1c", "a1c test", "hba1c", "hba1c test", "chest x ray", "ct scan", "ultrasound", "mri", "blood glucose test"}:
        return bool(re.search(
            rf"\b(?:performed|completed|done|obtained|administered|collected|read|interpreted|reported|documented)\b[^.\n]*\b{re.escape(normalized_term)}\b",
            normalized_evidence,
        )) or bool(re.search(rf"\b{re.escape(normalized_term)}\b", normalized_evidence))
    return bool(re.search(
        rf"\b(?:performed|completed|done|obtained|administered|collected|read|interpreted|reported|documented)\b[^.\n]*\b{re.escape(normalized_term)}\b",
        normalized_evidence,
    ))


def extract_performed_procedures(documentation: str) -> list[str]:
    return [term for term in PROCEDURE_KEYWORDS if is_explicitly_performed(term, documentation)]


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower())).strip()


def normalize_code(value: Any) -> str:
    return str(value or "").strip().upper()


def _column_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def normalize_detected_procedures(detected_procedures: Any) -> list[str]:
    """Normalize strings and supported procedure dictionaries into unique terms."""
    if detected_procedures is None:
        return []
    if isinstance(detected_procedures, (str, dict)):
        detected_procedures = [detected_procedures]
    elif not isinstance(detected_procedures, (list, tuple, set)):
        detected_procedures = [detected_procedures]
    supported_keys = (
        "procedure", "procedure_name", "procedureName", "detected_procedure",
        "detectedProcedure", "service", "service_name", "serviceName", "test",
        "test_name", "testName", "description", "text", "name", "item", "value",
    )
    normalized = []
    seen = set()
    for item in detected_procedures:
        value = None
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            for key in supported_keys:
                if item.get(key):
                    value = item[key]
                    break
        elif item is not None:
            value = str(item)
        if value is None:
            continue
        value = " ".join(str(value).split())
        key = normalize_text(value)
        if key and key not in seen:
            seen.add(key)
            normalized.append(value)
    logger.debug("CPT normalized items count=%d", len(normalized))
    return normalized


def infer_documented_procedures(documentation: str) -> list[str]:
    """Infer known billable services from documentation without treating drugs as CPT."""
    normalized_documentation = normalize_text(documentation)
    inferred = []
    for phrase, procedure in sorted(DOCUMENTATION_PROCEDURE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if phrase in normalized_documentation and procedure not in inferred:
            inferred.append(procedure)
    return inferred


def _find_json_file() -> Path | None:
    for directory in (BASE_DIR, BASE_DIR / "data", PROJECT_DIR, PROJECT_DIR / "data", Path.cwd(), Path.cwd() / "data"):
        for filename in CPT_FILE_NAMES:
            path = directory / filename
            if path.is_file():
                logger.debug("CPT selected JSON file=%s", path)
                return path
    logger.warning("CPT JSON file not found; checked names=%s", CPT_FILE_NAMES)
    return None


def _read_records(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("CPT JSON could not be parsed: %s", error)
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("codes", "records", "data", "items", "results", "cpt_codes", "hcpcs_codes"):
            actual = next((candidate for candidate in payload if _column_key(candidate) == _column_key(key)), None)
            if actual is not None and isinstance(payload[actual], list):
                return [item for item in payload[actual] if isinstance(item, dict)]
        return [{"code": code, "description": value} for code, value in payload.items() if isinstance(value, str)]
    return []


def _find_column(columns: list[Any], names: tuple[str, ...]) -> Any | None:
    available = {_column_key(column): column for column in columns}
    for name in names:
        if _column_key(name) in available:
            return available[_column_key(name)]
    return None


def load_cpt_data(force_reload: bool = False):
    """Load and cache CPT data, retaining alternate descriptions for a code."""
    global _cpt_dataframe
    if _cpt_dataframe is not None and not force_reload:
        return _cpt_dataframe
    import pandas as pd

    path = _find_json_file()
    records = _read_records(path)
    if not records:
        _cpt_dataframe = pd.DataFrame(columns=["code", "description", "search_text"])
        logger.warning("CPT dataset size=0")
        return _cpt_dataframe
    dataframe = pd.DataFrame(records)
    logger.debug("CPT detected columns=%s", list(dataframe.columns))
    code_column = _find_column(list(dataframe.columns), ("code", "cpt_code", "hcpcs_code", "cpt_hcpcs_code", "procedure_code"))
    description_column = _find_column(list(dataframe.columns), ("description", "procedure_description", "service_description", "long_description", "short_description", "name", "title"))
    if code_column is None or description_column is None:
        logger.warning("CPT required columns missing; available=%s", list(dataframe.columns))
        _cpt_dataframe = pd.DataFrame(columns=["code", "description", "search_text"])
        return _cpt_dataframe
    dataframe = dataframe.rename(columns={code_column: "code", description_column: "description"})
    dataframe["code"] = dataframe["code"].map(normalize_code)
    dataframe["description"] = dataframe["description"].fillna("").astype(str).str.strip()
    dataframe["search_text"] = dataframe["description"].map(normalize_text)
    dataframe = dataframe[(dataframe["code"] != "") & (dataframe["search_text"] != "")].copy()
    dataframe.drop_duplicates(subset=["code", "search_text"], keep="first", inplace=True)
    dataframe.reset_index(drop=True, inplace=True)
    _cpt_dataframe = dataframe
    logger.debug("CPT dataset size=%d", len(dataframe))
    return dataframe


def _score(query: str, description: str) -> float:
    query = normalize_text(query)
    description = normalize_text(description)
    if not query or not description:
        return 0.0
    if query == description:
        return 100.0
    if query in description or description in query:
        return 96.0
    query_tokens = set(query.split())
    description_tokens = set(description.split())
    overlap = len(query_tokens & description_tokens) / max(len(query_tokens), 1)
    token_score = fuzz.token_set_ratio(query, description)
    return round(max(token_score, overlap * 100.0), 2)


def _dataset_matches(item: str, dataframe, threshold: float, max_matches: int) -> list[dict[str, Any]]:
    query = normalize_text(item)
    query_code = normalize_code(item)
    matches = []
    for _, row in dataframe.iterrows():
        description = row["description"]
        search_text = row["search_text"]
        score = 100.0 if row["code"] == query_code else _score(query, search_text)
        if query == search_text:
            score = 100.0
        elif query in search_text or search_text in query:
            score = max(score, 96.0)
        if score >= threshold:
            matches.append({"extracted_item": item, "code": row["code"], "description": description, "_score": score, "source": "dataset"})
    if not matches and query:
        choices = process.extract(query, dataframe["search_text"].tolist(), scorer=fuzz.token_set_ratio, score_cutoff=threshold, limit=max_matches)
        for _, score, index in choices:
            row = dataframe.iloc[index]
            matches.append({"extracted_item": item, "code": row["code"], "description": row["description"], "_score": float(score), "source": "dataset"})
    matches.sort(key=lambda value: (-value["_score"], value["code"], value["description"]))
    logger.debug("CPT item matched term=%s match_count=%d", normalize_text(item), len(matches))
    return matches[:max_matches]


def _fallback_for(item: str) -> dict[str, Any] | None:
    normalized = normalize_text(item)
    all_fallbacks = {**COMMON_CPT_FALLBACKS, **ADDITIONAL_CPT_FALLBACKS}
    for alias in sorted(all_fallbacks, key=len, reverse=True):
        if normalized == alias or alias in normalized or normalized in alias:
            return all_fallbacks[alias]
    return None


def get_cpt_codes(detected_procedures: Any, threshold: float = DEFAULT_THRESHOLD, max_matches: int = DEFAULT_MAX_MATCHES) -> list[dict[str, str]]:
    logger.debug("CPT raw input procedures=%r", detected_procedures)
    items = normalize_detected_procedures(detected_procedures)
    logger.debug("CPT raw input count=%d", len(items))
    if not items:
        return []
    dataframe = load_cpt_data()
    threshold = float(threshold)
    max_matches = max(1, int(max_matches))
    results = []
    for item in items:
        dataset_matches = _dataset_matches(item, dataframe, threshold, max_matches)
        selected = dataset_matches[0] if dataset_matches else None
        if selected is None:
            fallback = _fallback_for(item)
            if fallback is not None:
                logger.debug("CPT fallback matched term=%s code=%s", normalize_text(item), fallback["code"])
                selected = {"extracted_item": item, "code": fallback["code"], "description": fallback["description"], "source": "fallback"}
        if selected is not None:
            results.append({"Extracted Procedure": selected["extracted_item"], "Matched Procedure/Service": selected["description"], "CPT/HCPCS Code": selected["code"]})
    logger.debug("CPT final results count=%d", len(results))
    return results


def get_cpt_candidate_sets(performed_procedures: Any, documentation: str = "", threshold: float = DEFAULT_THRESHOLD, top_n: int = DEFAULT_TOP_CANDIDATES) -> list[dict[str, Any]]:
    items = normalize_detected_procedures(performed_procedures)
    dataframe = load_cpt_data()
    results = []
    for item in items:
        matches = _dataset_matches(item, dataframe, float(threshold), top_n)
        selected = matches[0] if matches else None
        if selected is None:
            fallback = _fallback_for(item)
            if fallback is not None:
                selected = {"extracted_item": item, "code": fallback["code"], "description": fallback["description"], "_score": 100.0, "source": "fallback"}
                matches = [selected]
        if selected is None:
            results.append({"term": item, "normalized_term": normalize_text(item), "status": "review_required", "selected_code": None, "selected_description": None, "confidence": 0.0, "reason": "No CPT candidate matched the supplied threshold.", "candidates": [], "alternatives": []})
        else:
            results.append({"term": item, "normalized_term": normalize_text(item), "status": "suggested", "selected_code": selected["code"], "selected_description": selected["description"], "confidence": round(float(selected.get("_score", 100.0)) / 100.0, 3), "reason": f"Matched CPT {selected['source']}.", "candidates": matches, "alternatives": matches[1:]})
    return results


def get_medical_codes(detected_items: Any, threshold: float = DEFAULT_THRESHOLD, max_matches: int = DEFAULT_MAX_MATCHES, code_type: str | None = None) -> list[dict[str, str]]:
    """CPT-compatible legacy router; explicit CPT types bypass classification."""
    if code_type is not None and normalize_text(code_type) not in {"cpt", "hcpcs", "cpthcpcs", "procedure", "service"}:
        return []
    return get_cpt_codes(detected_items, threshold=threshold, max_matches=max_matches)


def match_clinical_document(conditions=None, procedures=None, threshold=DEFAULT_THRESHOLD, max_matches=DEFAULT_MAX_MATCHES):
    from icd10_coder import get_icd10_codes
    return {"icd10_matches": get_icd10_codes(conditions, threshold=threshold, max_matches=max_matches), "cpt_hcpcs_matches": get_cpt_codes(procedures, threshold=threshold, max_matches=max_matches)}
