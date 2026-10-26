"""Person records for value-based care, assembled from the EHR export.

Identity, coverage, attribution, preferences and barriers are resolved here so every screen
works from the same person view (spec S05: "who this is" before any gap).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime

from frontend.vbc.catalog import MP, PAYER_BY_NAME


def parse_date(value) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:19]).date()
    except ValueError:
        pass
    for pattern in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    return None


def draw(person_id: str, salt: str) -> float:
    """Deterministic pseudo-random number in [0, 1) for the synthetic demo layers."""
    digest = hashlib.sha256(f"vbc-demo:{salt}:{person_id}".encode()).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def age_on(dob: date | None, on: date) -> int | None:
    if dob is None:
        return None
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


@dataclass
class Person:
    person_id: str
    person_key: str
    name: str
    sex: str
    dob: date | None
    age: int | None  # as of the end of the measurement period (HEDIS convention)
    language: str
    pcp: str
    attributed_since: date | None
    site: str
    payer_key: str | None
    payer_name: str
    member_id: str
    plan_type: str
    product_line: str
    coverage: str = "continuous"
    coverage_note: str = ""
    identity: str = "confident"
    identity_note: str = ""
    attribution_ended: date | None = None
    lab_source: str = "Northstar Reference Lab"
    preferences: dict = field(default_factory=dict)
    barriers: list = field(default_factory=list)
    consent_sms: bool = False
    consent_phone: bool = True
    mobile: str = ""
    upcoming_visit: dict | None = None
    problems: list = field(default_factory=list)
    medications: list = field(default_factory=list)
    vitals: list = field(default_factory=list)
    labs: list = field(default_factory=list)
    encounters: list = field(default_factory=list)
    allergies: list = field(default_factory=list)

    @property
    def initials(self) -> str:
        parts = [part for part in self.name.split() if part]
        return "".join(part[0] for part in parts[:2]).upper() or "?"

    @property
    def label(self) -> str:
        age = f"{self.age}" if self.age is not None else "?"
        return f"{self.name} · {age} {self.sex[:1] if self.sex else ''}".strip()

    def has_condition(self, value_set) -> bool:
        return any(
            value_set.contains("ICD-10-CM", problem["code"]) and problem["active"]
            for problem in self.problems
        )

    @property
    def workable(self) -> bool:
        """Identity and coverage are resolved, so gaps may be shown and worked."""
        return self.identity == "confident" and self.coverage == "continuous"


def _rows(export: dict, sheet: str) -> list[dict]:
    return ((export or {}).get("sheets", {}).get(sheet) or {}).get("records") or []


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("Patient_ID") or ""), []).append(row)
    return grouped


def build_population(export: dict, as_of: date) -> list[Person]:
    """Build one Person per EHR patient, with demo identity/coverage edge cases layered in."""
    sheets = {name: _group(_rows(export, name)) for name in (
        "Problems", "Medications", "Vitals", "Labs", "Encounters", "Insurance", "Appointments",
        "Care_Team", "Consent_Directives", "Family_Social_Hx", "Allergies",
    )}
    people = []
    for row in _rows(export, "Patient_Master"):
        person_id = str(row.get("Patient_ID") or "").strip()
        if not person_id:
            continue
        name = str(row.get("Legal_Name") or "").strip() or " ".join(
            part for part in (str(row.get("First_Name") or "").strip(), str(row.get("Last_Name") or "").strip()) if part
        ) or person_id
        dob = parse_date(row.get("DOB"))
        age = age_on(dob, MP.end)
        coverage = next(
            (item for item in sheets["Insurance"].get(person_id, []) if str(item.get("Priority", "")).lower() == "primary"),
            (sheets["Insurance"].get(person_id) or [{}])[0],
        )
        payer = PAYER_BY_NAME.get(str(coverage.get("Payer_Name") or ""))
        pcp_row = next((item for item in sheets["Care_Team"].get(person_id, []) if item.get("Role") == "PCP"), {})
        consent = (sheets["Consent_Directives"].get(person_id) or [{}])[0]
        social = (sheets["Family_Social_Hx"].get(person_id) or [{}])[0]
        appointments = sorted(
            (item for item in sheets["Appointments"].get(person_id, [])
             if str(item.get("Status")) == "Scheduled" and (parse_date(item.get("Date")) or date.min) >= as_of),
            key=lambda item: (parse_date(item.get("Date")), str(item.get("Time") or "")),
        )
        encounters = sorted(sheets["Encounters"].get(person_id, []), key=lambda item: str(item.get("Date") or ""))
        site = (appointments[0].get("Location") if appointments else "") or (encounters[-1].get("Facility") if encounters else "") or "Northstar Medical Center"
        person = Person(
            person_id=person_id,
            person_key=f"PK-{person_id[-6:].zfill(6)}",
            name=name,
            sex=str(row.get("Sex_at_Birth") or ""),
            dob=dob,
            age=age,
            language=str(row.get("Preferred_Language") or "English"),
            pcp=str(row.get("PCP") or pcp_row.get("Provider") or "Unattributed"),
            attributed_since=parse_date(pcp_row.get("Start_Date")),
            site=site if site != "NuuCare Demo" else "Northstar Medical Center",
            payer_key=payer.key if payer else None,
            payer_name=payer.label if payer else "No coverage",
            member_id=str(coverage.get("Member_ID") or ""),
            plan_type=str(coverage.get("Plan_Type") or ""),
            product_line="Medicare Advantage" if (age or 0) >= 65 else "Commercial",
            consent_sms=str(consent.get("Portal_Consent") or "").lower() == "active",
            consent_phone=str(consent.get("Status") or "Active").lower() == "active",
            mobile=str(row.get("Mobile") or ""),
            upcoming_visit=_visit(appointments[0]) if appointments else None,
            problems=[_problem(item) for item in sheets["Problems"].get(person_id, [])],
            medications=sheets["Medications"].get(person_id, []),
            vitals=sorted(sheets["Vitals"].get(person_id, []), key=lambda item: str(item.get("Observed_Date") or "")),
            labs=sheets["Labs"].get(person_id, []),
            encounters=encounters,
            allergies=sheets["Allergies"].get(person_id, []),
        )
        person.preferences = {
            "channel": "SMS" if person.consent_sms else "Phone",
            "time": "AM" if draw(person_id, "pref-time") < 0.55 else "PM",
            "language": person.language,
        }
        if str(social.get("Social_Risk") or "").lower() == "moderate":
            person.barriers.append("transport" if draw(person_id, "barrier") < 0.6 else "cost")
        if not person.mobile:
            person.consent_sms = False
        people.append(person)
    _apply_edge_cases(people)
    return people


def _visit(row: dict) -> dict:
    return {
        "date": parse_date(row.get("Date")),
        "time": str(row.get("Time") or ""),
        "provider": str(row.get("Provider") or ""),
        "location": str(row.get("Location") or ""),
        "reason": str(row.get("Reason") or ""),
        "appointment_id": str(row.get("Appointment_ID") or ""),
    }


def _problem(row: dict) -> dict:
    return {
        "code": str(row.get("ICD_10_CM") or "").strip(),
        "name": str(row.get("Problem_Name") or ""),
        "active": str(row.get("Clinical_Status") or "Active").lower() == "active",
        "onset": parse_date(row.get("Onset_Date")),
        "provider": str(row.get("Provider") or ""),
    }


def _apply_edge_cases(people: list[Person]) -> None:
    """Deterministic identity, enrollment, attribution and feed edge cases (spec U01, U02, U04)."""
    adults = sorted((person for person in people if (person.age or 0) >= 18 and person.dob), key=lambda person: draw(person.person_id, "edge"))
    if adults:
        adults[0].identity = "review"
        adults[0].identity_note = "Two records share name and date of birth with different addresses — sent to the MPI steward."
    if len(adults) > 1:
        adults[1].coverage = "gap"
        adults[1].coverage_note = "Enrollment file missing for October — plan-level status unknown."
    for person in adults[2:5]:
        person.attribution_ended = date(2026, 7, 1)
    # Two members draw labs at an outreach lab whose interface goes quiet (U04).
    diabetic = [
        person for person in adults
        if any(problem["code"].startswith(("E10", "E11", "E13")) for problem in person.problems) and not person.upcoming_visit
    ]
    others = [person for person in adults[2:] if person not in diabetic]
    for group in (diabetic, others):
        if group:
            min(group, key=lambda person: draw(person.person_id, "lab-source")).lab_source = "Lakeside Outreach Lab"
