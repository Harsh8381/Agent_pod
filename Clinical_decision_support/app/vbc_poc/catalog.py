"""Rule catalog for the VBC POC: measures, states, workflow, sources and concept maps.

Everything that changes how a result is calculated lives here as configuration, versioned by
RULE_VERSION, so every stored result can say exactly which rules produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

RULE_VERSION = "POC-1.1"
RULE_CHANGE_LOG = (
    ("POC-1.0", "2026-09-01", "Initial four HEDIS-inspired prototypes: glycemic assessment, BP control in diabetes, breast and colorectal screening."),
    ("POC-1.1", "2026-10-01", "Same-date BP readings use lowest systolic and lowest diastolic; device-transmitted home BP accepted; "
                              "positive stool test creates a linked follow-up gap."),
)
PROGRAM = "Northstar ACO pilot (synthetic)"
POSITIONING = ("A longitudinal VBC workflow prototype that applies HEDIS-inspired quality rules to patient history, converts findings "
               "into actionable care gaps, manages closure, and shows measurable quality improvement.")
DISCLAIMER = ("Synthetic data · HEDIS-inspired prototype logic · not NCQA-certified, not official HEDIS reporting, "
              "not audit-ready. Risk scores rank workload; they are not validated clinical predictions.")


@dataclass(frozen=True)
class MeasurementPeriod:
    period_id: str
    start: date
    end: date


PERIOD = MeasurementPeriod("MP-2026", date(2026, 1, 1), date(2026, 12, 31))
HISTORY_START = date(2025, 11, 1)  # 12 months of synthetic history before the demo date

# ---------------------------------------------------------------------------
# Measure (calculation) states, workflow states and dispositions — three separate dimensions
# ---------------------------------------------------------------------------
NOT_ELIGIBLE = "NOT_ELIGIBLE"
DENOMINATOR_NOT_MET = "DENOMINATOR_NOT_MET"
EXCLUDED = "EXCLUDED"
MET = "MET"
OPEN_GAP = "OPEN_GAP"
OUTCOME_NOT_CONTROLLED = "OUTCOME_NOT_CONTROLLED"
EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
COMPLETED_PENDING_VERIFICATION = "COMPLETED_PENDING_VERIFICATION"
DATA_QUALITY_ISSUE = "DATA_QUALITY_ISSUE"
FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"


@dataclass(frozen=True)
class StateInfo:
    label: str
    meaning: str
    system_action: str
    tone: str
    in_denominator: bool
    numerator_met: bool
    actionable: bool


MEASURE_STATES = {
    NOT_ELIGIBLE: StateInfo("Not eligible", "Outside the broad prototype population.", "Not counted; no task.", "gray", False, False, False),
    DENOMINATOR_NOT_MET: StateInfo("Denominator not met", "Entered screening but fails a denominator rule (enrollment, attribution).",
                                   "Excluded from the rate with reason.", "gray", False, False, False),
    EXCLUDED: StateInfo("Excluded", "An accepted exclusion applies.", "Gap suppressed; exclusion evidence retained.", "gray", False, False, False),
    MET: StateInfo("Met", "Required service/outcome evidence satisfies the rule.", "No active gap; monitor.", "green", True, True, False),
    OPEN_GAP: StateInfo("Open care gap", "Eligible; required care not documented as completed.", "Create care task.", "red", True, False, True),
    OUTCOME_NOT_CONTROLLED: StateInfo("Outcome not controlled", "Assessment done; latest accepted result outside target.",
                                      "Create clinical follow-up task.", "peach", True, False, True),
    EVIDENCE_REVIEW: StateInfo("Evidence review", "Candidate evidence is incomplete, external, conflicting or unverified.",
                               "Create documentation-reconciliation task; avoid duplicate care.", "amber", True, False, True),
    COMPLETED_PENDING_VERIFICATION: StateInfo("Completed · pending verification", "Completion evidence arrived but has not passed checks.",
                                              "Route for verification; prevent premature closure.", "blue", True, False, True),
    DATA_QUALITY_ISSUE: StateInfo("Data-quality issue", "Key data is invalid or missing, so the result cannot be trusted.",
                                  "Create data-correction task; do not force a clinical result.", "violet", True, False, True),
    FOLLOW_UP_REQUIRED: StateInfo("Follow-up required", "Initial screening complete; a subsequent action remains.",
                                  "Create a linked follow-up gap.", "amber", True, True, True),
}

WORKFLOW = ("DETECTED", "REVIEWED", "ASSIGNED", "OUTREACH", "SCHEDULED", "COMPLETED", "VERIFIED", "CLOSED")
WORKFLOW_ROLES = {
    "DETECTED": ("VBC engine", "Measure result and source evidence"),
    "REVIEWED": ("Care coordinator", "Review decision and notes"),
    "ASSIGNED": ("Care manager", "Task assignment"),
    "OUTREACH": ("Outreach user", "Channel, timestamp and disposition"),
    "SCHEDULED": ("Scheduler", "Appointment or order reference"),
    "COMPLETED": ("Provider / system", "Procedure, observation or document"),
    "VERIFIED": ("Reviewer / engine", "Accepted evidence reference"),
    "CLOSED": ("VBC engine", "New measure result and closure reason"),
}
# Human-driven transitions. CLOSED is only ever reached by the engine after re-evaluation.
ALLOWED_TRANSITIONS = {
    "DETECTED": ("REVIEWED",),
    "REVIEWED": ("ASSIGNED",),
    "ASSIGNED": ("OUTREACH", "SCHEDULED"),
    "OUTREACH": ("SCHEDULED", "ASSIGNED"),
    "SCHEDULED": ("ASSIGNED",),
    "COMPLETED": ("ASSIGNED",),
    "VERIFIED": (),
    "CLOSED": (),
}
DISPOSITIONS = (
    "UNABLE_TO_CONTACT", "PATIENT_DECLINED", "CANCELLED", "NO_SHOW", "EXTERNAL_EVIDENCE_PENDING", "NOT_CLINICALLY_APPROPRIATE",
)

GAP_TYPES = {
    "CLINICAL": "Clinical care opportunity",
    "DOCUMENTATION": "Documentation / evidence reconciliation",
    "OUTCOME": "Outcome management",
    "DATA_CORRECTION": "Data correction",
    "FOLLOW_UP": "Linked follow-up",
}
STATE_GAP_TYPE = {
    OPEN_GAP: "CLINICAL",
    EVIDENCE_REVIEW: "DOCUMENTATION",
    COMPLETED_PENDING_VERIFICATION: "DOCUMENTATION",
    OUTCOME_NOT_CONTROLLED: "OUTCOME",
    DATA_QUALITY_ISSUE: "DATA_CORRECTION",
}
# Due-date rule (days from detection) and owner role by gap type.
DUE_DAYS = {"CLINICAL": 30, "DOCUMENTATION": 14, "OUTCOME": 14, "DATA_CORRECTION": 7, "FOLLOW_UP": 21}
OWNER_ROLES = {"CLINICAL": "Care manager", "DOCUMENTATION": "Care coordinator", "OUTCOME": "Physician / APP",
               "DATA_CORRECTION": "Data steward", "FOLLOW_UP": "Care manager"}
HIGH_RISK_DUE_DAYS = 7

# ---------------------------------------------------------------------------
# Sources and provenance (lower rank wins when duplicates or conflicts need a primary)
# ---------------------------------------------------------------------------
SOURCES = {
    "lab": ("Lab interface (HL7 ORU)", 1),
    "ehr": ("EHR structured record", 2),
    "patient_device": ("Device-transmitted home reading", 3),
    "external_report": ("External report (scanned / faxed)", 4),
    "claim": ("Claim (X12 837)", 5),
    "workflow_upload": ("Closure evidence attached in workflow", 6),
    "narrative": ("SOAP / free-text narrative", 9),
}

# Raw code -> canonical concept.
CONCEPT_CODES = {
    ("LOINC", "4548-4"): "hba1c", ("LOINC", "17856-6"): "hba1c", ("CPT", "83036"): "hba1c",
    ("LOINC", "85354-9"): "bp", ("LOINC", "55284-4"): "bp",
    ("CPT", "77067"): "mammogram", ("CPT", "77066"): "mammogram", ("CPT", "77065"): "mammogram", ("CPT", "77063"): "mammogram",
    ("HCPCS", "G0202"): "mammogram",
    ("CPT", "76641"): "breast_ultrasound", ("CPT", "76642"): "breast_ultrasound",
    ("CPT", "45378"): "colonoscopy", ("CPT", "45380"): "colonoscopy", ("CPT", "45385"): "colonoscopy", ("HCPCS", "G0121"): "colonoscopy",
    ("CPT", "82274"): "fit", ("HCPCS", "G0328"): "fit",
    ("CPT", "81528"): "sdna_fit",
    ("CPT", "74263"): "ct_colonography",
    ("ICD-10-CM", "Z90.13"): "bilateral_mastectomy",
    ("ICD-10-CM", "Z51.5"): "palliative_care",
    ("HCPCS", "G9473"): "hospice", ("HCPCS", "Q5003"): "hospice",
    ("CPT", "44150"): "total_colectomy",
    # Narrative mentions (never accepted on their own)
    ("TEXT", "SOAP-MAMMO"): "mammogram", ("TEXT", "SOAP-BP"): "bp", ("TEXT", "SOAP-COL"): "colonoscopy",
}
CONCEPT_PREFIXES = {("ICD-10-CM", "E10"): "diabetes", ("ICD-10-CM", "E11"): "diabetes", ("ICD-10-CM", "E13"): "diabetes",
                    ("ICD-10-CM", "C18"): "colorectal_cancer", ("ICD-10-CM", "C19"): "colorectal_cancer", ("ICD-10-CM", "C20"): "colorectal_cancer"}
CONCEPT_LABELS = {
    "hba1c": "HbA1c", "bp": "Blood pressure", "mammogram": "Mammography", "breast_ultrasound": "Breast ultrasound (not mammography)",
    "colonoscopy": "Colonoscopy", "fit": "FIT (stool test)", "sdna_fit": "sDNA-FIT", "ct_colonography": "CT colonography",
    "diabetes": "Diabetes diagnosis", "bilateral_mastectomy": "Bilateral mastectomy", "palliative_care": "Palliative care",
    "hospice": "Hospice", "total_colectomy": "Total colectomy", "colorectal_cancer": "Colorectal cancer",
}

# Plausibility ranges for data-quality checks.
PLAUSIBLE = {"hba1c": (3.0, 20.0), "systolic": (60, 300), "diastolic": (30, 200)}
VALID_UNITS = {"hba1c": ("%",), "bp": ("mmHg",)}
ACCEPTED_BP_CONTEXTS = ("outpatient", "telehealth", "home")
EXCLUSION_CONCEPTS = {"hospice": "Hospice services during the period", "palliative_care": "Palliative care during the period",
                      "bilateral_mastectomy": "Bilateral mastectomy history", "total_colectomy": "Total colectomy history",
                      "colorectal_cancer": "Colorectal cancer history"}
ENROLLMENT_ALLOWED_GAP_DAYS = 45


@dataclass(frozen=True)
class Measure:
    measure_id: str
    name: str
    short: str
    purpose: str
    kind: str  # outcome | screening
    age: tuple
    sex: str | None
    requires_condition: str | None
    exclusions: tuple
    evidence_concepts: tuple
    lookbacks: dict = field(default_factory=dict)  # concept -> window start date
    target: str = ""
    outcome_rule: str = ""
    actions: dict = field(default_factory=dict)  # gap type -> recommended action


MEASURES = {
    "GSD-P": Measure(
        "GSD-P", "Glycemic Status Assessment for Patients With Diabetes (prototype)", "Glycemic status",
        "Was glycemic testing done this period, and is the latest accepted result within the configured target?",
        "outcome", (18, 75), None, "diabetes", ("hospice", "palliative_care"), ("hba1c",),
        {"hba1c": PERIOD.start}, "Latest accepted HbA1c < 8.0%", "Latest HbA1c in the period; most recent date wins.",
        {"CLINICAL": "Order HbA1c", "OUTCOME": "Clinical follow-up: intensify glycemic management",
         "DOCUMENTATION": "Retrieve / verify the HbA1c report", "DATA_CORRECTION": "Correct the HbA1c record (date, unit or value)"},
    ),
    "BPD-P": Measure(
        "BPD-P", "Blood Pressure Control for Patients With Diabetes (prototype)", "BP control (diabetes)",
        "Is there a valid BP reading this period, and does the latest acceptable reading meet the control threshold?",
        "outcome", (18, 75), None, "diabetes", ("hospice", "palliative_care"), ("bp",),
        {"bp": PERIOD.start}, "Latest acceptable BP < 140/90", "Outpatient, telehealth or device-transmitted home readings; "
        "same-date readings use lowest systolic and lowest diastolic.",
        {"CLINICAL": "Schedule BP check", "OUTCOME": "BP follow-up visit within 14 days",
         "DOCUMENTATION": "Verify the BP source", "DATA_CORRECTION": "Correct the BP record"},
    ),
    "BCS-P": Measure(
        "BCS-P", "Breast Cancer Screening (prototype)", "Breast screening",
        "Is there acceptable mammography within the lookback window?",
        "screening", (50, 74), "F", None, ("hospice", "palliative_care", "bilateral_mastectomy"), ("mammogram",),
        {"mammogram": date(PERIOD.start.year - 2, 10, 1)}, "Mammogram since 1 Oct two years before the period", "",
        {"CLINICAL": "Book screening mammogram", "DOCUMENTATION": "Request the outside mammogram report",
         "DATA_CORRECTION": "Resolve identity mismatch on imaging record"},
    ),
    "COL-P": Measure(
        "COL-P", "Colorectal Cancer Screening (prototype)", "Colorectal screening",
        "Is there acceptable screening by any supported modality within its lookback?",
        "screening", (45, 75), None, None, ("hospice", "palliative_care", "total_colectomy", "colorectal_cancer"),
        ("colonoscopy", "fit", "sdna_fit", "ct_colonography"),
        {"colonoscopy": date(PERIOD.start.year - 9, 1, 1), "ct_colonography": date(PERIOD.start.year - 4, 1, 1),
         "sdna_fit": date(PERIOD.start.year - 2, 1, 1), "fit": PERIOD.start},
        "Colonoscopy 10 y · CT colonography 5 y · sDNA-FIT 3 y · FIT this period", "",
        {"CLINICAL": "Offer FIT kit or schedule colonoscopy", "DOCUMENTATION": "Retrieve the outside colonoscopy report",
         "DATA_CORRECTION": "Correct the screening record", "FOLLOW_UP": "Diagnostic colonoscopy after positive stool test"},
    ),
}
GLYCEMIC_TARGET = 8.0
BP_TARGET = (140, 90)

# Governance artifacts.
LIMITATIONS = (
    ("Public summaries, not licensed specs", "Rates are HEDIS-inspired and cannot be reported as HEDIS.", "Obtain licensed NCQA specifications."),
    ("Synthetic history", "20 patients, one measurement period; patterns are illustrative.", "Validate with real longitudinal data."),
    ("Claims lag", "Recent care may look missing.", "Evidence-pending status and re-evaluation."),
    ("Rule-based risk", "Workload priority only, not a validated prediction.", "Factors shown beside every score."),
    ("Free text", "Narrative never closes a gap.", "Structured or verified evidence plus human approval."),
    ("Single period", "Period rollover is simulated, not exercised.", "Add rollover tests before production."),
)
DATA_DICTIONARY = (
    ("patient", "patient_id, DOB, sex, status, attributed provider, program, enrollment periods"),
    ("clinical_event", "event_id, patient_id, event_type, code system/code, concept, value(s), unit, event_date, recorded_at, "
                       "source, source_record_id, status, verification, context, duplicate_of"),
    ("measure_result", "result_id, run_id, patient_id, measure_id, rule_version, period, state, evidence trace, logic trace, evaluated_at"),
    ("care_gap", "gap_id, result_id, type, priority, owner, due_date, workflow status, disposition, history"),
    ("care_plan / care_task", "plan_id, goals, interventions, tasks (action, owner role, due date, status), approval"),
    ("outreach", "outreach_id, gap_id, channel, draft, approval, sent_at, disposition"),
    ("audit_event", "seq, actor, action, object_type, object_id, previous/new state, details, timestamp, hash"),
)
ROLE_MATRIX = (
    ("View dashboards, Patient 360, gaps", "All value-based care roles"),
    ("Review, assign, schedule, attach evidence, draft outreach", "Care Manager, HEDIS Analyst, HEDIS Lead, Physician / APP, Pharmacist"),
    ("Verify evidence, approve outreach and care plans", "HEDIS Analyst, HEDIS Lead, Physician / APP, Care Manager"),
    ("Reset demo data", "Super Admin, Operations Admin"),
)
