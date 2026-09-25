import json
import logging
import re
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

ICD10_FILE_NAMES = [
    "ICD10CM_2022_Codes.json",
    "icd10.json",
    "icd10_codes.json",
]

CPT_FILE_NAMES = [
    "CPT_CODES.json",
    "structured_cpt_hcpcs_2026.json",
    "cpt_codes.json",
]

DEFAULT_THRESHOLD = 90
DEFAULT_MAX_MATCHES = 1
DEFAULT_TOP_CANDIDATES = 5
MIN_COMPATIBLE_SCORE = 72
MIN_SCORE_MARGIN = 5

DIAGNOSIS_KEYWORDS = [
    "fever",
    "infection",
    "diagnosis",
    "diabetes",
    "hypertension",
    "asthma",
    "cough",
    "pain",
    "malaria",
    "typhoid",
    "disease",
    "injury",
    "syndrome",
    "arthritis",
    "pneumonia",
    "anemia",
    "sepsis",
    "tuberculosis",
    "leprosy",
    "stroke",
    "headache",
    "fatigue",
    "nausea",
    "alcohol use",
    "smoking history",
    "chronic cough",
    "chest pain",
]

PROCEDURE_KEYWORDS = [
    "x ray",
    "x-ray",
    "imaging",
    "scan",
    "surgery",
    "operation",
    "biopsy",
    "therapy",
    "consult",
    "evaluation",
    "assessment",
    "injection",
    "debridement",
    "catheter",
    "drainage",
    "repair",
    "procedure",
    "endoscopy",
    "intubation",
    "cauter",
    "excision",
    "suturing",
    "ultrasound",
    "mri",
    "ct",
    "radiograph",
    "ecg",
    "electrocardiogram",
    "physical therapy",
    "blood test",
    "lab test",
    "complete blood count",
    "cbc",
    "blood count",
    "blood count test",
    "blood glucose",
    "a1c",
    "hemoglobin a1c",
    "hemoglobin a1",
    "hba1c",
    "hba1c test",
    "a1c test",
    "a1 c test",
    "sputum",
    "chest x ray",
    "ct scan",
    "abdominal ultrasound",
    "physical therapy",
]

COMMON_CPT_FALLBACKS = {
    "chest x ray 2 views": {
        "code": "71046",
        "description": "X-ray exam, chest, 2 views",
        "code_system": "CPT/HCPCS",
        "service_category": "Radiology",
    },
    "x ray exam chest 2 views": {
        "code": "71046",
        "description": "X-ray exam, chest, 2 views",
        "code_system": "CPT/HCPCS",
        "service_category": "Radiology",
    },
    "chest x ray": {
        "code": "71045",
        "description": "X-ray exam, chest, 1 view",
        "code_system": "CPT/HCPCS",
        "service_category": "Radiology",
    },
    "x ray exam chest 1 view": {
        "code": "71045",
        "description": "X-ray exam, chest, 1 view",
        "code_system": "CPT/HCPCS",
        "service_category": "Radiology",
    },
    "ct scan of chest": {
        "code": "71250",
        "description": "CT scan of chest without contrast",
        "code_system": "CPT/HCPCS",
        "service_category": "Radiology",
    },
    "ct chest": {
        "code": "71250",
        "description": "CT scan of chest without contrast",
        "code_system": "CPT/HCPCS",
        "service_category": "Radiology",
    },
    "complete blood count": {
        "code": "85025",
        "description": "Blood count; complete (CBC), automated",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "blood count": {
        "code": "85025",
        "description": "Blood count; complete (CBC), automated",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "blood count test": {
        "code": "85025",
        "description": "Blood count; complete (CBC), automated",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "cbc": {
        "code": "85025",
        "description": "Blood count; complete (CBC), automated",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "cbc test": {
        "code": "85025",
        "description": "Blood count; complete (CBC), automated",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "blood glucose test": {
        "code": "82947",
        "description": "Glucose; blood, reagent strip",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "blood glucose": {
        "code": "82947",
        "description": "Glucose; blood, reagent strip",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "hemoglobin a1c test": {
        "code": "83036",
        "description": "Hemoglobin A1c",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "hemoglobin a1 test": {
        "code": "83036",
        "description": "Hemoglobin A1c",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "hba1c": {
        "code": "83036",
        "description": "Hemoglobin A1c",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "hba1c test": {
        "code": "83036",
        "description": "Hemoglobin A1c",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "a1c test": {
        "code": "83036",
        "description": "Hemoglobin A1c",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "a1 c test": {
        "code": "83036",
        "description": "Hemoglobin A1c",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "sputum laboratory test": {
        "code": "87070",
        "description": "Culture, bacterial; any source except urine, blood or stool",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "sputum culture": {
        "code": "87070",
        "description": "Culture, bacterial; any source except urine, blood or stool",
        "code_system": "CPT/HCPCS",
        "service_category": "Laboratory",
    },
    "abdominal ultrasound": {
        "code": "76700",
        "description": "Ultrasound, abdomen, complete",
        "code_system": "CPT/HCPCS",
        "service_category": "Radiology",
    },
    "ultrasound abdomen": {
        "code": "76700",
        "description": "Ultrasound, abdomen, complete",
        "code_system": "CPT/HCPCS",
        "service_category": "Radiology",
    },
    "physical therapy": {
        "code": "97110",
        "description": "Therapeutic exercises to develop strength and endurance, range of motion and flexibility",
        "code_system": "CPT/HCPCS",
        "service_category": "Rehabilitation",
    },
    "physical therapy treatment": {
        "code": "97110",
        "description": "Therapeutic exercises to develop strength and endurance, range of motion and flexibility",
        "code_system": "CPT/HCPCS",
        "service_category": "Rehabilitation",
    },
    "established patient office visit": {
        "code": "99213",
        "description": "Established patient office or other outpatient visit, 15 minutes",
        "code_system": "CPT/HCPCS",
        "service_category": "Evaluation and Management",
    },
    "office visit": {
        "code": "99213",
        "description": "Established patient office or other outpatient visit, 15 minutes",
        "code_system": "CPT/HCPCS",
        "service_category": "Evaluation and Management",
    },
}

COMMON_ICD10_FALLBACKS = {
    "diabetes type 2": {
        "code": "E11.9",
        "description": "Type 2 diabetes mellitus without complications",
    },
    "type 2 diabetes mellitus": {
        "code": "E11.9",
        "description": "Type 2 diabetes mellitus without complications",
    },
    "hypertension": {
        "code": "I10",
        "description": "Essential (primary) hypertension",
    },
}

RUNTIME_ALIASES = {
    "diabetes type 2": "type 2 diabetes mellitus",
    "type ii diabetes": "type 2 diabetes mellitus",
    "t2dm": "type 2 diabetes mellitus",
    "dm2": "type 2 diabetes mellitus",
    "diabetes type 1": "type 1 diabetes mellitus",
    "type i diabetes": "type 1 diabetes mellitus",
    "t1dm": "type 1 diabetes mellitus",
    "dm1": "type 1 diabetes mellitus",
    "htn": "hypertension",
    "sob": "shortness of breath",
    "pyrexia": "fever",
}

ICD_FAMILY_FILTERS = {
    "type 1 diabetes mellitus": ("E10",),
    "type 2 diabetes mellitus": ("E11",),
    "fever": ("R50",),
    "headache": ("R51",),
    "hypertension": ("I10", "I11", "I12", "I13"),
    "chest pain": ("R07",),
    "abdominal pain": ("R10",),
    "cough": ("R05",),
    "shortness of breath": ("R06",),
    "asthma": ("J45",),
    "pneumonia": tuple(f"J{code}" for code in range(12, 19)),
    "knee pain": ("M25.56",),
}

DEFAULT_SYMPTOM_CODES = {
    "fever": ("R50.9", "Fever, unspecified"),
    "headache": ("R51", "Headache"),
    "cough": ("R05.9", "Cough, unspecified"),
    "chest pain": ("R07.9", "Chest pain, unspecified"),
    "abdominal pain": ("R10.9", "Unspecified abdominal pain"),
    "shortness of breath": ("R06.02", "Shortness of breath"),
    "knee pain": ("M25.569", "Pain in unspecified knee"),
}

GENERIC_SYMPTOMS = {
    "fever", "headache", "cough", "chest pain", "abdominal pain",
    "shortness of breath", "nausea", "fatigue",
}

NEGATED_OR_NONACTIVE_PATTERNS = (
    r"\bno\s+(?:evidence of\s+)?{term}\b",
    r"\bdenies\s+{term}\b",
    r"\bwithout\s+{term}\b",
    r"\b(?:rule[d]?\s*out|r/o|suspected|possible)\s+{term}\b",
    r"\b(?:family history of|mother has|father has|sibling has)\s+{term}\b",
    r"\bhistory of\s+{term}\b",
)

PERFORMED_PROCEDURE_PATTERNS = (
    r"\b(?:performed|completed|done|obtained|administered|collected|read|interpreted)\b[^.\n]*\b{term}\b",
    r"\b{term}\b[^.\n]*\b(?:performed|completed|done|obtained|administered|collected|read|interpreted)\b",
)


# ============================================================
# DATASET CACHE
# ============================================================

_icd10_dataframe = None
_cpt_dataframe = None


# ============================================================
# FILE LOCATION
# ============================================================

def find_json_file(file_names):
    """
    Locate the first available JSON file.
    """

    search_directories = [
        BASE_DIR,
        BASE_DIR / "data",
        PROJECT_DIR,
        PROJECT_DIR / "data",
        Path.cwd(),
        Path.cwd() / "data",
    ]

    for directory in search_directories:
        for file_name in file_names:
            file_path = directory / file_name

            if file_path.is_file():
                print(f"JSON file found: {file_path}")
                return file_path

    print(f"JSON file not found. Expected one of: {file_names}")

    return None


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_column_name(value):
    """
    Normalize a column name for flexible comparison.
    """

    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value).strip().lower(),
    )


def normalize_text(value):
    """
    Normalize text before fuzzy matching.
    """

    if value is None:
        return ""

    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_clinical_term(value):
    """Normalize a clinical term without changing the source JSON or source text."""
    normalized = normalize_text(value)
    return RUNTIME_ALIASES.get(normalized, normalized)


def get_record_value(record, key_variants):
    """Read a value using only the key variants supported by the loaded dataset."""
    normalized_keys = {
        normalize_column_name(key): key
        for key in record
    }
    for key in key_variants:
        actual_key = normalized_keys.get(normalize_column_name(key))
        if actual_key is not None and record.get(actual_key) is not None:
            return record[actual_key]
    return ""


def get_icd_code(record):
    return normalize_code(get_record_value(record, ("icd10_code", "icd10cm_code", "icd_code", "code")))


def get_icd_description(record):
    return str(get_record_value(record, ("description", "long_description", "short_description"))).strip()


def get_cpt_code(record):
    return normalize_code(get_record_value(record, ("code", "cpt_code", "hcpcs_code", "cpt_hcpcs_code")))


def get_cpt_description(record):
    return str(get_record_value(record, ("description", "procedure_description", "service_description", "long_description", "short_description"))).strip()


def is_nonactive_context(term, evidence=""):
    normalized_term = normalize_clinical_term(term)
    normalized_evidence = normalize_text(evidence)
    if not normalized_evidence:
        return False
    terms = [normalized_term]
    if "diabetes" in normalized_term:
        terms.append("diabetes")
    if "headache" in normalized_term:
        terms.append("headache")
    return any(
        re.search(pattern.format(term=re.escape(candidate_term)), normalized_evidence)
        for candidate_term in terms
        for pattern in NEGATED_OR_NONACTIVE_PATTERNS
    )


def is_explicitly_performed(term, evidence=""):
    normalized_term = normalize_text(term)
    normalized_evidence = normalize_text(evidence)
    if not normalized_evidence or is_nonactive_context(term, evidence):
        return False
    if normalized_term in {"office visit", "established patient office visit"}:
        return True
    return any(
        re.search(pattern.format(term=re.escape(normalized_term)), normalized_evidence)
        for pattern in PERFORMED_PROCEDURE_PATTERNS
    )


def is_planned_diagnostic_test(term, evidence=""):
    """Return true for diagnostic tests ordered or recommended in the note."""
    normalized_term = normalize_text(term)
    normalized_evidence = normalize_text(evidence)
    if not normalized_evidence or is_nonactive_context(term, evidence):
        return False
    if normalized_term not in {
        "a1c", "a1c test", "hba1c", "hba1c test", "hemoglobin a1c",
        "chest x ray", "chest x ray 2 views", "ct scan", "ct scan of head without contrast",
    }:
        return False
    return bool(re.search(
        r"\b(?:order|ordered|ordering|recommend|recommended|suggest|suggested|complete|completing|undergo|undergoing)\b"
        r"[^.\n]*\b(?:a1c|hba1c|hemoglobin a1c|x[- ]?ray|ct scan)\b",
        normalized_evidence,
    ))


def canonicalize_documented_procedure(term, documentation):
    """Use nearby documentation to disambiguate generic imaging terms."""
    normalized_term = normalize_text(term)
    normalized_documentation = normalize_text(documentation)
    if normalized_term in {"ct scan", "ct"} and "head" in normalized_documentation:
        return "ct scan of head without contrast"
    if normalized_term in {"chest x ray", "x ray"} and "two view" in normalized_documentation:
        return "chest x ray 2 views"
    return term


def family_filter_for(term):
    return ICD_FAMILY_FILTERS.get(normalize_clinical_term(term), ())


def normalize_code(value):
    """
    Normalize ICD-10, CPT, or HCPCS codes.
    """

    if value is None:
        return ""

    return str(value).strip().upper()


def normalize_threshold(threshold):
    """
    Accept threshold as either 0.60 or 60.
    """

    try:
        threshold = float(threshold)

    except (TypeError, ValueError):
        threshold = DEFAULT_THRESHOLD

    if threshold <= 1:
        threshold = threshold * 100

    return max(0, min(threshold, 100))


def normalize_detected_items(detected_items):
    """
    Convert the input into a clean list of strings.
    """

    if detected_items is None:
        return []

    if isinstance(detected_items, str):
        detected_items = [detected_items]

    elif isinstance(detected_items, dict):
        detected_items = [detected_items]

    elif not isinstance(detected_items, (list, tuple, set)):
        detected_items = [detected_items]

    supported_keys = [
        "condition",
        "diagnosis",
        "disease",
        "injury",
        "symptom",
        "procedure",
        "service",
        "test",
        "description",
        "text",
        "name",
        "item",
        "value",
    ]

    normalized_items = []
    seen = set()

    for item in detected_items:
        extracted_value = None

        if isinstance(item, str):
            extracted_value = item

        elif isinstance(item, dict):
            for key in supported_keys:
                if item.get(key):
                    extracted_value = item[key]
                    break

        elif item is not None:
            extracted_value = str(item)

        if extracted_value is None:
            continue

        extracted_value = " ".join(str(extracted_value).split())
        normalized_key = normalize_text(extracted_value)

        if normalized_key and normalized_key not in seen:
            seen.add(normalized_key)
            normalized_items.append(extracted_value)

    return normalized_items


def classify_medical_item(item):
    """
    Determine whether a detected item is diagnostic or procedural.
    """

    if item is None:
        return None

    text = normalize_clinical_term(str(item))
    if not text:
        return None

    if text in {"office visit", "established patient office visit"}:
        return "cpt"

    if any(keyword in text for keyword in DIAGNOSIS_KEYWORDS):
        return "icd10"

    if any(keyword in text for keyword in PROCEDURE_KEYWORDS):
        return "cpt"

    return None


# ============================================================
# JSON LOADING
# ============================================================

def read_json_records(file_path):
    """
    Read records from flat or nested JSON.
    """

    if file_path is None or not file_path.is_file():
        return []

    try:
        with open(
            file_path,
            "r",
            encoding="utf-8-sig",
        ) as file:
            data = json.load(file)

    except Exception as error:
        print(f"Error loading {file_path}: {error}")
        return []

    # Flat JSON list
    if isinstance(data, list):
        return [
            item
            for item in data
            if isinstance(item, dict)
        ]

    # Nested JSON object
    if isinstance(data, dict):
        possible_keys = [
            "codes",
            "records",
            "data",
            "items",
            "results",
            "icd10_codes",
            "cpt_codes",
            "hcpcs_codes",
        ]

        normalized_keys = {
            normalize_column_name(key): key
            for key in data.keys()
        }

        for possible_key in possible_keys:
            actual_key = normalized_keys.get(
                normalize_column_name(possible_key)
            )

            if actual_key is not None:
                records = data.get(actual_key)

                if isinstance(records, list):
                    return [
                        item
                        for item in records
                        if isinstance(item, dict)
                    ]

        # Dictionary format:
        # {"E11.9": "Type 2 diabetes mellitus"}
        records = []

        for code, value in data.items():
            if isinstance(value, str):
                records.append(
                    {
                        "code": code,
                        "description": value,
                    }
                )

            elif isinstance(value, dict):
                record = value.copy()
                record.setdefault("code", code)
                records.append(record)

        return records

    return []


def find_column(dataframe, possible_names):
    """
    Find a DataFrame column using flexible comparison.
    """

    available_columns = {
        normalize_column_name(column): column
        for column in dataframe.columns
    }

    for name in possible_names:
        normalized_name = normalize_column_name(name)

        if normalized_name in available_columns:
            return available_columns[normalized_name]

    return None


# ============================================================
# ICD-10 DATASET
# ============================================================

def load_icd10_data(force_reload=False):
    """
    Load and normalize the ICD-10 dataset.
    """

    global _icd10_dataframe

    if _icd10_dataframe is not None and not force_reload:
        return _icd10_dataframe

    file_path = find_json_file(ICD10_FILE_NAMES)
    records = read_json_records(file_path)

    if not records:
        print("No ICD-10 records were loaded.")
        return None

    dataframe = pd.DataFrame(records)

    code_column = find_column(
        dataframe,
        [
            "icd10_code",
            "icd10cm_code",
            "icd_code",
            "ICD-10 Code",
            "code",
        ],
    )

    description_column = find_column(
        dataframe,
        [
            "description",
            "long_description",
            "short_description",
            "diagnosis",
            "condition",
            "disease",
            "name",
            "title",
        ],
    )

    if code_column is None or description_column is None:
        print(
            "ICD-10 columns not found. "
            f"Available columns: {list(dataframe.columns)}"
        )
        return None

    dataframe = dataframe.rename(
        columns={
            code_column: "code",
            description_column: "description",
        }
    )

    dataframe["code"] = (
        dataframe["code"]
        .fillna("")
        .astype(str)
        .map(normalize_code)
    )

    dataframe["description"] = (
        dataframe["description"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    dataframe["search_text"] = (
        dataframe["description"]
        .map(normalize_text)
    )

    dataframe = dataframe[
        (dataframe["code"] != "")
        & (dataframe["description"] != "")
        & (dataframe["search_text"] != "")
    ].copy()

    dataframe.drop_duplicates(
        subset=["code"],
        keep="first",
        inplace=True,
    )

    dataframe.reset_index(drop=True, inplace=True)

    _icd10_dataframe = dataframe

    print(f"Loaded {len(dataframe)} unique ICD-10 codes.")

    return _icd10_dataframe


# ============================================================
# CPT/HCPCS DATASET
# ============================================================

def load_cpt_data(force_reload=False):
    """
    Load and normalize the CPT/HCPCS dataset.

    The function searches for CPT_CODES.json first.
    """

    global _cpt_dataframe

    if _cpt_dataframe is not None and not force_reload:
        return _cpt_dataframe

    file_path = find_json_file(CPT_FILE_NAMES)
    records = read_json_records(file_path)

    if not records:
        print("No CPT/HCPCS records were loaded.")
        return None

    dataframe = pd.DataFrame(records)

    code_column = find_column(
        dataframe,
        [
            "Code",
            "cpt_code",
            "hcpcs_code",
            "cpt_hcpcs_code",
            "procedure_code",
            "code",
        ],
    )

    description_column = find_column(
        dataframe,
        [
            "Description",
            "procedure_description",
            "service_description",
            "long_description",
            "short_description",
            "name",
            "title",
        ],
    )

    category_column = find_column(
        dataframe,
        [
            "Service Category",
            "service_category",
            "category",
        ],
    )

    code_system_column = find_column(
        dataframe,
        [
            "Code System",
            "code_system",
            "code_type",
            "system",
        ],
    )

    if code_column is None or description_column is None:
        print(
            "CPT/HCPCS columns not found. "
            f"Available columns: {list(dataframe.columns)}"
        )
        return None

    rename_columns = {
        code_column: "code",
        description_column: "description",
    }

    if category_column is not None:
        rename_columns[category_column] = "service_category"

    if code_system_column is not None:
        rename_columns[code_system_column] = "code_system"

    dataframe = dataframe.rename(columns=rename_columns)

    if "service_category" not in dataframe.columns:
        dataframe["service_category"] = ""

    if "code_system" not in dataframe.columns:
        dataframe["code_system"] = "CPT/HCPCS"

    dataframe["code"] = (
        dataframe["code"]
        .fillna("")
        .astype(str)
        .map(normalize_code)
    )

    dataframe["description"] = (
        dataframe["description"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    dataframe["service_category"] = (
        dataframe["service_category"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    dataframe["code_system"] = (
        dataframe["code_system"]
        .fillna("CPT/HCPCS")
        .astype(str)
        .str.strip()
    )

    dataframe["search_text"] = (
        dataframe["description"]
        .map(normalize_text)
    )

    dataframe = dataframe[
        (dataframe["code"] != "")
        & (dataframe["description"] != "")
        & (dataframe["search_text"] != "")
    ].copy()

    # Remove duplicate CPT/HCPCS codes.
    dataframe.drop_duplicates(
        subset=["code"],
        keep="first",
        inplace=True,
    )

    dataframe.reset_index(drop=True, inplace=True)

    _cpt_dataframe = dataframe

    print(f"Loaded {len(dataframe)} unique CPT/HCPCS codes.")

    return _cpt_dataframe


# ============================================================
# GENERIC MATCHING
# ============================================================

def match_dataset(
    detected_items,
    dataframe,
    threshold=DEFAULT_THRESHOLD,
    max_matches=DEFAULT_MAX_MATCHES,
):
    """
    Match input items against a dataset while keeping the output narrow,
    deterministic, and non-hallucinatory.
    """

    if dataframe is None or dataframe.empty:
        return []

    detected_items = normalize_detected_items(detected_items)
    if not detected_items:
        return []

    threshold = normalize_threshold(threshold)
    try:
        max_matches = max(1, int(max_matches))
    except (TypeError, ValueError):
        max_matches = DEFAULT_MAX_MATCHES

    if threshold < 60:
        threshold = 80

    descriptions = dataframe["search_text"].tolist()
    final_results = []

    for detected_item in detected_items:
        normalized_query = normalize_text(detected_item)
        normalized_query_code = normalize_code(detected_item)

        matches_for_item = []

        # Prefer exact or phrase-level matches before fuzzy matches.
        exact_code_rows = dataframe[dataframe["code"].eq(normalized_query_code)]
        for _, row in exact_code_rows.iterrows():
            matches_for_item.append({
                "extracted_item": detected_item,
                "code": row["code"],
                "description": row["description"],
                "service_category": row.get("service_category", ""),
                "code_system": row.get("code_system", ""),
                "_score": 100.0,
            })

        if not matches_for_item:
            keyword_matches = dataframe[
                dataframe["search_text"].eq(normalized_query)
                | dataframe["search_text"].str.startswith(normalized_query + " ")
                | dataframe["search_text"].str.endswith(" " + normalized_query)
            ]

            for _, row in keyword_matches.head(max_matches).iterrows():
                matches_for_item.append({
                    "extracted_item": detected_item,
                    "code": row["code"],
                    "description": row["description"],
                    "service_category": row.get("service_category", ""),
                    "code_system": row.get("code_system", ""),
                    "_score": 95.0,
                })

        if not matches_for_item:
            fuzzy_matches = process.extract(
                normalized_query,
                descriptions,
                scorer=fuzz.WRatio,
                score_cutoff=threshold,
                limit=max_matches,
            )

            for _, score, row_index in fuzzy_matches:
                row = dataframe.iloc[row_index]
                matches_for_item.append({
                    "extracted_item": detected_item,
                    "code": row["code"],
                    "description": row["description"],
                    "service_category": row.get("service_category", ""),
                    "code_system": row.get("code_system", ""),
                    "_score": float(score),
                })

        unique_matches = {}
        for match in matches_for_item:
            key = (normalize_text(match["extracted_item"]), normalize_code(match["code"]))
            if key not in unique_matches:
                unique_matches[key] = match
            elif match["_score"] > unique_matches[key]["_score"]:
                unique_matches[key] = match

        sorted_matches = sorted(
            unique_matches.values(),
            key=lambda item: item["_score"],
            reverse=True,
        )
        final_results.extend(sorted_matches[:max_matches])

    deduplicated_results = []
    seen_results = set()
    for result in final_results:
        result_key = (
            normalize_text(result["extracted_item"]),
            normalize_code(result["code"]),
        )
        if result_key not in seen_results:
            seen_results.add(result_key)
            result.pop("_score", None)
            deduplicated_results.append(result)

    return deduplicated_results


# ============================================================
# ICD-10 MATCHING
# ============================================================

def _row_payload(row, code_key, description_key):
    return {
        "code": normalize_code(row.get(code_key, "")),
        "description": str(row.get(description_key, "")).strip(),
    }


def _score_candidate(query, description, generic_symptom=False):
    query = normalize_clinical_term(query)
    description = normalize_text(description)
    if not query or not description:
        return 0.0
    ratio = fuzz.ratio(query, description)
    token_ratio = fuzz.token_ratio(query, description)
    token_set_ratio = fuzz.token_set_ratio(query, description)
    containment = 100.0 if query == description else 0.0
    if query in description and query != description:
        containment = 86.0
    score = (0.35 * ratio) + (0.25 * token_ratio) + (0.25 * token_set_ratio) + (0.15 * containment)
    if generic_symptom and description != query:
        score -= 10.0
    return round(max(0.0, min(score, 100.0)), 2)


def _filter_icd_family(dataframe, normalized_term):
    families = family_filter_for(normalized_term)
    if not families:
        return dataframe
    filtered = dataframe[dataframe["code"].map(lambda code: any(code.startswith(prefix) for prefix in families))].copy()
    logger.debug("ICD family filter term=%s families=%s before=%d after=%d", normalized_term, families, len(dataframe), len(filtered))
    return filtered


def _candidate_rows(dataframe, query, top_n, generic_symptom=False):
    if dataframe is None or dataframe.empty:
        return []
    scored = []
    for _, row in dataframe.iterrows():
        code = normalize_code(row.get("code", ""))
        description = str(row.get("description", "")).strip()
        score = _score_candidate(query, description, generic_symptom=generic_symptom)
        if score:
            scored.append({
                "code": code,
                "description": description,
                "match_score": score,
                "clinical_compatibility": "compatible",
                "reason": "Matches the normalized documented concept within the allowed code family.",
            })
    return sorted(scored, key=lambda item: (-item["match_score"], item["code"]))[:top_n]


def _review_result(term, normalized_term, reason, alternatives=None):
    logger.debug("Coding review_required term=%s normalized=%s reason=%s", term, normalized_term, reason)
    return {
        "term": term,
        "normalized_term": normalized_term,
        "status": "review_required",
        "selected_code": None,
        "selected_description": None,
        "confidence": 0.0,
        "reason": reason,
        "candidates": [],
        "alternatives": alternatives or [],
    }


def _select_candidate(term, normalized_term, candidates, threshold):
    if not candidates:
        return _review_result(term, normalized_term, "No clinically compatible candidate was found.")
    top = candidates[0]
    second = candidates[1]["match_score"] if len(candidates) > 1 else 0.0
    if top["match_score"] < max(float(threshold), MIN_COMPATIBLE_SCORE):
        return _review_result(term, normalized_term, "The best candidate did not meet the configured confidence threshold.", candidates)
    if len(candidates) > 1 and top["match_score"] - second < MIN_SCORE_MARGIN:
        return _review_result(term, normalized_term, "Multiple candidates are too close to select safely.", candidates)
    logger.debug("Coding suggested term=%s code=%s score=%.2f", term, top["code"], top["match_score"])
    return {
        "term": term,
        "normalized_term": normalized_term,
        "status": "suggested",
        "selected_code": top["code"],
        "selected_description": top["description"],
        "confidence": round(top["match_score"] / 100.0, 3),
        "reason": top["reason"],
        "candidates": candidates,
        "alternatives": candidates[1:],
    }


def get_icd10_candidate_sets(documented_conditions, threshold=DEFAULT_THRESHOLD, top_n=DEFAULT_TOP_CANDIDATES, evidence_by_term=None):
    """Return conservative, validated ICD candidates for documented conditions."""
    items = normalize_detected_items(documented_conditions)
    dataframe = load_icd10_data()
    results = []
    evidence_by_term = evidence_by_term or {}
    for original_term in items:
        normalized_term = normalize_clinical_term(original_term)
        evidence = evidence_by_term.get(original_term, "")
        logger.debug("ICD term original=%s normalized=%s type=diagnosis_or_symptom", original_term, normalized_term)
        if is_nonactive_context(original_term, evidence):
            results.append(_review_result(original_term, normalized_term, "The documentation marks this condition as negated, historical, family history, or ruled out."))
            continue
        fallback = COMMON_ICD10_FALLBACKS.get(normalized_term)
        if fallback:
            candidate = {
                "code": fallback["code"],
                "description": fallback["description"],
                "match_score": 100.0,
                "clinical_compatibility": "compatible",
                "reason": "Canonical runtime alias matched a deterministic ICD-10 mapping.",
            }
            results.append(_select_candidate(original_term, normalized_term, [candidate], threshold))
            continue
        default_symptom = DEFAULT_SYMPTOM_CODES.get(normalized_term)
        if default_symptom:
            code, description = default_symptom
            family_prefixes = family_filter_for(normalized_term)
            if dataframe is not None and any(dataframe["code"].eq(code)) and any(code.startswith(prefix) for prefix in family_prefixes):
                candidate = {
                    "code": code,
                    "description": description,
                    "match_score": 100.0,
                    "clinical_compatibility": "compatible",
                    "reason": "Generic symptom mapped to its unspecified symptom family; no disease subtype was inferred.",
                }
                results.append(_select_candidate(original_term, normalized_term, [candidate], threshold))
                continue
        filtered = _filter_icd_family(dataframe, normalized_term)
        candidates = _candidate_rows(filtered, normalized_term, top_n, generic_symptom=normalized_term in GENERIC_SYMPTOMS)
        results.append(_select_candidate(original_term, normalized_term, candidates, threshold))
    return results


def extract_performed_procedures(documentation):
    """Extract procedure phrases only when the documentation says they were performed."""
    text = documentation or ""
    terms = []
    for term in PROCEDURE_KEYWORDS:
        if is_explicitly_performed(term, text):
            terms.append(term)
    normalized = list(dict.fromkeys(normalize_clinical_term(term) for term in terms))
    return normalized


def get_cpt_candidate_sets(performed_procedures, documentation="", threshold=DEFAULT_THRESHOLD, top_n=DEFAULT_TOP_CANDIDATES):
    """Return CPT candidates for performed services and planned A1C testing."""
    items = normalize_detected_items(performed_procedures)
    dataframe = load_cpt_data()
    results = []
    for original_term in items:
        original_term = canonicalize_documented_procedure(original_term, documentation)
        normalized_term = normalize_clinical_term(original_term)
        logger.debug("CPT term original=%s normalized=%s type=performed_procedure", original_term, normalized_term)

        has_documentation = bool((documentation or "").strip())
        is_documented_performed = (
            not has_documentation
            or is_explicitly_performed(original_term, documentation)
            or is_planned_diagnostic_test(original_term, documentation)
        )

        if has_documentation and not is_documented_performed:
            results.append(_review_result(original_term, normalized_term, "The documentation does not state that this service was performed."))
            continue

        fallback = COMMON_CPT_FALLBACKS.get(normalized_term)
        if fallback:
            candidates = [{
                "code": fallback["code"],
                "description": fallback["description"],
                "match_score": 100.0,
                "clinical_compatibility": "compatible",
                "reason": "Canonical runtime procedure mapping matched a documented or inferred billable service.",
            }]
        else:
            candidates = _candidate_rows(dataframe, normalized_term, top_n)
        results.append(_select_candidate(original_term, normalized_term, candidates, threshold))
    return results

def get_icd10_codes(
    detected_conditions,
    threshold=DEFAULT_THRESHOLD,
    max_matches=DEFAULT_MAX_MATCHES,
):
    """
    Return ICD-10 diagnosis matches without scores or methods.
    """

    detailed = get_icd10_candidate_sets(detected_conditions, threshold=threshold, top_n=max(DEFAULT_TOP_CANDIDATES, max_matches))
    return [
        {
            "Extracted Condition": item["term"],
            "Matched Disease/Injury": item["selected_description"],
            "ICD-10 Code": item["selected_code"],
        }
        for item in detailed
        if item["status"] == "suggested"
    ]


# ============================================================
# CPT/HCPCS MATCHING
# ============================================================

def get_cpt_codes(
    detected_procedures,
    threshold=DEFAULT_THRESHOLD,
    max_matches=DEFAULT_MAX_MATCHES,
):
    """
    Return CPT/HCPCS matches without scores or methods.

    This function uses the JSON dataset first, then a deterministic fallback map for
    the common clinical procedures the app is expected to recognize in consultation notes.
    """

    from cpt_coder import get_cpt_codes as split_get_cpt_codes

    return split_get_cpt_codes(
        detected_procedures,
        threshold=threshold,
        max_matches=max_matches,
    )

    items = normalize_detected_items(detected_procedures)
    if not items:
        return []

    dataframe = load_cpt_data()
    matches = match_dataset(
        detected_items=items,
        dataframe=dataframe,
        threshold=threshold,
        max_matches=max_matches,
    )

    matched_results = []
    matched_payloads = set()

    for match in matches:
        key = (normalize_text(match["extracted_item"]), normalize_code(match["code"]))
        if key not in matched_payloads:
            matched_payloads.add(key)
            matched_results.append({
                "Extracted Procedure": match["extracted_item"],
                "Matched Procedure/Service": match["description"],
                "CPT/HCPCS Code": match["code"],
            })

    # Fallback for common procedures that are expected in consultation notes but may not be present in the raw JSON.
    for item in items:
        normalized = normalize_text(item)
        fallback_entry = None

        for alias, payload in COMMON_CPT_FALLBACKS.items():
            if normalize_text(alias) == normalized:
                fallback_entry = payload
                break

        if fallback_entry is None:
            for alias, payload in COMMON_CPT_FALLBACKS.items():
                if normalize_text(alias) in normalized or normalized in normalize_text(alias):
                    fallback_entry = payload
                    break

        if fallback_entry is None:
            continue

        key = (normalize_text(item), normalize_code(fallback_entry["code"]))
        if key in matched_payloads:
            continue

        matched_payloads.add(key)
        matched_results.append({
            "Extracted Procedure": item,
            "Matched Procedure/Service": fallback_entry["description"],
            "CPT/HCPCS Code": fallback_entry["code"],
        })

    return matched_results


# ============================================================
# BACKWARD-COMPATIBLE FUNCTION
# ============================================================

def get_medical_codes(
    detected_items,
    threshold=DEFAULT_THRESHOLD,
    max_matches=DEFAULT_MAX_MATCHES,
    code_type=None,
):
    """
    Production-safe code routing for clinical insights.

    Rules:
    - diagnosis/symptom items are matched only to ICD-10
    - procedure/service items are matched only to CPT/HCPCS
    - ambiguous or unsupported item types are ignored
    - only the strongest match is returned per item by default
    """

    if code_type is not None and normalize_column_name(code_type) in {"cpt", "hcpcs", "cpthcpcs", "procedure", "service"}:
        from cpt_coder import get_cpt_codes as split_get_cpt_codes

        return split_get_cpt_codes(detected_items, threshold=threshold, max_matches=max_matches)

    items = normalize_detected_items(detected_items)
    if not items:
        return []

    if code_type is None:
        icd_items = []
        cpt_items = []

        for item in items:
            item_type = classify_medical_item(item)
            if item_type == "icd10":
                icd_items.append(item)
            elif item_type == "cpt":
                cpt_items.append(item)

        results = []
        if icd_items:
            results.extend(get_icd10_codes(
                detected_conditions=icd_items,
                threshold=threshold,
                max_matches=max_matches,
            ))
        if cpt_items:
            results.extend(get_cpt_codes(
                detected_procedures=cpt_items,
                threshold=threshold,
                max_matches=max_matches,
            ))
        return results

    code_type = normalize_column_name(code_type)

    if code_type in {"icd", "icd10", "icd10cm", "diagnosis", "condition"}:
        filtered_items = [
            item for item in items
            if classify_medical_item(item) == "icd10"
        ]
        return get_icd10_codes(
            detected_conditions=filtered_items,
            threshold=threshold,
            max_matches=max_matches,
        )

    if code_type in {"cpt", "hcpcs", "cpthcpcs", "procedure", "service"}:
        return []

    raise ValueError("code_type must be 'icd10' or 'cpt'.")


# ============================================================
# COMBINED ICD-10 AND CPT/HCPCS MATCHING
# ============================================================

def match_clinical_document(
    conditions=None,
    procedures=None,
    threshold=DEFAULT_THRESHOLD,
    max_matches=DEFAULT_MAX_MATCHES,
):
    """
    Match conditions against ICD-10 and procedures against CPT.
    """

    from cpt_coder import get_cpt_codes as split_get_cpt_codes

    return {
        "icd10_matches": get_icd10_codes(
            detected_conditions=conditions,
            threshold=threshold,
            max_matches=max_matches,
        ),
        "cpt_hcpcs_matches": split_get_cpt_codes(
            detected_procedures=procedures,
            threshold=threshold,
            max_matches=max_matches,
        ),
    }


# ============================================================
# SIMPLE TEST
# ============================================================

if __name__ == "__main__":
    icd_results = get_icd10_codes(
        detected_conditions=[
            "Tuberculosis",
            "Typhoid fever",
        ],
        threshold=60,
        max_matches=5,
    )

    cpt_results = get_cpt_codes(
        detected_procedures=[
            "Chest x-ray 2 views",
            "Breast ultrasound",
            "PSA screening",
        ],
        threshold=60,
        max_matches=5,
    )

    print("\nICD-10 RESULTS")
    print(
        json.dumps(
            icd_results,
            indent=2,
            ensure_ascii=False,
        )
    )

    print("\nCPT/HCPCS RESULTS")
    print(
        json.dumps(
            cpt_results,
            indent=2,
            ensure_ascii=False,
        )
    )