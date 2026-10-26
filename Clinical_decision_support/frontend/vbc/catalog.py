"""Value-based care catalog: measurement period, statuses, evidence checks, value sets and measures.

Value sets here are illustrative stand-ins built from public code systems (ICD-10-CM, LOINC,
CPT, CVX). They are NOT the licensed NCQA Value Set Directory; the UI says so wherever value
sets appear. Cut points are estimates held as configuration, never CMS-published values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Measurement period and versions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementPeriod:
    code: str
    start: date
    end: date
    rating_year: str

    @property
    def label(self) -> str:
        return f"{self.code} · feeds {self.rating_year}"


MP = MeasurementPeriod("MP 2026", date(2026, 1, 1), date(2026, 12, 31), "2028 Star Ratings")
PRIOR_MP = MeasurementPeriod("MP 2025", date(2025, 1, 1), date(2025, 12, 31), "2027 Star Ratings")
BASE_VERSION = "v2026.1"
VALUE_SET_SOURCE = "Demo VSD (public code systems)"
VALUE_SET_NOTICE = (
    "Running on illustrative value sets built from public code systems. Load the licensed NCQA "
    "Value Set Directory before producing reportable rates."
)

# ---------------------------------------------------------------------------
# Shared language: the seven gap statuses
# ---------------------------------------------------------------------------
GAP_OPEN = "GAP_OPEN"
GAP_UNKNOWN = "GAP_UNKNOWN"
PENDING_EVIDENCE = "PENDING_EVIDENCE"
GAP_CLOSED = "GAP_CLOSED"
EXCLUDED = "EXCLUDED"
NOT_IN_POPULATION = "NOT_IN_POPULATION"
PREFERENCE_RECORDED = "PREFERENCE_RECORDED"


@dataclass(frozen=True)
class GapStatus:
    code: str
    label: str
    meaning: str
    counts: str
    work_goes: str
    tone: str  # badge tone in the UI palette


GAP_STATUSES = {
    GAP_OPEN: GapStatus(
        GAP_OPEN, "Open", "In the denominator; no qualifying evidence after every permitted source was checked.",
        "Counts as not met", "Intervention queue: visit, outreach or referral", "red",
    ),
    GAP_UNKNOWN: GapStatus(
        GAP_UNKNOWN, "Unknown", "In the denominator; a required source is stale, missing or unreadable.",
        "Not met (reported)", "Evidence retrieval — never patient outreach", "amber",
    ),
    PENDING_EVIDENCE: GapStatus(
        PENDING_EVIDENCE, "Pending evidence", "Service reported as done; acceptable evidence not yet received.",
        "Not met until proof", "Record request or supplemental data", "blue",
    ),
    GAP_CLOSED: GapStatus(
        GAP_CLOSED, "Closed", "Numerator met with evidence that passed all eight checks.",
        "Met", "Monitor for reversal; send to payer", "green",
    ),
    EXCLUDED: GapStatus(
        EXCLUDED, "Excluded", "A denominator exclusion is met, with evidence.",
        "Removed from denominator", "Suppress outreach", "gray",
    ),
    NOT_IN_POPULATION: GapStatus(
        NOT_IN_POPULATION, "Not in population", "Does not meet the measure's initial population.",
        "Not counted", "No action", "gray",
    ),
    PREFERENCE_RECORDED: GapStatus(
        PREFERENCE_RECORDED, "Preference recorded",
        "Informed refusal or deferral documented; not an exclusion unless the spec says so.",
        "Not met (reported)", "Re-offer per policy, without pressure", "violet",
    ),
}
WORKABLE_STATUSES = (GAP_OPEN, GAP_UNKNOWN, PENDING_EVIDENCE)
DENOMINATOR_STATUSES = (GAP_OPEN, GAP_UNKNOWN, PENDING_EVIDENCE, GAP_CLOSED, PREFERENCE_RECORDED)

# ---------------------------------------------------------------------------
# Evidence: the eight checks and reason codes
# ---------------------------------------------------------------------------
EVIDENCE_CHECKS = (
    ("present", "Present", "PRS"),
    ("value_set", "Value-set match", "VSM"),
    ("structured", "Structured", "STR"),
    ("final", "Final status", "STS"),
    ("in_window", "In window", "WIN"),
    ("person_linked", "Person-linked", "IDN"),
    ("source_permitted", "Source permitted", "SRC"),
    ("unconflicted", "Unconflicted", "CFL"),
)
REASON_CODES = {
    "PRS": ("No result value or code on the record", "Lab interface owner"),
    "VSM": ("Code is not in the value set for this measure version", "HEDIS analyst / coding"),
    "STR": ("Not structured — needs abstraction or structured entry", "HEDIS analyst"),
    "STS": ("Result is not final — held until the final result posts", "Lab interface owner"),
    "WIN": ("Dated outside the permitted window", "Care manager"),
    "IDN": ("Identity match below threshold", "MPI steward"),
    "SRC": ("Source not permitted for this data method", "HEDIS analyst"),
    "CFL": ("Conflicts with another source — human review required", "HEDIS analyst"),
    "EXC": ("Exclusion evidence incomplete", "Clinician / analyst"),
    "LIN": ("Lineage incomplete — quarantined until restored", "HEDIS analyst"),
}
VERDICT_ACCEPTED = "ACCEPTED"
VERDICT_HELD = "ON_HOLD"
VERDICT_REJECTED = "REJECTED"
VERDICT_REVIEW = "REVIEW_REQUIRED"
FINAL_RESULT_STATUSES = ("final", "corrected", "amended")

# ---------------------------------------------------------------------------
# Sources of evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    payer_visible: bool
    ecds: bool  # standard electronic clinical data, permitted for ECDS/administrative evidence
    format: str


SOURCES = {
    "ehr": Source("ehr", "EHR (synthetic chart)", False, True, "FHIR R4"),
    "lab_interface": Source("lab_interface", "Regional lab interface", False, True, "HL7 v2 ORU"),
    "claims": Source("claims", "Claims", True, True, "X12 837"),
    "pharmacy": Source("pharmacy", "Pharmacy claims", True, True, "NCPDP"),
    "hie": Source("hie", "Health information exchange", False, True, "C-CDA"),
    "scanned": Source("scanned", "Scanned outside record", False, False, "PDF"),
    "abstraction": Source("abstraction", "Hybrid abstraction", False, False, "Abstraction form"),
    "cpt2": Source("cpt2", "CPT II · coder confirmed", True, True, "X12 837"),
    "attestation": Source("attestation", "Clinician attestation", False, True, "Structured entry"),
    "rpm": Source("rpm", "Remote patient monitoring", False, True, "FHIR Observation"),
    "adt": Source("adt", "ADT feed", False, True, "HL7 v2 A01/A03"),
    "outreach": Source("outreach", "Outreach record", False, True, "Workflow"),
}

# ---------------------------------------------------------------------------
# Value sets (illustrative)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueSet:
    name: str
    system: str
    codes: frozenset = frozenset()
    prefixes: tuple = ()

    def contains(self, system: str, code: str) -> bool:
        code = str(code or "").strip().upper()
        if not code or (system and self.system and system.upper() != self.system.upper()):
            return False
        return code in self.codes or any(code.startswith(prefix) for prefix in self.prefixes)


def _vs(name, system, codes=(), prefixes=()):
    return ValueSet(name, system, frozenset(str(code).upper() for code in codes), tuple(prefixes))


VALUE_SETS = {
    "Diabetes": _vs("Diabetes", "ICD-10-CM", prefixes=("E10", "E11", "E13")),
    "Essential Hypertension": _vs("Essential Hypertension", "ICD-10-CM", prefixes=("I10", "I11", "I12", "I13", "I15", "I16")),
    "HbA1c Lab Test": _vs("HbA1c Lab Test", "LOINC", ("4548-4", "4549-2", "17856-6", "97506-0")),
    "HbA1c Level CPT II": _vs("HbA1c Level CPT II", "CPT", ("3044F", "3051F", "3052F", "3046F")),
    "Estimated Glomerular Filtration Rate Lab Test": _vs(
        "Estimated Glomerular Filtration Rate Lab Test", "LOINC",
        ("98979-8", "48642-3", "48643-1", "33914-3", "62238-1", "69405-9", "50044-7", "88293-6", "88294-4"),
    ),
    "Urine Albumin Creatinine Ratio Lab Test": _vs(
        "Urine Albumin Creatinine Ratio Lab Test", "LOINC",
        ("9318-7", "14959-1", "13705-9", "32294-1", "44292-1", "59159-4", "76401-1"),
    ),
    "Retinal Eye Exams": _vs(
        "Retinal Eye Exams", "CPT",
        ("92002", "92004", "92012", "92014", "92250", "92227", "92228", "92229", "2022F", "2023F", "2024F", "2025F", "2026F", "2033F", "3072F"),
    ),
    "Mammography": _vs("Mammography", "CPT", ("77061", "77062", "77063", "77065", "77066", "77067")),
    "Colonoscopy": _vs(
        "Colonoscopy", "CPT",
        ("44388", "44389", "44392", "44394", "44401", "44402", "44403", "44404", "45378", "45379", "45380", "45381",
         "45384", "45385", "45388", "45390", "45393", "45398"),
    ),
    "FOBT/FIT Lab Test": _vs("FOBT/FIT Lab Test", "CPT", ("82270", "82274", "G0328")),
    "sDNA FIT Lab Test": _vs("sDNA FIT Lab Test", "CPT", ("81528",)),
    "Blood Pressure Observation": _vs("Blood Pressure Observation", "LOINC", ("85354-9", "8480-6", "8462-4", "55284-4")),
    "Systolic/Diastolic CPT II": _vs("Systolic/Diastolic CPT II", "CPT", ("3074F", "3075F", "3077F", "3078F", "3079F", "3080F")),
    "Statin Medications": _vs(
        "Statin Medications", "RXNORM-NAME",
        prefixes=("ATORVASTATIN", "ROSUVASTATIN", "SIMVASTATIN", "PRAVASTATIN", "LOVASTATIN", "FLUVASTATIN", "PITAVASTATIN"),
    ),
    "Functional Status Assessment": _vs("Functional Status Assessment", "CPT", ("1170F",)),
    "Influenza Vaccine": _vs(
        "Influenza Vaccine", "CVX", ("88", "135", "140", "141", "150", "153", "155", "158", "161", "166", "168", "171", "185", "186", "197", "205"),
    ),
    "Bilateral Mastectomy": _vs("Bilateral Mastectomy", "ICD-10-CM", ("Z90.13",)),
    "Colorectal Cancer / Total Colectomy": _vs(
        "Colorectal Cancer / Total Colectomy", "ICD-10-CM", ("Z90.49", "Z85.038", "Z85.048"), ("C18", "C19", "C20", "C21"),
    ),
    "ESRD / Dialysis": _vs("ESRD / Dialysis", "ICD-10-CM", ("N18.6", "Z99.2"), ("Z49",)),
    "Hospice Intervention or Encounter": _vs("Hospice Intervention or Encounter", "HCPCS", ("G9473", "G9474", "G9475", "Q5003", "S9126", "T2042")),
    "Palliative Care": _vs("Palliative Care", "ICD-10-CM", ("Z51.5",)),
}
# A local lab code that a real interface might send instead of LOINC (drives the U05 path).
LOCAL_UACR_CODE = "LAB-UACR-01"

# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    value_sets: tuple
    lookback_years: int = 0  # 0 = measurement period only
    window_start_override: date | None = None
    window_end_override: date | None = None


@dataclass(frozen=True)
class Measure:
    code: str
    name: str
    pathway: str
    star_weight: int  # 0 = display measure, not Star-rated
    data_method: str  # Administrative | Hybrid | ECDS
    kind: str  # person | event | ratio
    age_min: int
    age_max: int
    closure_rule: str
    action_label: str
    action_kind: str  # order | referral | outreach | visit | none
    components: tuple = ()
    sex: str | None = None
    direction: str = "higher"
    cut_points: tuple = ()  # estimated 2,3,4,5-star thresholds (rates in %, PCR as O/E)
    prior_rate: float | None = None  # MP 2025, frozen under v2025.2
    exclusions: tuple = ("hospice", "palliative")
    numerator_label: str = "Numerator met"
    allowed_sources: tuple = ("ehr", "lab_interface", "claims", "pharmacy", "hie", "cpt2", "attestation")

    @property
    def rated(self) -> bool:
        return self.star_weight > 0

    @property
    def label(self) -> str:
        return f"{self.code} · {self.name}"


COMMON_EXCLUSIONS = ("hospice", "palliative", "frailty_advanced_illness")
MEASURES = {
    "GSD": Measure(
        "GSD", "Glycemic Status Assessment for Patients With Diabetes (<8.0%)", "Diabetes", 3, "Hybrid", "person", 18, 75,
        "Needs a glycemic status result this period below 8.0%.", "Order HbA1c", "order",
        components=(Component("glycemic", "Most recent glycemic status", ("HbA1c Lab Test", "HbA1c Level CPT II")),),
        cut_points=(60.0, 70.0, 76.0, 82.0), prior_rate=67.1, exclusions=COMMON_EXCLUSIONS,
        numerator_label="Glycemic status <8.0%",
        allowed_sources=("ehr", "lab_interface", "claims", "hie", "cpt2", "abstraction"),
    ),
    "CBP": Measure(
        "CBP", "Controlling High Blood Pressure", "Cardiovascular", 3, "Hybrid", "person", 18, 85,
        "Needs a most-recent blood pressure this period below 140/90.", "BP recheck visit", "visit",
        components=(Component("bp", "Most recent BP reading", ("Blood Pressure Observation", "Systolic/Diastolic CPT II")),),
        cut_points=(58.0, 68.0, 74.0, 80.0), prior_rate=66.0, exclusions=COMMON_EXCLUSIONS + ("esrd",),
        numerator_label="BP <140/90",
        allowed_sources=("ehr", "claims", "hie", "cpt2", "abstraction"),  # remote readings only once CR-26-02 is published
    ),
    "KED": Measure(
        "KED", "Kidney Health Evaluation for Patients With Diabetes", "Diabetes", 1, "Administrative", "person", 18, 85,
        "Needs an eGFR and a uACR this period — both are required.", "Order uACR", "order",
        components=(
            Component("egfr", "eGFR", ("Estimated Glomerular Filtration Rate Lab Test",)),
            Component("uacr", "uACR", ("Urine Albumin Creatinine Ratio Lab Test",)),
        ),
        cut_points=(40.0, 52.0, 62.0, 72.0), prior_rate=63.7, exclusions=COMMON_EXCLUSIONS + ("esrd",),
        numerator_label="eGFR and uACR both present",
    ),
    "EED": Measure(
        "EED", "Eye Exam for Patients With Diabetes", "Diabetes", 1, "Hybrid", "person", 18, 75,
        "Needs a qualifying retinal eye exam in the permitted window.", "Refer to eye care", "referral",
        components=(Component("retinal", "Retinal eye exam", ("Retinal Eye Exams",)),),
        cut_points=(55.0, 65.0, 73.0, 80.0), prior_rate=65.2, exclusions=COMMON_EXCLUSIONS,
        numerator_label="Retinal exam in window",
    ),
    "BCS-E": Measure(
        "BCS-E", "Breast Cancer Screening", "Prevention", 1, "ECDS", "person", 50, 74,
        "Needs a mammogram from 1 Oct two years before the period through period end.", "Order mammogram", "order",
        components=(Component("mammogram", "Mammogram", ("Mammography",), 0, date(2024, 10, 1)),),
        sex="Female", cut_points=(58.0, 68.0, 77.0, 82.0), prior_rate=72.4,
        exclusions=COMMON_EXCLUSIONS + ("bilateral_mastectomy",), numerator_label="Mammogram in window",
    ),
    "COL-E": Measure(
        "COL-E", "Colorectal Cancer Screening", "Prevention", 1, "ECDS", "person", 45, 75,
        "Needs a colonoscopy (10 yrs), FIT (this period) or FIT-DNA (3 yrs).", "Offer FIT kit", "outreach",
        components=(
            Component("colonoscopy", "Colonoscopy", ("Colonoscopy",), 9),
            Component("fit", "FIT", ("FOBT/FIT Lab Test",)),
            Component("fit_dna", "FIT-DNA", ("sDNA FIT Lab Test",), 2),
        ),
        cut_points=(55.0, 65.0, 73.0, 79.0), prior_rate=66.8, exclusions=COMMON_EXCLUSIONS + ("colorectal_cancer",),
        numerator_label="Any qualifying screening",
    ),
    "SPD": Measure(
        "SPD", "Statin Therapy for Patients With Diabetes", "Diabetes", 0, "Administrative", "person", 40, 75,
        "Needs at least one statin dispensing event this period.", "Review statin therapy", "order",
        components=(Component("statin", "Statin dispensed", ("Statin Medications",)),),
        prior_rate=70.0, exclusions=("hospice", "palliative", "esrd"), numerator_label="Statin dispensed",
        allowed_sources=("ehr", "pharmacy"),
    ),
    "COA-FSA": Measure(
        "COA-FSA", "Care for Older Adults — Functional Status Assessment", "Older adults", 0, "Hybrid", "person", 66, 120,
        "Needs a structured functional status assessment this period.", "Complete assessment", "visit",
        components=(Component("fsa", "Functional status assessment", ("Functional Status Assessment",)),),
        exclusions=("hospice",), numerator_label="Assessment documented",
    ),
    "AIS-E": Measure(
        "AIS-E", "Adult Immunization Status — Influenza", "Prevention", 0, "ECDS", "person", 19, 120,
        "Needs an influenza vaccine from 1 Jul of the prior year through 30 Jun.", "Offer flu vaccine", "outreach",
        components=(Component("influenza", "Influenza vaccine", ("Influenza Vaccine",), 0, date(2025, 7, 1), date(2026, 6, 30)),),
        prior_rate=41.0, exclusions=("hospice",), numerator_label="Influenza vaccine",
    ),
    "TRC": Measure(
        "TRC", "Transitions of Care", "Transitions", 1, "Hybrid", "event", 18, 120,
        "Each discharge needs notification, discharge information, engagement and medication reconciliation.",
        "Work the transition", "visit", cut_points=(45.0, 58.0, 66.0, 74.0), prior_rate=58.2, exclusions=("hospice",),
        numerator_label="Average of four components",
    ),
    "FMC": Measure(
        "FMC", "Follow-Up After ED Visit for People With Multiple High-Risk Chronic Conditions", "Transitions", 1,
        "Administrative", "event", 18, 120, "Needs a follow-up service within 7 days of the ED visit.", "Book follow-up", "visit",
        cut_points=(45.0, 55.0, 62.0, 70.0), prior_rate=52.0, exclusions=("hospice",), numerator_label="Follow-up within 7 days",
    ),
    "PCR": Measure(
        "PCR", "Plan All-Cause Readmissions (observed / expected)", "Utilization", 3, "Administrative", "ratio", 18, 120,
        "Lower is better: observed 30-day readmissions against the risk-adjusted expectation.", "Review discharge plan", "none",
        direction="lower", cut_points=(1.15, 1.05, 0.98, 0.90), prior_rate=1.07, exclusions=(),
        numerator_label="Observed readmissions",
    ),
}
DASHBOARD_MEASURES = ("GSD", "CBP", "PCR", "BCS-E", "COL-E", "KED", "EED", "TRC", "FMC")
PERSON_MEASURES = tuple(code for code, measure in MEASURES.items() if measure.kind == "person")
EXCLUSION_LABELS = {
    "hospice": "Hospice or hospice services",
    "palliative": "Palliative care",
    "frailty_advanced_illness": "Frailty with advanced illness (66+)",
    "esrd": "ESRD or dialysis",
    "bilateral_mastectomy": "Bilateral mastectomy",
    "colorectal_cancer": "Colorectal cancer or total colectomy",
    "deceased": "Died in period",
}
CUT_POINT_NOTICE = "Cut points are estimates until CMS publishes them; claims-based figures are ESTIMATED until run-out reaches 90%."

# ---------------------------------------------------------------------------
# Guardrails — the eight things the agent never does on its own
# ---------------------------------------------------------------------------
GUARDRAILS = (
    ("Close a gap without evidence", "Every closure passes all eight evidence checks."),
    ("Alter the medical record", "No edits to diagnoses, codes, results or clinical notes; clinicians document."),
    ("Treat refusal as exclusion", "Refusal, no-contact and missing data stay in the denominator."),
    ("Count suspected conditions", "Text-extracted suspects never enter HEDIS counts until a person confirms."),
    ("Touch patient surveys", "No referencing, coaching or targeting of CAHPS or HOS."),
    ("Let Star weight win", "Clinical urgency always ranks above Star weight."),
    ("Overwrite history", "Results stay frozen under the version that produced them."),
    ("Submit or attest", "An authorized person signs HEDIS submissions, never the agent."),
)

# ---------------------------------------------------------------------------
# Payers and operating configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Payer:
    key: str
    label: str
    name: str
    format: str
    cadence: str
    supplemental_cutoff: date


PAYERS = {
    "A": Payer("A", "Payer A", "Athena Health Insurance", "Flat file", "weekly", date(2026, 11, 13)),
    "B": Payer("B", "Payer B", "Care Gap Health", "FHIR bundle", "daily", date(2026, 12, 4)),
}
PAYER_BY_NAME = {payer.name: payer for payer in PAYERS.values()}
TEAM_WEEKLY_CAPACITY = 45
OUTREACH_ATTEMPT_LIMIT = 3
OUTREACH_RECONTACT_DAYS = 14
QUIET_HOURS = (20, 8)  # no outreach from 20:00 to 08:00 local time
FEED_STALE_DAYS = 7
EXCLUSION_SUPPRESS_CONFIDENCE = 0.85
HYBRID_SUNSET = "MP 2029"
RECONCILIATION_TARGET = 99.0
CLAIMS_RUNOUT = 0.78


@dataclass
class ChangeRequest:
    cr_id: str
    measure: str
    change: str
    change_type: str  # Revised | New indicator | Moved to ECDS | Retired
    tests_passed: int
    tests_total: int
    spec_override: dict = field(default_factory=dict)
    note: str = ""

    @property
    def tests_ok(self) -> bool:
        return self.tests_passed == self.tests_total


CHANGE_REQUESTS = (
    ChangeRequest("CR-26-01", "BCS-E", "Age band expanded from 50–74 to 40–74", "Revised", 48, 48, {"BCS-E": {"age_min": 40}},
                  "NCQA expanded the screening population; denominator grows, rate usually dips first."),
    ChangeRequest("CR-26-02", "CBP", "Patient-reported remote BP readings accepted", "Revised", 52, 52, {"CBP": {"allow_rpm": True}},
                  "Remote monitoring readings count as numerator evidence when person-linked and final."),
    ChangeRequest("CR-26-03", "SPD", "Moved to ECDS only (SPD → SPD-E)", "Moved to ECDS", 150, 150, {"SPD": {"data_method": "ECDS"}},
                  "Sampling switched off; full population reported."),
    ChangeRequest("CR-26-04", "AIS-E", "COVID-19 indicator added for members 65+", "New indicator", 61, 63, {},
                  "Two regression tests fail on dose-window edge cases; publication stays blocked."),
    ChangeRequest("CR-26-05", "EED", "Autonomous AI retinal imaging (CPT 92229) accepted", "Revised", 38, 38, {},
                  "Already present in the demo value set; no rate change on current data."),
)
CHANGE_REQUESTS_BY_ID = {cr.cr_id: cr for cr in CHANGE_REQUESTS}
