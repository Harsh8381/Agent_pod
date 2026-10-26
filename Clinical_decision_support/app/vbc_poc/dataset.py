"""Synthetic longitudinal dataset: 20 patients, 12 months of history, deliberate edge cases.

Cohorts follow the POC design: diabetes controlled (5), diabetes uncontrolled or missing test (5),
preventive screening due (4), recently discharged (3) and low-risk preventive (3). Every event keeps
its own provenance (source system, source record ID, event date separate from ingestion date).
All names and records are fictional.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from app.vbc_poc.catalog import PROGRAM


@dataclass
class Patient:
    patient_id: str
    name: str
    dob: date | None
    sex: str
    attributed_provider: str
    cohort: str
    story: str
    enrollment: list = field(default_factory=list)  # [(start, end)]
    program: str = PROGRAM
    status: str = "active"
    language: str = "English"
    preferred_channel: str = "SMS"
    phone_on_file: bool = True

    def age_on(self, day: date) -> int | None:
        if not self.dob:
            return None
        return day.year - self.dob.year - ((day.month, day.day) < (self.dob.month, self.dob.day))


@dataclass
class Event:
    event_id: str
    patient_id: str
    event_type: str  # encounter | condition | observation | procedure | order | claim | document
    code_system: str
    code: str
    display: str
    event_date: date | None
    recorded_at: date
    source: str
    source_record_id: str
    value: float | None = None
    value2: float | None = None
    unit: str = ""
    status: str = "final"  # final | completed | ordered | scheduled | incomplete | preliminary | cancelled
    verification: str = "verified"  # verified | pending | rejected
    context: str = "outpatient"  # outpatient | inpatient | ed | home | external | telehealth
    encounter_type: str = ""  # pcp_office | annual_wellness | ed | inpatient | urgent_care | specialist | hospice
    end_date: date | None = None
    dob_on_record: date | None = None
    result_flag: str = ""  # positive | negative
    note: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("event_date", "recorded_at", "end_date", "dob_on_record"):
            data[key] = data[key].isoformat() if data[key] else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Event:
        values = dict(data)
        for key in ("event_date", "recorded_at", "end_date", "dob_on_record"):
            values[key] = date.fromisoformat(values[key]) if values.get(key) else None
        return cls(**values)


SOURCE_PREFIX = {"lab": "ORU", "ehr": "EHR", "claim": "CLM", "external_report": "EXT", "narrative": "NOTE",
                 "patient_device": "RPM", "workflow_upload": "UPL"}
FULL_YEAR = [(date(2025, 1, 1), date(2026, 12, 31))]


class _Builder:
    """Compact event construction with automatic IDs and provenance."""

    def __init__(self, patient_id: str):
        self.patient_id = patient_id
        self.events: list[Event] = []

    def add(self, event_type, system, code, display, on, source="ehr", lag=1, **extra) -> Event:
        number = len(self.events) + 1
        recorded = extra.pop("recorded", None) or ((on + timedelta(days=lag)) if on else date(2026, 7, 1))
        event = Event(f"{self.patient_id}-E{number:02d}", self.patient_id, event_type, system, code, display, on, recorded, source,
                      f"{SOURCE_PREFIX[source]}-{self.patient_id[-3:]}{number:02d}{(on or recorded).strftime('%y%m%d')}", **extra)
        self.events.append(event)
        return event

    def visit(self, on, kind="pcp_office", provider="", end=None, context=None, note=""):
        labels = {"pcp_office": "PCP office visit", "annual_wellness": "Annual wellness visit", "ed": "Emergency department visit",
                  "inpatient": "Inpatient admission", "urgent_care": "Urgent care visit", "specialist": "Specialist visit",
                  "hospice": "Hospice enrollment"}
        ctx = context or {"ed": "ed", "inpatient": "inpatient", "hospice": "inpatient"}.get(kind, "outpatient")
        return self.add("encounter", "ENC", kind.upper(), labels[kind], on, encounter_type=kind, end_date=end, context=ctx,
                        note=note or provider)

    def diabetes(self, on=date(2019, 3, 1), code="E11.9"):
        return self.add("condition", "ICD-10-CM", code, "Type 2 diabetes mellitus", on, lag=0)

    def a1c(self, on, value, source="lab", **extra):
        return self.add("observation", "LOINC", "4548-4", "Hemoglobin A1c", on, source, unit=extra.pop("unit", "%"), value=value, **extra)

    def bp(self, on, systolic, diastolic, source="ehr", context="outpatient", **extra):
        return self.add("observation", "LOINC", "85354-9", "Blood pressure panel", on, source, value=systolic, value2=diastolic,
                        unit="mmHg", context=context, **extra)

    def proc(self, on, system, code, display, source="ehr", **extra):
        return self.add("procedure", system, code, display, on, source, status=extra.pop("status", "completed"), **extra)


def _routine_history(builder: _Builder) -> None:
    """Background events that no measure uses, so every timeline carries 4-8+ events like a real chart."""
    visits = [event.event_date for event in builder.events if event.event_type == "encounter" and event.event_date]
    anchor = max(visits) if visits else date(2026, 1, 15)
    if len(builder.events) < 6:
        builder.add("observation", "LOINC", "29463-7", "Body weight", anchor, "ehr", value=float(66 + int(builder.patient_id[-2:]) % 9 * 3), unit="kg")
    if len(builder.events) < 6:
        builder.add("procedure", "CVX", "150", "Influenza vaccine, quadrivalent", date(2025, 11, 4), "claim", lag=9)
    if len(builder.events) < 6:
        builder.add("document", "TEXT", "MED-REC", "Medication reconciliation note", anchor, "ehr", note="Structured medication list reviewed")


def _patients() -> tuple[list[Patient], list[Event]]:
    people: list[Patient] = []
    events: list[Event] = []

    def make(pid, name, dob, sex, pcp, cohort, story, build, enrollment=None, **extra):
        builder = _Builder(pid)
        build(builder)
        _routine_history(builder)
        people.append(Patient(pid, name, dob, sex, pcp, cohort, story, enrollment or FULL_YEAR, **extra))
        events.extend(builder.events)

    # ---------------- Diabetes controlled ----------------
    def p001(b):
        b.diabetes()
        b.visit(date(2026, 3, 10), "pcp_office", "Dr. A. Rivera")
        b.a1c(date(2026, 3, 10), 7.1)
        b.proc(date(2026, 3, 10), "CPT", "83036", "HbA1c test (claim)", "claim", lag=18, note="Billed for the same draw as the lab result")
        b.visit(date(2026, 9, 2), "pcp_office", "Dr. A. Rivera")
        b.a1c(date(2026, 9, 2), 6.9)
        b.proc(date(2026, 9, 2), "CPT", "83036", "HbA1c test (claim)", "claim", lag=21)
        b.bp(date(2026, 9, 2), 128, 78)
        b.proc(date(2025, 6, 20), "CPT", "77067", "Screening mammography, bilateral", "claim", lag=14)
        b.proc(date(2020, 5, 14), "CPT", "45378", "Colonoscopy, diagnostic", "ehr")

    make("VBC-001", "Maria Alvarez", date(1968, 4, 12), "F", "Dr. A. Rivera", "Diabetes controlled",
         "Controlled diabetes; duplicate lab + claim HbA1c counted once; all screenings current.", p001)

    def p002(b):
        b.diabetes()
        b.visit(date(2026, 7, 15), "pcp_office", "Dr. A. Rivera")
        b.a1c(date(2026, 7, 15), 7.6)
        b.bp(date(2026, 7, 15), 134, 84)
        b.bp(date(2026, 9, 30), 850, 40, note="Keying error at kiosk")
        b.proc(date(2026, 2, 11), "CPT", "77067", "Screening mammography, bilateral", "ehr", dob_on_record=date(1962, 3, 8),
               note="Imaging record carries a different date of birth")
        b.proc(date(2026, 2, 20), "CPT", "82274", "FIT, immunochemical", "lab", result_flag="negative")

    make("VBC-002", "Ruth Okafor", date(1962, 8, 30), "F", "Dr. A. Rivera", "Diabetes controlled",
         "Controlled diabetes; an impossible 850/40 reading is excluded; mammogram linked to a different DOB.", p002)

    def p003(b):
        b.diabetes()
        b.a1c(date(2026, 5, 20), 7.4)
        b.a1c(date(2026, 12, 15), 6.5, recorded=date(2026, 6, 2), note="Future-dated entry (data entry error)")
        b.visit(date(2026, 8, 11), "pcp_office", "Dr. S. Bennett")
        b.bp(date(2026, 8, 11), 142, 88, note="First reading")
        b.bp(date(2026, 8, 11), 136, 82, note="Repeat reading after rest")
        b.proc(date(2018, 9, 10), "CPT", "45378", "Colonoscopy, diagnostic", "ehr")

    make("VBC-003", "Daniel Kim", date(1957, 11, 5), "M", "Dr. S. Bennett", "Diabetes controlled",
         "Two same-day BP readings resolved by the lowest-reading rule; a future-dated HbA1c is rejected.", p003)

    def p004(b):
        b.diabetes()
        b.visit(date(2026, 6, 3), "pcp_office", "Dr. S. Bennett")
        b.a1c(date(2026, 6, 3), 6.8)
        b.bp(date(2026, 6, 3), 124, 76)

    make("VBC-004", "Linda Brooks", date(1970, 2, 17), "F", "Dr. S. Bennett", "Diabetes controlled",
         "Well controlled, but a 75-day enrollment gap means the denominator is not met.", p004,
         enrollment=[(date(2025, 1, 1), date(2026, 4, 30)), (date(2026, 7, 15), date(2026, 12, 31))])

    def p005(b):
        b.diabetes()
        b.a1c(date(2026, 2, 18), 8.3)
        b.visit(date(2026, 8, 19), "pcp_office", "Dr. A. Rivera")
        b.a1c(date(2026, 8, 19), 7.8)
        b.bp(date(2026, 8, 19), 138, 86)
        b.bp(date(2026, 9, 25), 132, 80, "patient_device", "home", note="Cellular cuff, device-transmitted")
        b.proc(date(2025, 3, 10), "CPT", "81528", "sDNA-FIT (Cologuard)", "lab", result_flag="negative")

    make("VBC-005", "Ahmed Hassan", date(1966, 12, 1), "M", "Dr. A. Rivera", "Diabetes controlled",
         "HbA1c improved from 8.3 to 7.8; latest BP is a device-transmitted home reading.", p005, language="Arabic")

    # ---------------- Diabetes uncontrolled / missing test ----------------
    def p006(b):
        b.diabetes(code="E11.65")
        b.visit(date(2025, 8, 12), "pcp_office", "Dr. A. Rivera")
        b.a1c(date(2025, 8, 12), 9.1, note="Last HbA1c, previous period")
        b.visit(date(2026, 9, 28), "ed", note="Hyperglycemia, discharged home")
        b.visit(date(2026, 10, 8), "urgent_care", note="Medication refill")
        b.bp(date(2026, 10, 8), 152, 94)

    make("VBC-006", "James Whitfield", date(1959, 3, 22), "M", "Dr. A. Rivera", "Diabetes uncontrolled / missing test",
         "Demo patient: no HbA1c this period, BP 152/94, recent ED visit, no PCP visit in 12 months, no colorectal screening.", p006)

    def p007(b):
        b.diabetes()
        b.visit(date(2026, 8, 14), "pcp_office", "Dr. S. Bennett")
        b.a1c(date(2026, 8, 14), 9.4)
        b.bp(date(2026, 8, 14), 138, 88)
        b.proc(date(2023, 11, 2), "CPT", "77067", "Screening mammography, bilateral", "claim", lag=12)
        b.proc(date(2026, 3, 3), "CPT", "76641", "Breast ultrasound, unilateral", "ehr", note="Diagnostic ultrasound, not mammography")
        b.proc(date(2024, 4, 15), "CPT", "45378", "Colonoscopy, diagnostic", "ehr", status="incomplete", note="Incomplete: inadequate prep")

    make("VBC-007", "Patricia Nguyen", date(1963, 7, 9), "F", "Dr. S. Bennett", "Diabetes uncontrolled / missing test",
         "HbA1c 9.4 (test done, outcome not controlled); mammogram outside lookback; incomplete colonoscopy.", p007, language="Vietnamese")

    def p008(b):
        b.diabetes()
        b.a1c(None, 7.2, recorded=date(2026, 7, 9), note="Result received without a collection date")
        b.bp(date(2025, 11, 20), 128, 80, note="Previous period")
        b.visit(date(2026, 7, 22), "pcp_office", "Dr. S. Bennett")
        b.bp(date(2026, 7, 22), 146, None, note="Diastolic not recorded")
        b.proc(date(2022, 2, 8), "CPT", "45378", "Colonoscopy, diagnostic", "ehr")

    make("VBC-008", "Robert Chen", date(1961, 1, 28), "M", "Dr. S. Bennett", "Diabetes uncontrolled / missing test",
         "HbA1c without a date and a systolic-only BP: data-quality issues, not care gaps.", p008, language="Mandarin")

    def p009(b):
        b.diabetes()
        b.visit(date(2026, 9, 12), "specialist", "Riverside Endocrinology")
        b.a1c(date(2026, 9, 12), 7.0, "external_report", verification="pending", context="external", lag=6,
              note="Faxed report from Riverside Endocrinology; patient/date match not yet confirmed")
        b.proc(date(2026, 9, 12), "CPT", "83036", "HbA1c test (claim, no result value)", "claim", lag=24)
        b.bp(date(2026, 9, 12), 144, 92, context="outpatient")
        b.add("document", "TEXT", "SOAP-MAMMO", "SOAP note: 'mammogram done elsewhere in March'", date(2026, 9, 12), "narrative",
              verification="pending", note="Free text from the ambient scribe; no report on file")
        b.proc(date(2026, 4, 3), "CPT", "82274", "FIT, immunochemical", "lab", result_flag="positive")

    make("VBC-009", "Susan Patel", date(1960, 10, 14), "F", "Dr. A. Rivera", "Diabetes uncontrolled / missing test",
         "External HbA1c pending verification, BP 144/92, mammogram only mentioned in a note, positive FIT needing follow-up.", p009)

    def p010(b):
        b.diabetes()
        b.visit(date(2026, 8, 27), "pcp_office", "Dr. S. Bennett")
        b.a1c(date(2026, 8, 27), 7.1, note="Northstar Reference Lab")
        b.a1c(date(2026, 8, 27), 9.2, note="Lakeside Outreach Lab — same day, different value")
        b.bp(date(2026, 8, 27), 148, 92, note="Reading 1")
        b.bp(date(2026, 8, 27), 138, 86, note="Reading 2")
        b.bp(date(2026, 8, 27), 142, 84, note="Reading 3")
        b.add("document", "TEXT", "SOAP-BP", "SOAP note: 'BP controlled'", date(2026, 9, 20), "narrative", verification="pending",
              note="Free text from the ambient scribe")
        b.proc(date(2021, 5, 4), "CPT", "45378", "Colonoscopy (outside report)", "external_report", verification="pending", context="external",
               lag=1600, note="Outside GI practice; awaiting verification")

    make("VBC-010", "George Murphy", date(1964, 5, 3), "M", "Dr. S. Bennett", "Diabetes uncontrolled / missing test",
         "Two labs disagree on the same day's HbA1c; three same-day BP readings; narrative 'BP controlled' is not evidence.", p010)

    # ---------------- Preventive screening due ----------------
    def p011(b):
        b.visit(date(2026, 9, 30), "annual_wellness", "Dr. A. Rivera")
        b.add("order", "CPT", "45378", "Colonoscopy ordered", date(2026, 9, 30), "ehr", status="ordered", note="Appointment 18 Nov 2026")

    make("VBC-011", "Helen Carter", date(1971, 6, 25), "F", "Dr. A. Rivera", "Preventive screening due",
         "No mammogram on record; colonoscopy ordered but not completed (an order never closes a gap).", p011)

    def p012(b):
        b.visit(date(2026, 8, 1), "pcp_office", "Dr. A. Rivera")
        b.add("order", "CPT", "77067", "Screening mammogram ordered", date(2026, 8, 1), "ehr", status="ordered")
        b.proc(date(2019, 3, 12), "CPT", "45378", "Colonoscopy, diagnostic", "ehr")
        b.proc(date(2019, 3, 12), "CPT", "45378", "Colonoscopy (claim)", "claim", lag=20)

    make("VBC-012", "Margaret Doyle", date(1956, 9, 15), "F", "Dr. A. Rivera", "Preventive screening due",
         "Mammogram ordered but not done; colonoscopy recorded twice (EHR + claim) and counted once.", p012)

    def p013(b):
        b.visit(date(2026, 4, 14), "annual_wellness", "Dr. S. Bennett")
        b.add("document", "TEXT", "SOAP-COL", "SOAP note: 'colonoscopy at age 50 elsewhere'", date(2026, 4, 14), "narrative",
              verification="pending", note="Free text; no procedure record or report")

    make("VBC-013", "Thomas Reed", date(1973, 1, 19), "M", "Dr. S. Bennett", "Preventive screening due",
         "Colonoscopy only mentioned in narrative: evidence review, not closure.", p013)

    def p014(b):
        b.add("condition", "ICD-10-CM", "Z90.13", "Acquired absence of bilateral breasts", date(2015, 6, 1), "ehr", lag=0)
        b.proc(date(2015, 2, 20), "CPT", "45378", "Colonoscopy, diagnostic", "ehr")
        b.visit(date(2026, 5, 6), "pcp_office", "Dr. A. Rivera")

    make("VBC-014", "Grace Liu", date(1958, 4, 2), "F", "Dr. A. Rivera", "Preventive screening due",
         "Bilateral mastectomy excludes breast screening; colonoscopy from 2015 is outside the 10-year lookback.", p014, language="Mandarin")

    # ---------------- Recently discharged ----------------
    def p015(b):
        b.diabetes(code="E11.22")
        b.visit(date(2026, 3, 5), "pcp_office", "Dr. S. Bennett")
        b.visit(date(2026, 10, 12), "inpatient", end=date(2026, 10, 17), note="CHF exacerbation, Northstar Medical Center")
        b.a1c(date(2026, 10, 13), 8.6, context="inpatient")
        b.bp(date(2026, 10, 14), 128, 76, context="inpatient", note="Inpatient reading")
        b.bp(date(2026, 10, 16), 132, 78, context="inpatient", note="Inpatient reading")
        b.proc(date(2020, 1, 22), "CPT", "45378", "Colonoscopy, diagnostic", "ehr")

    make("VBC-015", "Walter Simmons", date(1952, 12, 9), "M", "Dr. S. Bennett", "Recently discharged",
         "Discharged 9 days ago; only inpatient BP readings this period (not acceptable); HbA1c 8.6.", p015)

    def p016(b):
        b.visit(date(2026, 9, 2), "inpatient", end=date(2026, 9, 6), note="Pneumonia")
        b.visit(date(2026, 9, 24), "inpatient", end=date(2026, 9, 29), note="Readmission: sepsis")
        b.visit(date(2026, 10, 5), "ed", note="Shortness of breath")
        b.proc(date(2025, 1, 15), "CPT", "77067", "Screening mammography, bilateral", "claim", lag=10)
        b.proc(date(2026, 6, 10), "CPT", "82274", "FIT, immunochemical", "lab", result_flag="negative")

    make("VBC-016", "Dorothy Evans", date(1953, 7, 21), "F", "Dr. A. Rivera", "Recently discharged",
         "Readmitted within 30 days and an ED visit since; screenings are current — high risk without quality gaps.", p016)

    def p017(b):
        b.diabetes()
        b.visit(date(2026, 9, 10), "inpatient", end=date(2026, 9, 18), note="Metastatic disease, comfort care")
        b.add("procedure", "HCPCS", "G9473", "Hospice services", date(2026, 9, 20), "claim", status="completed", lag=12)

    make("VBC-017", "Frank Morales", date(1955, 2, 11), "M", "Dr. S. Bennett", "Recently discharged",
         "Hospice since September: excluded from every measure, so no outreach is created.", p017, language="Spanish")

    # ---------------- Low-risk preventive ----------------
    def p018(b):
        b.visit(date(2026, 4, 22), "annual_wellness", "Dr. A. Rivera")
        b.proc(date(2026, 4, 22), "CPT", "77067", "Screening mammography, bilateral", "ehr")
        b.proc(date(2021, 6, 1), "CPT", "45378", "Colonoscopy, diagnostic", "ehr")

    make("VBC-018", "Emily Foster", date(1974, 8, 8), "F", "Dr. A. Rivera", "Low-risk preventive",
         "Everything current: shows the system does not over-alert.", p018)

    def p019(b):
        b.visit(date(2026, 5, 14), "annual_wellness", "Dr. S. Bennett")
        b.proc(date(2026, 5, 14), "CPT", "82274", "FIT, immunochemical", "lab", result_flag="negative")

    make("VBC-019", "Kevin Shah", date(1979, 3, 30), "M", "Dr. S. Bennett", "Low-risk preventive",
         "Negative FIT this period: colorectal screening met.", p019)

    def p020(b):
        b.visit(date(2026, 2, 3), "annual_wellness", "Dr. A. Rivera")

    make("VBC-020", "Olivia Grant", date(1994, 11, 12), "F", "Dr. A. Rivera", "Low-risk preventive",
         "Age 32: not eligible for any of the four measures.", p020, language="French")
    return people, events


PATIENTS, EVENTS = _patients()
PATIENTS_BY_ID = {patient.patient_id: patient for patient in PATIENTS}
DEMO_PATIENT = "VBC-006"
