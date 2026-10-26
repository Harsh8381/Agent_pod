"""Evidence feeds: real EHR rows plus deterministic synthetic partner feeds.

EHR facts come straight from the chart. The EHR has no eGFR/uACR, eye exams, mammograms,
colonoscopies or inpatient stays, so a synthetic layer stands in for the partner feeds a plan
would receive (lab interface, claims, HIE, ADT, scanned outside records). It is generated
deterministically from patient IDs — the same input always yields the same facts — and every
fact carries its source and message reference so it is visibly labelled as a demo feed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from frontend.vbc.catalog import LOCAL_UACR_CODE, MP, VALUE_SETS
from frontend.vbc.evidence import Fact
from frontend.vbc.population import Person, draw, parse_date

# Chronic conditions that make an ED visit FMC-eligible (illustrative list).
FMC_CONDITIONS = {
    "asthma/COPD": ("J44", "J45"),
    "diabetes": ("E10", "E11", "E13"),
    "hypertension": ("I10", "I11", "I12", "I13"),
    "heart disease": ("I21", "I25", "I48", "I50"),
    "chronic kidney disease": ("N18",),
    "depression/anxiety": ("F32", "F33", "F41"),
    "stroke": ("I63", "G45"),
}


def _at(day: date, hour: int = 9, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute))


def _msg(prefix: str, person_id: str, salt: str) -> str:
    return f"{prefix}-{10000 + int(draw(person_id, 'msg-' + salt) * 89999)}"


def _in_period(person_id: str, salt: str, as_of: date, first: date | None = None, last: date | None = None) -> date:
    first = first or MP.start
    last = min(last or MP.end, as_of - timedelta(days=1))
    span = max((last - first).days, 1)
    return first + timedelta(days=int(draw(person_id, salt) * span))


# ---------------------------------------------------------------------------
# EHR facts (real chart rows)
# ---------------------------------------------------------------------------


def ehr_facts(person: Person) -> list[Fact]:
    facts: list[Fact] = []
    for row in person.labs:
        on = parse_date(row.get("Result_Date"))
        facts.append(Fact(
            f"EHR-LAB-{row.get('Lab_ID')}", person.person_id, "lab", "LOINC", str(row.get("LOINC_Code") or ""),
            str(row.get("Test_Name") or "Lab result"), on, _at(on, 7) if on else None, "ehr", f"EHR Labs/{row.get('Lab_ID')}",
            status=str(row.get("Status") or "final").lower(), value=row.get("Result"), unit=str(row.get("Unit") or ""),
            origin="ehr", provider=str(row.get("Ordering_Provider") or ""),
            extra={"lab": row.get("Performing_Lab"), "flag": row.get("Flag"), "reference": row.get("Reference_Range")},
        ))
    for row in person.vitals:
        on = parse_date(row.get("Observed_Date"))
        systolic, diastolic = row.get("Systolic_mmHg"), row.get("Diastolic_mmHg")
        if systolic in (None, "") or diastolic in (None, ""):
            continue
        facts.append(Fact(
            f"EHR-BP-{row.get('Vital_ID')}", person.person_id, "bp", "LOINC", "85354-9", "Blood pressure", on,
            _at(on, 10) if on else None, "ehr", f"EHR Vitals/{row.get('Vital_ID')}", value=(int(systolic), int(diastolic)),
            unit="mmHg", origin="ehr", provider=str(row.get("Recorded_By") or ""), extra={"position": row.get("Position")},
        ))
    for row in person.medications:
        on = parse_date(row.get("Start_Date"))
        facts.append(Fact(
            f"EHR-MED-{row.get('Medication_ID')}", person.person_id, "medication", "RXNORM-NAME", str(row.get("Medication") or "").upper(),
            str(row.get("Medication") or ""), on, _at(on, 8) if on else None, "ehr", f"EHR Medications/{row.get('Medication_ID')}",
            status="final" if str(row.get("Status") or "").lower() == "active" else "inactive", origin="ehr",
            provider=str(row.get("Prescriber") or ""),
        ))
    for problem in person.problems:
        facts.append(Fact(
            f"EHR-DX-{person.person_id}-{problem['code']}", person.person_id, "diagnosis", "ICD-10-CM", problem["code"],
            problem["name"], problem["onset"], _at(problem["onset"]) if problem["onset"] else None, "ehr",
            f"EHR Problems/{problem['code']}", status="final" if problem["active"] else "resolved", origin="ehr",
            provider=problem["provider"],
        ))
    return facts


def ehr_rows_facts(export: dict, person_ids: set[str]) -> dict[str, list[Fact]]:
    """Facts from sheets not carried on Person (orders, claims, immunizations)."""
    sheets = (export or {}).get("sheets", {})
    facts: dict[str, list[Fact]] = {}

    def add(fact: Fact):
        if fact.person_id in person_ids:
            facts.setdefault(fact.person_id, []).append(fact)

    for row in (sheets.get("Orders_Procedures") or {}).get("records") or []:
        on = parse_date(row.get("Order_Date"))
        add(Fact(
            f"EHR-ORD-{row.get('Order_ID')}", str(row.get("Patient_ID")), "procedure", "CPT", str(row.get("CPT_HCPCS") or ""),
            str(row.get("Order_Description") or ""), on, _at(on, 12) if on else None, "ehr", f"EHR Orders/{row.get('Order_ID')}",
            status="final" if str(row.get("Status")) == "Completed" else "ordered", origin="ehr", provider=str(row.get("Ordering_Provider") or ""),
        ))
    for row in (sheets.get("Billing_Claims") or {}).get("records") or []:
        on = parse_date(row.get("Service_Date"))
        add(Fact(
            f"CLM-{row.get('Claim_ID')}", str(row.get("Patient_ID")), "procedure", "CPT", str(row.get("CPT_HCPCS") or ""),
            str(row.get("Description") or ""), on, _at(on + timedelta(days=9)) if on else None, "claims", f"X12 837 {row.get('Claim_ID')}",
            origin="ehr", payer_visible=True, extra={"paid": row.get("Payment_USD"), "charge": row.get("Charge_USD")},
        ))
    for row in (sheets.get("Immunizations") or {}).get("records") or []:
        on = parse_date(row.get("Administration_Date"))
        add(Fact(
            f"EHR-IMM-{row.get('Immunization_ID')}", str(row.get("Patient_ID")), "immunization", "CVX", str(row.get("CVX_Code") or ""),
            str(row.get("Vaccine") or "Vaccine"), on, _at(on, 11) if on else None, "ehr", f"EHR Immunizations/{row.get('Immunization_ID')}",
            origin="ehr", provider=str(row.get("Administered_By") or ""),
        ))
    return facts


# ---------------------------------------------------------------------------
# Synthetic partner feeds
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A possible exclusion found in unstructured text (spec S07) — never counted until confirmed."""

    candidate_id: str
    person_id: str
    measures: tuple
    exclusion: str
    finding: str
    confidence: float
    document: str
    source: str
    page: str
    received: date
    lines: list
    highlight: int
    code: str
    rule_note: str = ""
    complete: bool = True


@dataclass
class Stay:
    stay_id: str
    person_id: str
    facility: str
    admit: date
    discharge: date | None
    expected_readmit: float
    adt_ref: str
    readmission_of: str | None = None


@dataclass
class EdVisit:
    visit_id: str
    person_id: str
    on: date
    diagnosis: str
    conditions: list
    followed_up: date | None


@dataclass
class FeedLayer:
    facts: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    stays: list = field(default_factory=list)
    ed_visits: list = field(default_factory=list)


def _fact(layer: FeedLayer, fact: Fact):
    layer.facts.setdefault(fact.person_id, []).append(fact)


def synthetic_layer(people: list[Person], as_of: date) -> FeedLayer:
    layer = FeedLayer()
    diabetes, hypertension = VALUE_SETS["Diabetes"], VALUE_SETS["Essential Hypertension"]
    seniors = [person for person in people if (person.age or 0) >= 65]
    hospice_person = min((p for p in seniors if (p.age or 0) >= 75), key=lambda p: draw(p.person_id, "hospice"), default=None)
    hospice_note_person = min(
        (p for p in seniors if p is not hospice_person and (p.age or 0) >= 70), key=lambda p: draw(p.person_id, "hospice-note"), default=None,
    )
    for person in people:
        pid, age = person.person_id, person.age or 0
        lakeside = person.lab_source == "Lakeside Outreach Lab"
        if person.has_condition(diabetes) and not lakeside:
            _diabetes_labs(layer, person, as_of)
        if person.has_condition(diabetes):
            _eye_exam(layer, person, as_of)
        if person.sex == "Female" and 40 <= age <= 74:
            _mammography(layer, person, as_of)
        if 45 <= age <= 75:
            _colorectal(layer, person, as_of)
        if person.has_condition(hypertension) and draw(pid, "rpm") < 0.4:
            reading = (126 + int(draw(pid, "rpm-s") * 10), 76 + int(draw(pid, "rpm-d") * 8))
            on = as_of - timedelta(days=4 + int(draw(pid, "rpm-day") * 6))
            _fact(layer, Fact(
                f"RPM-{pid}", pid, "bp", "LOINC", "85354-9", "Home blood pressure (remote monitoring)", on, _at(on, 7, 40), "rpm",
                _msg("RPM", pid, "bp"), value=reading, unit="mmHg", note="Patient-reported via connected cuff",
            ))
        _pharmacy_fills(layer, person, as_of)
        if age >= 66:
            _functional_status(layer, person, as_of)
            if draw(pid, "frailty") < 0.1:
                layer.candidates.append(_frailty_candidate(person, as_of))
    if hospice_person:
        on = date(2026, 8, 12)
        _fact(layer, Fact(
            f"HOSPICE-{hospice_person.person_id}", hospice_person.person_id, "procedure", "HCPCS", "Q5003", "Hospice care (claim)",
            on, _at(on + timedelta(days=10)), "claims", _msg("X12", hospice_person.person_id, "hospice"), payer_visible=True,
            extra={"exclusion": "hospice"},
        ))
    if hospice_note_person:
        layer.candidates.append(Candidate(
            f"EXC-HOSP-{hospice_note_person.person_id}", hospice_note_person.person_id, ("GSD", "CBP", "KED", "EED", "BCS-E", "COL-E"),
            "hospice", "Hospice referral", 0.61, "Clinic progress note", "ehr", "note 14 Sep",
            as_of - timedelta(days=40),
            ["ASSESSMENT/PLAN", "Discussed goals of care with patient and daughter.", "Hospice referral placed; awaiting intake.",
             "Continue current medications."],
            2, "Z51.5", "Referral note only — no hospice claim or enrollment yet. Check hospice status before deciding.",
        ))
    _ensure_review_cases(layer, people, as_of)
    layer.stays = _adt_stays(seniors, as_of)
    layer.ed_visits = _ed_visits(people) + _recent_ed_visit(people, layer.stays, as_of)
    return layer


def _diabetes_labs(layer: FeedLayer, person: Person, as_of: date):
    pid = person.person_id
    if draw(pid, "egfr") < 0.88:
        on = _in_period(pid, "egfr-date", as_of, last=date(2026, 9, 30))
        _fact(layer, Fact(
            f"LIF-EGFR-{pid}", pid, "lab", "LOINC", "98979-8", "eGFR (CKD-EPI 2021)", on, _at(on + timedelta(days=1), 9, 12),
            "lab_interface", f"HL7 ORU {_msg('msg', pid, 'egfr')}", value=55 + int(draw(pid, "egfr-v") * 40),
            unit="mL/min/1.73m2", extra={"lab": person.lab_source},
        ))
    roll = draw(pid, "uacr")
    if roll < 0.78:
        on = _in_period(pid, "uacr-date", as_of, first=date(2026, 3, 1), last=date(2026, 9, 30))
        code, status = "9318-7", "final"
        if 0.55 <= roll < 0.68:
            code = LOCAL_UACR_CODE  # local lab code, not LOINC — rejected on value set (U05)
        elif roll >= 0.68:
            status, on = "preliminary", as_of - timedelta(days=3)  # held until final (U06)
        _fact(layer, Fact(
            f"LIF-UACR-{pid}", pid, "lab", "LOINC" if code != LOCAL_UACR_CODE else "LOCAL", code, "Urine albumin-creatinine ratio",
            on, _at(on + timedelta(days=1), 9, 12), "lab_interface", f"HL7 ORU {_msg('msg', pid, 'uacr')}", status=status,
            value=8 + int(draw(pid, "uacr-v") * 40), unit="mg/g", extra={"lab": person.lab_source},
        ))
    if draw(pid, "a1c-prelim") < 0.2:
        on = as_of - timedelta(days=10)
        _fact(layer, Fact(
            f"LIF-A1C-{pid}", pid, "lab", "LOINC", "4548-4", "Hemoglobin A1c", on, _at(on, 16, 5), "lab_interface",
            f"HL7 ORU {_msg('msg', pid, 'a1c')}", status="preliminary", value=round(6.8 + draw(pid, "a1c-v") * 1.2, 1), unit="%",
            extra={"lab": person.lab_source},
        ))


def _eye_exam(layer: FeedLayer, person: Person, as_of: date):
    pid = person.person_id
    roll = draw(pid, "eye")
    if roll < 0.32:
        on = _in_period(pid, "eye-date", as_of)
        code = "92250" if draw(pid, "eye-code") < 0.6 else "2023F"
        _fact(layer, Fact(
            f"CLM-EYE-{pid}", pid, "procedure", "CPT", code, "Retinal eye exam" if code == "92250" else "Retinal exam, no retinopathy (CPT II)",
            on, _at(on + timedelta(days=12)), "claims", f"X12 837 {_msg('CLM', pid, 'eye')}", payer_visible=True,
            provider="Northstar Eye Associates",
        ))
    elif roll < 0.46:
        on = min(date(2026, 10, 9), as_of - timedelta(days=1))
        _fact(layer, Fact(
            f"HIE-EYE-{pid}", pid, "document", "TEXT", "EYE-EXAM-REPORTED", "Outside retinal exam reported — report requested", on,
            _at(on + timedelta(days=2)), "hie", _msg("HIE", pid, "eye"), structured=False, extra={"reports": "EED"},
            note="Care done elsewhere; waiting for the eye report to arrive",
        ))


def _mammography(layer: FeedLayer, person: Person, as_of: date):
    pid = person.person_id
    roll = draw(pid, "mammo")
    if roll < 0.45:
        on = _in_period(pid, "mammo-date", as_of, first=date(2025, 2, 1), last=date(2026, 9, 30))
        _fact(layer, Fact(
            f"CLM-MAM-{pid}", pid, "procedure", "CPT", "77067", "Screening mammography, bilateral", on, _at(on + timedelta(days=11)),
            "claims", f"X12 837 {_msg('CLM', pid, 'mammo')}", payer_visible=True, provider="Northstar Imaging",
        ))
    elif roll < 0.53:
        on = date(2024, 7, 15)  # outside the 27-month window (U07)
        _fact(layer, Fact(
            f"CLM-MAM-{pid}", pid, "procedure", "CPT", "77067", "Screening mammography, bilateral", on, _at(on + timedelta(days=11)),
            "claims", f"X12 837 {_msg('CLM', pid, 'mammo')}", payer_visible=True, provider="Lakeside Imaging",
        ))
    elif roll < 0.6 and (person.age or 0) >= 50:
        layer.candidates.append(Candidate(
            f"EXC-MAST-{pid}", pid, ("BCS-E",), "bilateral_mastectomy", "Bilateral mastectomy", 0.93,
            "Lakeside Surgical Associates · scanned PDF", "scanned", "page 3 of 7", as_of - timedelta(days=14),
            ["PAST SURGICAL HISTORY", "1. Cholecystectomy, laparoscopic (2009)", "2. Bilateral mastectomy (2015) following diagnosis of ...",
             "3. Right knee arthroscopy (2018)"],
            2, "Z90.13",
        ))


def _colorectal(layer: FeedLayer, person: Person, as_of: date):
    pid = person.person_id
    roll = draw(pid, "col")
    if roll < 0.28:
        on = date(2018, 1, 1) + timedelta(days=int(draw(pid, "col-date") * 3100))
        on = min(on, as_of - timedelta(days=30))
        _fact(layer, Fact(
            f"CLM-COL-{pid}", pid, "procedure", "CPT", "45378", "Colonoscopy, diagnostic", on, _at(on + timedelta(days=14)), "claims",
            f"X12 837 {_msg('CLM', pid, 'col')}", payer_visible=True, provider="Northstar GI",
        ))
    elif roll < 0.36:
        on = _in_period(pid, "fit-date", as_of)
        _fact(layer, Fact(
            f"CLM-FIT-{pid}", pid, "procedure", "CPT", "82274", "FIT (immunochemical fecal occult blood)", on, _at(on + timedelta(days=8)),
            "claims", f"X12 837 {_msg('CLM', pid, 'fit')}", payer_visible=True,
        ))
    elif roll < 0.42:
        # EHR-side record says 2018 (inside the window); the claim says 2016 (outside) — U03.
        _fact(layer, Fact(
            f"HIE-COL-{pid}", pid, "procedure", "CPT", "45378", "Colonoscopy (surgical history, C-CDA)", date(2018, 5, 2),
            _at(as_of - timedelta(days=30)), "hie", _msg("HIE", pid, "col"), conflict_ref=f"CLM-COL16-{pid}",
        ))
        _fact(layer, Fact(
            f"CLM-COL16-{pid}", pid, "procedure", "CPT", "45378", "Colonoscopy (claim)", date(2016, 4, 12), _at(date(2016, 4, 26)), "claims",
            f"X12 837 {_msg('CLM', pid, 'col16')}", payer_visible=True, conflict_ref=f"HIE-COL-{pid}",
        ))
    elif roll < 0.46:
        layer.candidates.append(_colectomy_candidate(person, as_of))


def _colectomy_candidate(person: Person, as_of: date) -> Candidate:
    return Candidate(
        f"EXC-COLX-{person.person_id}", person.person_id, ("COL-E",), "colorectal_cancer", "Total colectomy", 0.88,
        "Operative note · outside hospital", "hie", "page 1 of 2", as_of - timedelta(days=21),
        ["PROCEDURE PERFORMED", "Total abdominal colectomy with ileorectal anastomosis.", "Indication: refractory ulcerative colitis.",
         "Estimated blood loss 150 mL."],
        1, "Z90.49", "Found only in operative note text — structured documentation required.",
    )


def _frailty_candidate(person: Person, as_of: date) -> Candidate:
    return Candidate(
        f"EXC-FRAIL-{person.person_id}", person.person_id, ("GSD", "CBP", "KED", "EED"), "frailty_advanced_illness",
        "Frailty + advanced illness", 0.47, "Home health assessment", "hie", "page 2 of 4", as_of - timedelta(days=12),
        ["FUNCTIONAL ASSESSMENT", "Gait unsteady; uses walker indoors (frailty).",
         "No advanced illness diagnoses documented in this record.", "Plan: PT referral."],
        1, "R26.81", "Frailty found, advanced illness missing — exclusion not met; gap stays open.", complete=False,
    )


def _ensure_review_cases(layer: FeedLayer, people: list[Person], as_of: date) -> None:
    """Small populations may not draw every review scenario; guarantee U08 and the frailty rule appear once."""
    kinds = {candidate.exclusion for candidate in layer.candidates}
    taken = {candidate.person_id for candidate in layer.candidates}
    covered = {fact.person_id for facts in layer.facts.values() for fact in facts if fact.fact_id.startswith(("CLM-COL", "HIE-COL", "CLM-FIT"))}
    if "colorectal_cancer" not in kinds:
        pool = [p for p in people if 45 <= (p.age or 0) <= 75 and p.workable and p.person_id not in taken | covered]
        if pool:
            layer.candidates.append(_colectomy_candidate(min(pool, key=lambda p: draw(p.person_id, "colx")), as_of))
    if "frailty_advanced_illness" not in kinds:
        pool = [p for p in people if (p.age or 0) >= 66 and p.workable and p.person_id not in taken]
        if pool:
            layer.candidates.append(_frailty_candidate(min(pool, key=lambda p: draw(p.person_id, "frail-pick")), as_of))


def _pharmacy_fills(layer: FeedLayer, person: Person, as_of: date):
    """Pharmacy claims for active statins (the EHR medication list records starts, not fills)."""
    for row in person.medications:
        name = str(row.get("Medication") or "")
        if str(row.get("Status") or "").lower() != "active" or not VALUE_SETS["Statin Medications"].contains("RXNORM-NAME", name.upper()):
            continue
        if draw(person.person_id, "statin-fill") < 0.12:
            continue  # active on the list but no dispensing this period — a real SPD gap
        on = _in_period(person.person_id, "statin-fill-date", as_of, first=date(2026, 6, 1))
        _fact(layer, Fact(
            f"RX-{person.person_id}-{row.get('Medication_ID')}", person.person_id, "medication", "RXNORM-NAME", name.upper(),
            f"{name} · 30-day fill", on, _at(on + timedelta(days=2)), "pharmacy", _msg("NCPDP", person.person_id, "statin"),
            payer_visible=True,
        ))


def _functional_status(layer: FeedLayer, person: Person, as_of: date):
    pid = person.person_id
    roll = draw(pid, "fsa")
    if roll < 0.45:
        on = _in_period(pid, "fsa-date", as_of)
        _fact(layer, Fact(
            f"CLM-FSA-{pid}", pid, "procedure", "CPT", "1170F", "Functional status assessed (CPT II)", on, _at(on + timedelta(days=9)),
            "cpt2", f"X12 837 {_msg('CLM', pid, 'fsa')}", payer_visible=True,
        ))
    elif roll < 0.6:
        on = min(date(2026, 10, 10), as_of - timedelta(days=1))
        _fact(layer, Fact(
            f"HIE-FSA-{pid}", pid, "document", "TEXT", "FSA-REPORTED", "Functional assessment done — structured entry awaited", on,
            _at(on + timedelta(days=1)), "hie", _msg("HIE", pid, "fsa"), structured=False, extra={"reports": "COA-FSA"},
        ))


def _expected_readmission(person: Person) -> float:
    chronic = sum(1 for problem in person.problems if problem["active"])
    risk = 0.08 + 0.006 * max((person.age or 65) - 65, 0) + 0.03 * chronic
    if person.has_condition(VALUE_SETS["Diabetes"]):
        risk += 0.04
    return round(min(risk, 0.45), 3)


def _adt_stays(seniors: list[Person], as_of: date) -> list[Stay]:
    stays: list[Stay] = []
    admitted = sorted(seniors, key=lambda person: draw(person.person_id, "adt"))[: max(5, len(seniors) // 4)]
    readmit_person = min(admitted[1:], key=lambda person: draw(person.person_id, "readmit"), default=None)
    for index, person in enumerate(admitted):
        pid = person.person_id
        facility = "Riverside Hospital" if draw(pid, "adt-fac") < 0.5 else "Northstar Medical Center"
        if index == 0:
            admit = as_of - timedelta(days=8)
            stays.append(Stay(f"ADT-{pid}-1", pid, facility, admit, None, _expected_readmission(person), _msg("A01", pid, "now")))
            continue
        if index == 1:  # discharged yesterday: discharge information, engagement and reconciliation still to do (HP-2)
            admit = as_of - timedelta(days=5)
            stays.append(Stay(f"ADT-{pid}-1", pid, facility, admit, as_of - timedelta(days=1), _expected_readmission(person), _msg("A03", pid, "dc")))
            continue
        admit = MP.start + timedelta(days=20 + int(draw(pid, "adt-date") * 255))
        discharge = admit + timedelta(days=2 + int(draw(pid, "adt-los") * 5))
        if discharge >= as_of:
            admit, discharge = as_of - timedelta(days=12), as_of - timedelta(days=7)
        stay = Stay(f"ADT-{pid}-1", pid, facility, admit, discharge, _expected_readmission(person), _msg("A03", pid, "dc"))
        stays.append(stay)
        if person is readmit_person:
            readmit = discharge + timedelta(days=6 + int(draw(pid, "readmit-gap") * 18))
            if readmit + timedelta(days=4) < as_of:
                stays.append(Stay(
                    f"ADT-{pid}-2", pid, facility, readmit, readmit + timedelta(days=4), _expected_readmission(person),
                    _msg("A03", pid, "dc2"), readmission_of=stay.stay_id,
                ))
    return stays


def _fmc_conditions(person: Person) -> list[str]:
    return sorted({
        name for name, prefixes in FMC_CONDITIONS.items()
        for problem in person.problems if problem["active"] and problem["code"].startswith(prefixes)
    })


def _recent_ed_visit(people: list[Person], stays: list[Stay], as_of: date) -> list[EdVisit]:
    """One ED visit from the hospital feed three days ago, follow-up not yet booked (HP-5)."""
    admitted = {stay.person_id for stay in stays}
    eligible = [person for person in people if (person.age or 0) >= 18 and person.workable and person.person_id not in admitted
                and len(_fmc_conditions(person)) >= 2]
    if not eligible:
        return []
    person = min(eligible, key=lambda item: draw(item.person_id, "ed-recent"))
    return [EdVisit(f"ED-{person.person_id}-ADT", person.person_id, as_of - timedelta(days=3), "Chest pain, unspecified (R07.9)",
                    _fmc_conditions(person), None)]


def _ed_visits(people: list[Person]) -> list[EdVisit]:
    visits = []
    for person in people:
        conditions = _fmc_conditions(person)
        for encounter in person.encounters:
            if str(encounter.get("Type")) != "Emergency":
                continue
            on = parse_date(encounter.get("Date"))
            if not on:
                continue
            follow_up = next((
                parse_date(other.get("Date")) for other in person.encounters
                if other is not encounter and str(other.get("Type")) in ("Office Visit", "Annual Wellness", "Outpatient")
                and on < (parse_date(other.get("Date")) or date.min) <= on + timedelta(days=7)
            ), None)
            visits.append(EdVisit(
                str(encounter.get("Encounter_ID")), person.person_id, on, str(encounter.get("Primary_Diagnosis") or ""), conditions, follow_up,
            ))
    return visits


# ---------------------------------------------------------------------------
# Feed health and vendor data
# ---------------------------------------------------------------------------


def feed_registry(now: datetime) -> list[dict]:
    """Last message per feed; stale feeds turn dependent gaps UNKNOWN, never OPEN."""
    return [
        {"feed": "EHR nightly extract", "kind": "EHR", "last": now - timedelta(hours=22), "expected_hours": 26},
        {"feed": "Northstar Reference Lab · HL7 ORU", "kind": "Lab", "last": now - timedelta(hours=20), "expected_hours": 48},
        {"feed": "Lakeside Outreach Lab · HL7 ORU", "kind": "Lab", "last": now - timedelta(days=11), "expected_hours": 72},
        {"feed": "Claims · X12 837 (Payer A/B)", "kind": "Claims", "last": now - timedelta(days=6), "expected_hours": 24 * 7},
        {"feed": "Riverside Hospital ADT", "kind": "ADT", "last": now - timedelta(hours=26), "expected_hours": 12},
        {"feed": "Northstar ADT", "kind": "ADT", "last": now - timedelta(hours=1), "expected_hours": 12},
        {"feed": "State HIE · C-CDA", "kind": "HIE", "last": now - timedelta(days=2), "expected_hours": 24 * 4},
    ]


SURVEY_DATA = {
    "integrity": "Aggregate, read-only insights from approved survey vendors. No individual targeting. No coaching. No pre-screening.",
    "cahps": [
        {"measure": "Getting needed care", "score": 79, "n": 412, "change": -2, "direction": "down 2 vs last wave"},
        {"measure": "Getting appointments quickly", "score": 76, "n": 398, "change": 0, "direction": "flat"},
        {"measure": "Care coordination", "score": 84, "n": 405, "change": 1, "direction": "up 1"},
    ],
    "hos": [
        {"measure": "Reducing the risk of falling", "score": 54, "n": 61, "reliable": False},
        {"measure": "Monitoring physical activity", "score": 51, "n": 58, "reliable": False},
    ],
    "themes": [
        {"theme": "Long wait for specialist appointments (orthopedics)", "mentions": 48, "linked": "Getting needed care", "owner": "Access operations · referral team"},
        {"theme": "Not told about test results", "mentions": 31, "linked": "Care coordination", "owner": "Clinic ops · results workflow"},
        {"theme": "Hard to reach the clinic by phone", "mentions": 27, "linked": "Getting appointments quickly", "owner": "Contact centre lead"},
        {"theme": "Unclear medication changes after hospital stay", "mentions": 14, "linked": "Care coordination", "owner": "Transitions team (S10)"},
    ],
    "reliability_threshold": 100,
}

# Time-driven activity-based costing for one diabetes care cycle (process map minutes × cost rate).
TDABC_DIABETES_CYCLE = (
    ("Physician / APP visit time", 30, 4.10),
    ("Medical assistant (vitals, standing orders)", 34, 0.95),
    ("Care manager outreach and follow-up", 20, 1.20),
    ("Lab draw and processing", 1, 48.0),
    ("Retinal imaging", 1, 65.0),
)
