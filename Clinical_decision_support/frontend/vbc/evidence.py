"""Evidence model and the eight deterministic checks (spec S06).

Every fact — EHR row, lab message, claim, document, abstraction — is normalised into one
`Fact` with its provenance. A gap closes only on facts whose checks all pass; preliminary
results are held (not rejected); conflicts go to a person; nothing is probabilistic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from frontend.vbc.catalog import (
    FINAL_RESULT_STATUSES,
    REASON_CODES,
    SOURCES,
    VALUE_SETS,
    VERDICT_ACCEPTED,
    VERDICT_HELD,
    VERDICT_REJECTED,
    VERDICT_REVIEW,
    Component,
    MeasurementPeriod,
)

IDENTITY_THRESHOLD = 0.95
PRELIMINARY_STATUSES = ("preliminary", "pending", "registered", "partial")


@dataclass
class Fact:
    fact_id: str
    person_id: str
    kind: str  # lab | bp | procedure | diagnosis | medication | immunization | document
    system: str  # LOINC | CPT | ICD-10-CM | CVX | HCPCS | RXNORM-NAME | TEXT
    code: str
    description: str
    on: date | None
    received: datetime | None
    source: str  # key in catalog.SOURCES
    source_ref: str  # message id, claim id or document reference
    status: str = "final"
    value: object = None
    unit: str = ""
    structured: bool = True
    identity_confidence: float = 1.0
    conflict_ref: str | None = None
    origin: str = "feed"  # ehr | feed | workspace
    note: str = ""
    provider: str = ""
    payer_visible: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def source_label(self) -> str:
        source = SOURCES.get(self.source)
        return f"{source.label} · {source.format}" if source else self.source

    @property
    def display_value(self) -> str:
        if self.kind == "bp" and isinstance(self.value, (tuple, list)):
            return f"{self.value[0]}/{self.value[1]} mmHg"
        if self.value in (None, ""):
            return self.description
        return f"{self.value} {self.unit}".strip()

    def headline(self) -> str:
        when = self.on.strftime("%d %b %Y") if self.on else "undated"
        return f"{self.description} · {self.display_value} · {when}" if self.value not in (None, "") else f"{self.description} · {when}"


@dataclass
class Check:
    key: str
    label: str
    code: str
    passed: bool
    detail: str


@dataclass
class Verdict:
    fact: Fact
    measure: str
    component: str
    checks: list[Check]
    verdict: str
    reason_code: str | None
    owner: str
    decided_by: str | None = None

    @property
    def accepted(self) -> bool:
        return self.verdict == VERDICT_ACCEPTED

    @property
    def summary(self) -> str:
        if self.accepted:
            return "Accepted — all eight checks pass"
        meaning = REASON_CODES.get(self.reason_code or "", ("", ""))[0]
        return f"{self.verdict.replace('_', ' ').title()} · {self.reason_code}: {meaning}"


def component_window(component: Component, period: MeasurementPeriod) -> tuple[date, date]:
    start = component.window_start_override or date(period.start.year - component.lookback_years, 1, 1)
    return start, component.window_end_override or period.end


def run_checks(
    fact: Fact,
    measure_code: str,
    component: Component,
    period: MeasurementPeriod,
    allowed_sources: tuple,
    decisions: dict | None = None,
) -> Verdict:
    """Apply the eight checks in order; the first failure decides the verdict."""
    decisions = decisions or {}
    start, end = component_window(component, period)
    value_sets = [VALUE_SETS[name] for name in component.value_sets if name in VALUE_SETS]
    in_value_set = any(value_set.contains(fact.system, fact.code) for value_set in value_sets)
    decision = decisions.get(fact.fact_id)
    conflict_resolved = bool(decision and decision.get("decision") == "accepted")
    has_value = fact.value not in (None, "") or fact.kind in ("procedure", "diagnosis", "immunization", "medication")
    checks = [
        Check("present", "Present", "PRS", bool(fact.code) and has_value, "Value and code present" if has_value else "No result value"),
        Check("value_set", "Value-set match", "VSM", in_value_set,
              f"{fact.system} {fact.code} in {', '.join(component.value_sets)}" if in_value_set
              else f"{fact.system} {fact.code} is not in {', '.join(component.value_sets)}"),
        Check("structured", "Structured", "STR", fact.structured, "Coded, structured data" if fact.structured else "Free text — needs structured entry"),
        Check("final", "Final status", "STS", fact.status.lower() in FINAL_RESULT_STATUSES,
              "Final" if fact.status.lower() in FINAL_RESULT_STATUSES else fact.status.title()),
        Check("in_window", "In window", "WIN", bool(fact.on and start <= fact.on <= end),
              f"{fact.on:%d %b %Y} within {start:%d %b %Y} – {end:%d %b %Y}" if fact.on and start <= fact.on <= end
              else f"{fact.on:%d %b %Y} outside {start:%d %b %Y} – {end:%d %b %Y}" if fact.on else "Undated"),
        Check("person_linked", "Person-linked", "IDN", fact.identity_confidence >= IDENTITY_THRESHOLD,
              f"Identity confidence {fact.identity_confidence:.2f}"),
        Check("source_permitted", "Source permitted", "SRC", fact.source in allowed_sources,
              f"{SOURCES[fact.source].label if fact.source in SOURCES else fact.source} permitted for this measure"
              if fact.source in allowed_sources else "Source not permitted for this data method"),
        Check("unconflicted", "Unconflicted", "CFL", not fact.conflict_ref or conflict_resolved,
              "No conflicting source" if not fact.conflict_ref
              else f"Resolved by {decision.get('by')}" if conflict_resolved else f"Conflicts with {fact.conflict_ref}"),
    ]
    failed = next((check for check in checks if not check.passed), None)
    if decision and decision.get("decision") == "rejected":
        verdict, code = VERDICT_REJECTED, "CFL"
    elif failed is None:
        verdict, code = VERDICT_ACCEPTED, None
    elif failed.code == "STS" and fact.status.lower() in PRELIMINARY_STATUSES:
        verdict, code = VERDICT_HELD, "STS"  # held, not rejected: re-checked when the final result posts
    elif failed.code == "CFL":
        verdict, code = VERDICT_REVIEW, "CFL"
    else:
        verdict, code = VERDICT_REJECTED, failed.code
    owner = REASON_CODES[code][1] if code else "—"
    return Verdict(fact, measure_code, component.key, checks, verdict, code, owner,
                   decided_by=(decision or {}).get("by"))

