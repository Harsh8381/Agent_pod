"""Evidence normalization, de-duplication and validity checks (stages 5-6 of the evaluation model).

Raw events become canonical concepts with provenance. Duplicates (e.g. a lab result and the claim
for the same draw) are linked to one primary record and counted once. Each candidate then gets a
verdict for the measure being evaluated:

  accepted   counts toward the numerator/outcome
  rejected   valid record that does not satisfy this rule (outside window, wrong procedure, not done)
  review     plausible but unverified, external, narrative or missing a value  -> evidence review
  invalid    impossible, undated, future, wrong unit or identity mismatch       -> data-quality issue
  duplicate  same service recorded twice; the primary record carries the decision
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.vbc_poc.catalog import (
    ACCEPTED_BP_CONTEXTS,
    CONCEPT_CODES,
    CONCEPT_LABELS,
    CONCEPT_PREFIXES,
    PERIOD,
    PLAUSIBLE,
    SOURCES,
    VALID_UNITS,
    Measure,
)
from app.vbc_poc.dataset import Event, Patient

RELATED_CONCEPTS = {"BCS-P": ("breast_ultrasound",)}  # look-alike evidence that must be rejected, not ignored
REASONS = {
    "ACCEPTED": "Meets every evidence requirement for this rule",
    "NARRATIVE": "Free-text mention only — needs a structured record or the report",
    "NOT_COMPLETED": "Ordered or scheduled, not completed — an order never closes a gap",
    "INCOMPLETE": "Procedure incomplete or cancelled",
    "MISSING_DATE": "No service date on the record",
    "FUTURE_DATE": "Service date is in the future",
    "IDENTITY_MISMATCH": "Date of birth on the record does not match the patient",
    "NO_VALUE": "Test billed or reported without a result value",
    "UNIT": "Unexpected unit for this result",
    "IMPLAUSIBLE": "Value outside the plausible range",
    "INCOMPLETE_READING": "Systolic or diastolic value missing",
    "CONTEXT": "Inpatient or emergency-department reading — not acceptable for this rule",
    "UNACCEPTED_HOME": "Home reading not device-transmitted",
    "NOT_IN_VALUE_SET": "Procedure is not an accepted screening modality",
    "OUTSIDE_WINDOW": "Dated outside the permitted lookback window",
    "PENDING_VERIFICATION": "External or uploaded evidence awaiting verification",
    "REJECTED_BY_REVIEWER": "Rejected by a reviewer",
    "DUPLICATE": "Same service recorded twice — counted once via the primary record",
}
DATA_QUALITY_CODES = ("MISSING_DATE", "FUTURE_DATE", "IDENTITY_MISMATCH", "UNIT", "IMPLAUSIBLE", "INCOMPLETE_READING")


def concept_of(event: Event) -> str | None:
    concept = CONCEPT_CODES.get((event.code_system, event.code))
    if concept:
        return concept
    return next((name for (system, prefix), name in CONCEPT_PREFIXES.items()
                 if event.code_system == system and event.code.upper().startswith(prefix)), None)


@dataclass
class Evidence:
    event: Event
    concept: str
    duplicate_of: str | None = None
    verdict: str = ""
    code: str = ""

    @property
    def reason(self) -> str:
        return REASONS.get(self.code, self.code)

    def to_dict(self) -> dict:
        event = self.event
        return {
            "event_id": event.event_id, "concept": self.concept, "concept_label": CONCEPT_LABELS.get(self.concept, self.concept),
            "display": event.display, "date": event.event_date.isoformat() if event.event_date else None,
            "recorded_at": event.recorded_at.isoformat(), "value": event.value, "value2": event.value2, "unit": event.unit,
            "source": event.source, "source_label": SOURCES.get(event.source, (event.source, 9))[0], "source_record_id": event.source_record_id,
            "status": event.status, "verification": event.verification, "context": event.context, "result_flag": event.result_flag,
            "note": event.note, "verdict": self.verdict, "reason_code": self.code, "reason": self.reason, "duplicate_of": self.duplicate_of,
        }


def _rank(event: Event) -> int:
    return SOURCES.get(event.source, ("", 9))[1]


def normalize(events: list[Event], concepts: tuple) -> list[Evidence]:
    """Canonical evidence for the given concepts, with duplicate records linked to one primary."""
    items = [Evidence(event, concept) for event in events
             if event.event_type in ("observation", "procedure", "order", "document") and (concept := concept_of(event)) in concepts]
    groups: dict[tuple, list[Evidence]] = {}
    for item in items:
        if item.event.event_date and item.event.source != "narrative" and item.event.event_type != "order":
            groups.setdefault((item.concept, item.event.event_date), []).append(item)
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (_rank(item.event), item.event.value is None, item.event.event_id))
        primary = group[0]
        for other in group[1:]:
            same_value = other.event.value is None or primary.event.value is None or (
                other.event.value == primary.event.value and other.event.value2 == primary.event.value2)
            # Several readings from the same source on one day are real repeat readings, not duplicates.
            if same_value and other.event.source != primary.event.source:
                other.duplicate_of = primary.event.event_id
    return items


def assess(item: Evidence, measure: Measure, patient: Patient, as_of: date) -> Evidence:
    """Stage 6: is this evidence complete, timely, attributable and acceptable for this rule?"""
    event = item.event
    item.verdict, item.code = _verdict(item, event, measure, patient, as_of)
    return item


def _verdict(item: Evidence, event: Event, measure: Measure, patient: Patient, as_of: date) -> tuple[str, str]:
    if item.duplicate_of:
        return "duplicate", "DUPLICATE"
    if event.source == "narrative":
        return "review", "NARRATIVE"
    if event.event_type == "order" or event.status in ("ordered", "scheduled"):
        return "rejected", "NOT_COMPLETED"
    if event.status in ("incomplete", "cancelled", "entered-in-error"):
        return "rejected", "INCOMPLETE"
    if event.verification == "rejected":
        return "rejected", "REJECTED_BY_REVIEWER"
    if event.event_date is None:
        return "invalid", "MISSING_DATE"
    if event.event_date > as_of:
        return "invalid", "FUTURE_DATE"
    if event.dob_on_record and patient.dob and event.dob_on_record != patient.dob:
        return "invalid", "IDENTITY_MISMATCH"
    if item.concept == "hba1c" and event.event_type == "observation":
        if event.unit and event.unit not in VALID_UNITS["hba1c"]:
            return "invalid", "UNIT"
        if event.value is not None and not (PLAUSIBLE["hba1c"][0] <= event.value <= PLAUSIBLE["hba1c"][1]):
            return "invalid", "IMPLAUSIBLE"
    if item.concept == "bp":
        if event.value is None or event.value2 is None:
            return "invalid", "INCOMPLETE_READING"
        low_s, high_s = PLAUSIBLE["systolic"]
        low_d, high_d = PLAUSIBLE["diastolic"]
        if not (low_s <= event.value <= high_s and low_d <= event.value2 <= high_d and event.value > event.value2):
            return "invalid", "IMPLAUSIBLE"
        if event.context not in ACCEPTED_BP_CONTEXTS:
            return "rejected", "CONTEXT"
        if event.context == "home" and event.source != "patient_device":
            return "rejected", "UNACCEPTED_HOME"
    if item.concept not in measure.evidence_concepts:
        return "rejected", "NOT_IN_VALUE_SET"
    start = measure.lookbacks.get(item.concept, PERIOD.start)
    if not (start <= event.event_date <= PERIOD.end):
        return "rejected", "OUTSIDE_WINDOW"
    if event.verification == "pending":
        return "review", "PENDING_VERIFICATION"
    if item.concept == "hba1c" and event.value is None:
        return "review", "NO_VALUE"
    return "accepted", "ACCEPTED"
