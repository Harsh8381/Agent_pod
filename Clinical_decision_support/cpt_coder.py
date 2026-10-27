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
    "blood culture": {"code": "87040", "description": "Culture, bacterial; blood, aerobic", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "urine culture": {"code": "87086", "description": "Culture, bacterial; urine, quantitative", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "troponin test": {"code": "84484", "description": "Troponin, quantitative", "code_system": "CPT/HCPCS", "service_category": "Laboratory"},
    "complete transthoracic echocardiogram": {"code": "93306", "description": "Complete transthoracic echocardiography with Doppler and color flow", "code_system": "CPT/HCPCS", "service_category": "Cardiology"},
    "12 lead ecg with interpretation": {"code": "93000", "description": "Routine ECG with at least 12 leads, with interpretation and report", "code_system": "CPT/HCPCS", "service_category": "Cardiology"},
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
    "blood culture": "blood culture",
    "blood cultures": "blood culture",
    "urine culture": "urine culture",
    "urine cultures": "urine culture",
    "troponin": "troponin test",
    "troponin test": "troponin test",
    "complete transthoracic echocardiogram": "complete transthoracic echocardiogram",
    "complete transthoracic echo": "complete transthoracic echocardiogram",
    "transthoracic echocardiogram complete": "complete transthoracic echocardiogram",
    "12 lead ecg with interpretation": "12 lead ecg with interpretation",
    "12 lead ekg with interpretation": "12 lead ecg with interpretation",
    "12 lead electrocardiogram with interpretation": "12 lead ecg with interpretation",
}


def _service(code: str, description: str, category: str) -> dict[str, str]:
    return {"code": code, "description": description, "code_system": "CPT/HCPCS", "service_category": category}


# Services priced in the hospital chargemaster (OPD / IPD charge sheets) that the fallbacks above
# do not cover. Keys are the canonical names infer_documented_procedures emits and are matched
# exactly, so a generic mention ("MRI") never lands on a specific service ("MRI brain").
CHARGEMASTER_CPT_SERVICES = {
    "upper gi endoscopy": _service("43235", "Diagnostic upper GI endoscopy", "Endoscopy"),
    "colonoscopy": _service("45378", "Diagnostic colonoscopy", "Endoscopy"),
    "ct head": _service("70450", "CT head without contrast", "Radiology"),
    "mri brain": _service("70551", "MRI brain without contrast", "Radiology"),
    "ct chest with contrast": _service("71260", "CT chest with contrast", "Radiology"),
    "knee x ray": _service("73560", "X-ray exam of knee, 1 or 2 views", "Radiology"),
    "screening mammography": _service("77067", "Screening mammography, bilateral", "Radiology"),
    "prothrombin time": _service("85610", "Prothrombin time / INR", "Laboratory"),
    "psychotherapy": _service("90834", "Psychotherapy, 45 minutes", "Behavioral Health"),
    "hemodialysis": _service("90935", "Hemodialysis procedure", "Dialysis"),
    "ecg": _service("93005", "12-lead ECG, tracing only", "Cardiology"),
    "stress test": _service("93017", "Cardiovascular stress test, tracing only", "Cardiology"),
    "nebulizer treatment": _service("94640", "Nebulizer treatment", "Respiratory"),
    "therapeutic infusion": _service("96365", "Therapeutic IV infusion, initial hour", "Infusion"),
    "chemotherapy infusion": _service("96413", "Chemotherapy IV infusion, initial hour", "Chemotherapy"),
    "therapeutic exercise": _service("97110", "Therapeutic exercise, each 15 minutes", "Rehabilitation"),
    "physical therapy evaluation low complexity": _service("97161", "Physical therapy evaluation, low complexity", "Rehabilitation"),
    "physical therapy evaluation": _service("97162", "Physical therapy evaluation, moderate complexity", "Rehabilitation"),
    "occupational therapy evaluation": _service("97165", "Occupational therapy evaluation, low complexity", "Rehabilitation"),
    "nutrition assessment": _service("97802", "Medical nutrition therapy, initial assessment", "Nutrition"),
    "orthopedic fixation screw": _service("C1713", "Orthopedic fixation screw (implant)", "Implants"),
    "cefazolin injection": _service("J0690", "Cefazolin 1 g injection", "Pharmacy"),
    "vancomycin injection": _service("J3370", "Vancomycin 500 mg injection", "Pharmacy"),
    "normal saline infusion": _service("J7030", "Normal saline 1,000 mL", "Pharmacy"),
    **{
        f"office visit level {level}": _service(f"9921{level}", f"Established patient office visit, level {level}", "Evaluation and Management")
        for level in range(2, 6)
    },
    **{
        f"emergency department visit level {level}": _service(f"9928{level}", f"Emergency department visit, level {level}", "Evaluation and Management")
        for level in range(1, 6)
    },
}

_CHARGEMASTER_PHRASES = {
    "upper gi endoscopy": ("upper gi endoscopy", "upper endoscopy", "egd", "esophagogastroduodenoscopy"),
    "colonoscopy": ("colonoscopy",),
    "ct head": ("ct head", "head ct", "ct of the head", "ct of head", "ct scan of the head", "ct scan of head",
                "ct scan head", "ct brain", "computed tomography head"),
    "mri brain": ("mri brain", "brain mri", "mri of the brain", "mri of brain", "mri head"),
    "ct chest with contrast": ("ct chest with contrast", "chest ct with contrast", "ct of the chest with contrast",
                               "ct scan of the chest with contrast", "ct scan of chest with contrast", "contrast ct chest",
                               "contrast enhanced ct chest"),
    "knee x ray": ("knee x ray", "x ray of the knee", "x ray of knee", "x ray knee"),
    "screening mammography": ("mammogram", "mammography", "screening mammogram"),
    "prothrombin time": ("prothrombin time", "pt inr", "inr"),
    "psychotherapy": ("psychotherapy",),
    "hemodialysis": ("hemodialysis", "dialysis"),
    "ecg": ("ecg", "ekg", "electrocardiogram", "12 lead ecg", "12 lead ekg"),
    "stress test": ("stress test",),
    "nebulizer treatment": ("nebulizer", "nebuliser", "neb treatment", "nebulized"),
    "therapeutic infusion": ("therapeutic infusion", "iv infusion", "intravenous infusion", "infusion therapy"),
    "chemotherapy infusion": ("chemotherapy infusion", "chemo infusion", "chemotherapy"),
    "therapeutic exercise": ("therapeutic exercise", "therapeutic exercises"),
    "physical therapy evaluation low complexity": ("physical therapy evaluation low complexity",
                                                   "low complexity physical therapy evaluation",
                                                   "physical therapy initial evaluation low complexity",
                                                   "pt evaluation low complexity", "low complexity pt evaluation"),
    "physical therapy evaluation": ("physical therapy evaluation", "physiotherapy evaluation", "pt evaluation"),
    "occupational therapy evaluation": ("occupational therapy evaluation", "ot evaluation"),
    "nutrition assessment": ("nutrition assessment", "nutritional assessment", "dietitian assessment",
                             "dietary assessment", "medical nutrition therapy"),
    "orthopedic fixation screw": ("fixation screw", "fixation screws", "orthopedic screw", "orthopedic screws",
                                  "orthopaedic screw", "bone screw", "bone screws"),
    "cefazolin injection": ("cefazolin", "ancef"),
    "vancomycin injection": ("vancomycin",),
    "normal saline infusion": ("normal saline", "saline bolus", "ns bolus"),
    **{
        f"office visit level {level}": (f"office visit level {level}", f"clinic visit level {level}",
                                        f"outpatient visit level {level}", f"level {level} office visit",
                                        f"level {level} clinic visit", f"level {level} outpatient visit")
        for level in range(2, 6)
    },
    **{
        f"emergency department visit level {level}": (f"emergency department visit level {level}", f"ed visit level {level}",
                                                      f"er visit level {level}", f"emergency visit level {level}",
                                                      f"level {level} emergency department visit", f"level {level} ed visit",
                                                      f"level {level} er visit")
        for level in range(1, 6)
    },
}
DOCUMENTATION_PROCEDURE_ALIASES.update(
    {phrase: procedure for procedure, phrases in _CHARGEMASTER_PHRASES.items() for phrase in phrases}
)
DOCUMENTATION_PROCEDURE_ALIASES.update({
    "echocardiogram": "complete transthoracic echocardiogram",
    "transthoracic echocardiogram": "complete transthoracic echocardiogram",
    "tte": "complete transthoracic echocardiogram",
    "cmp": "comprehensive metabolic panel",
    "bmp": "basic metabolic panel",
})

# Generic mentions that a more specific service of the same kind replaces, e.g. "CT scan" when
# "CT head" is also documented, so one service is not billed twice under two codes.
GENERIC_PROCEDURE_TERMS = {
    "ct scan": "ct",
    "mri": "mri",
    "ultrasound": "ultrasound",
    "physical therapy": "physical therapy",
    "injection": "injection",
    "office visit": "visit",
    "established patient office visit": "visit",
}

_cpt_dataframe = None

PROCEDURE_KEYWORDS = ("x ray", "x-ray", "scan", "surgery", "biopsy", "therapy", "injection", "procedure", "endoscopy", "ultrasound", "mri", "ct", "cbc", "a1c", "hba1c", "blood test", "blood culture", "urine culture", "troponin", "office visit")

_PROCEDURE_STATUS_CUES = re.compile(
    r"\b(?:performed|completed|done|obtained|administered|collected|underwent|"
    r"given|infused|implanted|placed|inserted|ordered|order|requested|planned|plan|recommend(?:ed)?|consider(?:ed)?|"
    r"discuss(?:ed|ion)?|pending|declined|cancelled|canceled|history|historical|"
    r"previous|prior|previously|remote|not|never|without|no|possible|may|"
    r"needed|rule\s+out|results?|demonstrate|show|reveal|indicate)\b",
    re.IGNORECASE,
)
_NONPERFORMED_STATUS_PRIORITY = (
    "ordered_not_performed",
    "negated",
    "historical",
    "planned",
    "recommended",
    "discussed",
    "unknown",
)


def _canonicalize_procedure_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\b(?:c\s*\.?\s*x\s*\.?\s*r\.?)\b", "chest x ray", text)
    text = re.sub(r"\bchest\s+(?:x\s*[- ]?\s*ray|radiographs?)\b", "chest x ray", text)
    text = re.sub(r"\bx\s*[- ]?\s*ray\s+exam,?\s+chest\b", "chest x ray", text)
    return text


def _procedure_aliases(term: Any) -> tuple[str, ...]:
    normalized = normalize_text(_canonicalize_procedure_text(term))
    aliases = {normalized} if normalized else set()
    if normalized in {"chest x ray", "cxr", "chest radiograph", "chest radiographs"} or (
        "chest" in normalized and ("x ray" in normalized or "radiograph" in normalized)
    ):
        aliases.update({"chest x ray"})
    if normalized in {"cbc", "complete blood count"}:
        aliases.update({"cbc", "complete blood count"})
    if normalized in {"a1c", "a1c test", "hba1c", "hba1c test", "hemoglobin a1c"}:
        aliases.update({"a1c", "a1c test", "hba1c", "hba1c test", "hemoglobin a1c"})
    if normalized in {"office visit", "established patient office visit"}:
        aliases.update({"office visit", "patient evaluated", "patient was evaluated"})
    # Canonical service names ("ct head") are documented in other words ("head CT", "CT scan of the head").
    aliases.update(
        normalize_text(phrase) for phrase, procedure in DOCUMENTATION_PROCEDURE_ALIASES.items()
        if normalize_text(procedure) == normalized
    )
    return tuple(sorted(aliases, key=len, reverse=True))


def _contains_procedure_term(clause: str, aliases: tuple[str, ...]) -> bool:
    normalized_clause = normalize_text(_canonicalize_procedure_text(clause))
    return any(
        re.search(rf"\b{re.escape(alias)}\b", normalized_clause)
        for alias in aliases
    )


_STATUS_WINDOW_WORDS = 10


def _procedure_windows(clause: str, aliases: tuple[str, ...]) -> list[str]:
    """Words around each mention of the procedure, so a cue elsewhere in an unpunctuated
    transcript ("smoking history ... I recommend HbA1c test") does not set its status."""
    words = normalize_text(_canonicalize_procedure_text(clause)).split()
    windows = []
    for alias in aliases:
        alias_words = alias.split()
        size = len(alias_words)
        for start in range(len(words) - size + 1):
            if words[start:start + size] == alias_words:
                windows.append(" ".join(words[max(0, start - _STATUS_WINDOW_WORDS):start + size + _STATUS_WINDOW_WORDS]))
    return windows


def _contains_any_procedure(clause: str) -> bool:
    normalized_clause = normalize_text(_canonicalize_procedure_text(clause))
    known_terms = {
        *PROCEDURE_KEYWORDS,
        *COMMON_CPT_FALLBACKS,
        *ADDITIONAL_CPT_FALLBACKS,
        *DOCUMENTATION_PROCEDURE_ALIASES,
        "chest radiograph",
        "chest radiographs",
        "cxr",
    }
    return any(
        re.search(rf"\b{re.escape(normalize_text(term))}\b", normalized_clause)
        for term in known_terms
        if normalize_text(term)
    )


def _split_procedure_context(text: str) -> list[str]:
    clauses = []
    for sentence in re.split(r"(?<=[.!?])\s+|[;\n]+", text):
        sentence = sentence.strip()
        if not sentence:
            continue

        parts = []
        start = 0
        separators = re.finditer(r",|\b(?:but|however|whereas|while|and|or)\b", sentence, re.IGNORECASE)
        for separator in separators:
            left = sentence[start:separator.start()]
            right = sentence[separator.end():]
            if (
                _contains_any_procedure(left)
                and _contains_any_procedure(right)
                and _PROCEDURE_STATUS_CUES.search(left)
                and _PROCEDURE_STATUS_CUES.search(right)
            ):
                parts.append(left)
                start = separator.end()
        parts.append(sentence[start:])
        clauses.extend(part.strip() for part in parts if part.strip())
    return clauses


def _classify_procedure_clause(clause: str) -> str:
    text = normalize_text(_canonicalize_procedure_text(clause))

    if re.search(r"\b(?:declined|cancelled|canceled|pending|scheduled)\b", text):
        return "ordered_not_performed"
    if re.search(
        r"\b(?:no|not|never|without)\b.{0,45}\b(?:performed|completed|done|obtained|carried out)\b"
        r"|\b(?:not|never)\s+(?:been\s+)?(?:performed|completed|done|obtained|carried out)\b"
        r"|\b(?:no|without)\b.{0,35}\b(?:chest x ray|cbc|complete blood count|a1c|hba1c|"
        r"ct scan|mri|ultrasound|blood test|office visit)\b",
        text,
    ):
        return "ordered_not_performed" if re.search(r"\b(?:ordered|requested)\b", text) else "negated"
    if re.search(
        r"\b(?:history|historical|previous|prior|previously|remote|last year|years ago|in the past)\b",
        text,
    ):
        return "historical"
    if re.search(r"\b(?:recommend|recommended|recommendation)\b", text):
        return "recommended"
    if re.search(r"\b(?:discuss|discussed|discussion)\b", text):
        return "discussed"
    if re.search(
        r"\b(?:plan|planned|planning|possible|possibly|may be needed|might need|rule out|consider|considered)\b",
        text,
    ):
        return "planned"

    performed_action = re.search(
        r"\b(?:performed|completed|done|obtained|administered|collected|underwent|"
        r"given|infused|implanted|placed|inserted)\b",
        text,
    )
    result_evidence = re.search(
        r"\bresults?\b.{0,45}\b(?:demonstrate|demonstrates|demonstrated|show|shows|showed|"
        r"reveal|reveals|revealed|indicate|indicates|indicated|read|interpreted|reported)\b",
        text,
    )
    encounter_evaluation = re.search(r"\bpatient (?:was )?evaluated\b", text)
    # A lab reported with a measured value ("HbA1c was 8.2%") was run, but "A1c in 3 months" is not a result.
    lab_value = re.search(
        r"\b(?:hemoglobin a1c|hba1c|a1c|cbc|complete blood count|troponin|blood glucose|inr|prothrombin time)"
        r"(?: (?:test|level|result|value)){0,2}(?: (?:is|was|of|at|came back(?: at)?))? \d+(?: \d+)?"
        r"(?! (?:days?|weeks?|months?|years?|times?)\b)",
        text,
    )
    if performed_action or result_evidence or encounter_evaluation or lab_value:
        return "performed"
    if re.search(r"\b(?:ordered|requested)\b", text):
        return "ordered_not_performed"
    return "unknown"


def classify_procedure_status(term: Any, evidence: str = "") -> str:
    """Conservatively classify a procedure mention using its local text context."""
    aliases = _procedure_aliases(term)
    if not aliases or not str(evidence or "").strip():
        return "unknown"

    statuses = [
        _classify_procedure_clause(window)
        for clause in _split_procedure_context(_canonicalize_procedure_text(evidence))
        for window in _procedure_windows(clause, aliases)
    ]
    if "performed" in statuses:
        return "performed"
    return next(
        (status for status in _NONPERFORMED_STATUS_PRIORITY if status in statuses),
        "unknown",
    )


def classify_medical_item(item: Any) -> str | None:
    normalized = normalize_text(item)
    return "cpt" if any(keyword in normalized for keyword in PROCEDURE_KEYWORDS) else None


def is_explicitly_performed(term: Any, evidence: str = "") -> bool:
    return classify_procedure_status(term, evidence) == "performed"


def extract_performed_procedures(documentation: str) -> list[str]:
    """Return procedure keywords whose local documentation context says performed."""
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
    """Infer candidate services from documentation; this does not validate performance."""
    remaining = normalize_text(_canonicalize_procedure_text(documentation))
    inferred = []
    for phrase, procedure in sorted(DOCUMENTATION_PROCEDURE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        pattern = rf"\b{re.escape(normalize_text(phrase))}\b"
        if re.search(pattern, remaining):
            # Mask the match so a shorter phrase inside it ("ecg" in "12 lead ecg with interpretation") is not inferred too.
            remaining = re.sub(pattern, " ", remaining)
            if procedure not in inferred:
                inferred.append(procedure)
    return inferred


def drop_superseded_procedures(items: list[str]) -> list[str]:
    """Drop generic mentions ("CT scan") when a specific service of the same kind ("ct head") is present."""
    normalized = [normalize_text(item) for item in items]
    kept = []
    for item, key in zip(items, normalized):
        token = GENERIC_PROCEDURE_TERMS.get(key)
        if token and any(
            other != key and other not in GENERIC_PROCEDURE_TERMS and re.search(rf"\b{token}\b", other)
            for other in normalized
        ):
            continue
        kept.append(item)
    return kept


def _chargemaster_service(item: str) -> dict[str, Any] | None:
    service = CHARGEMASTER_CPT_SERVICES.get(normalize_text(item))
    if service is None:
        return None
    return {"extracted_item": item, "code": service["code"], "description": service["description"], "_score": 100.0, "source": "chargemaster"}


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
    items = drop_superseded_procedures(normalize_detected_procedures(detected_procedures))
    logger.debug("CPT raw input count=%d", len(items))
    if not items:
        return []
    dataframe = load_cpt_data()
    threshold = float(threshold)
    max_matches = max(1, int(max_matches))
    results = []
    for item in items:
        selected = _chargemaster_service(item)
        if selected is None:
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
    items = drop_superseded_procedures(normalize_detected_procedures(performed_procedures))
    dataframe = load_cpt_data()
    results = []
    for item in items:
        selected = _chargemaster_service(item)
        matches = [selected] if selected else _dataset_matches(item, dataframe, float(threshold), top_n)
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
