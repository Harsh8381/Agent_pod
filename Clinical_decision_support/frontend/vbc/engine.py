"""HEDIS measure evaluation: person × measure → one of the seven gap statuses, with the why.

Rules the whole product rests on: unknown is not open, and nothing closes without evidence
that passes every check. The same engine produces the provider view (all permitted sources)
and the payer view (only what the payer can see), which is how reconciliation explains
disagreements instead of arguing about them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from frontend.vbc.catalog import (
    DENOMINATOR_STATUSES,
    EXCLUDED,
    GAP_CLOSED,
    GAP_OPEN,
    GAP_UNKNOWN,
    MEASURES,
    MP,
    NOT_IN_POPULATION,
    PENDING_EVIDENCE,
    PERSON_MEASURES,
    PREFERENCE_RECORDED,
    VALUE_SETS,
    VERDICT_HELD,
    VERDICT_REJECTED,
    VERDICT_REVIEW,
    WORKABLE_STATUSES,
    Measure,
)
from frontend.vbc.evidence import Fact, Verdict, run_checks
from frontend.vbc.feeds import EdVisit, FeedLayer, Stay
from frontend.vbc.population import Person, draw

CARE_MANAGERS = ("P. Nair", "J. Ortiz", "L. Brooks", "S. Okafor")
LAB_DEPENDENT = ("GSD", "KED")
EXCLUSION_DIAGNOSES = {
    "palliative": VALUE_SETS["Palliative Care"],
    "esrd": VALUE_SETS["ESRD / Dialysis"],
    "bilateral_mastectomy": VALUE_SETS["Bilateral Mastectomy"],
    "colorectal_cancer": VALUE_SETS["Colorectal Cancer / Total Colectomy"],
}
POPULATION_CONDITION = {"GSD": "Diabetes", "KED": "Diabetes", "EED": "Diabetes", "SPD": "Diabetes", "CBP": "Essential Hypertension"}
URGENCY_BASE = {"KED": 0.55, "EED": 0.5, "SPD": 0.5, "BCS-E": 0.4, "COL-E": 0.4, "COA-FSA": 0.35, "AIS-E": 0.25}


@dataclass
class Spec:
    """Active measure-version parameters: base specification plus published change requests."""

    version: str
    overrides: dict = field(default_factory=dict)

    def param(self, measure: str, key: str, default):
        return self.overrides.get(measure, {}).get(key, default)


@dataclass
class EvalContext:
    as_of: date
    spec: Spec
    state: dict
    stale_labs: set = field(default_factory=set)
    payer_view: bool = False
    payer_visible: set = field(default_factory=set)

    @property
    def decisions(self) -> dict:
        return self.state.get("evidence_decisions", {})

    @property
    def quarantined(self) -> set:
        return {fact_id for fact_id, item in self.state.get("quarantine", {}).items() if not item.get("restored")}


@dataclass
class ComponentResult:
    key: str
    label: str
    met: bool
    detail: str
    verdict: Verdict | None = None


@dataclass
class MeasureResult:
    person_id: str
    measure: str
    status: str
    reason: str
    next_action: str = ""
    route: str = "none"  # visit | outreach | evidence | records | await | review | transition | none
    owner: str = ""
    components: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    exclusion: str | None = None
    exclusion_fact: Fact | None = None
    pending: list = field(default_factory=list)
    urgency: float = 0.0
    urgency_note: str = ""
    likelihood: float = 0.0
    closing: Verdict | None = None
    cases: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.person_id}:{self.measure}"

    @property
    def in_denominator(self) -> bool:
        return self.status in DENOMINATOR_STATUSES

    @property
    def met(self) -> bool:
        return self.status == GAP_CLOSED

    @property
    def workable(self) -> bool:
        return self.status in WORKABLE_STATUSES


@dataclass
class TransitionCase:
    stay: Stay
    person_id: str
    components: dict  # NIA/RDI/PED/MRP -> {"status", "label", "deadline", "met"}
    readmitted: bool = False

    @property
    def met(self) -> bool:
        return all(item["met"] for item in self.components.values())

    @property
    def open_components(self) -> list:
        return [name for name, item in self.components.items() if not item["met"] and item["label"] != "Missed"]


@dataclass
class FmcCase:
    visit: EdVisit
    person_id: str
    status: str  # Done | Booked | Due | Missed
    due: date
    note: str = ""

    @property
    def met(self) -> bool:
        return self.status == "Done"


@dataclass
class Rate:
    code: str
    initial: int = 0
    exclusions: Counter = field(default_factory=Counter)
    denominator: int = 0
    numerator: int = 0
    value: float | None = None
    components: dict = field(default_factory=dict)
    statuses: Counter = field(default_factory=Counter)
    rejections: Counter = field(default_factory=Counter)
    observed: int = 0
    expected: float = 0.0


@dataclass
class Evaluation:
    results: dict
    trc_cases: list
    fmc_cases: list
    pcr_stays: list
    context: EvalContext

    def for_person(self, person_id: str) -> list[MeasureResult]:
        return [result for (pid, _), result in self.results.items() if pid == person_id]

    def for_measure(self, code: str) -> list[MeasureResult]:
        return [result for (_, measure), result in self.results.items() if measure == code]

    def get(self, person_id: str, code: str) -> MeasureResult | None:
        return self.results.get((person_id, code))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fact_available(fact: Fact, ctx: EvalContext) -> bool:
    arrived = fact.received.date() if isinstance(fact.received, datetime) else fact.on
    if arrived and arrived > ctx.as_of:
        return False
    return not ctx.payer_view or fact.payer_visible or fact.fact_id in ctx.payer_visible


def care_manager_for(person: Person) -> str:
    return CARE_MANAGERS[int(draw(person.pcp, "cm") * len(CARE_MANAGERS))]


def _fmt(day: date | None) -> str:
    return day.strftime("%d %b") if day else "undated"


def _component_facts(facts: list[Fact], component) -> list[Fact]:
    value_sets = [VALUE_SETS[name] for name in component.value_sets if name in VALUE_SETS]
    return [
        fact for fact in facts
        if fact.kind != "document"
        and (fact.extra.get("component") == component.key or any(vs.contains(fact.system, fact.code) for vs in value_sets))
    ]


def _verdicts(person: Person, measure: Measure, facts: list[Fact], ctx: EvalContext) -> dict[str, list[Verdict]]:
    allowed = tuple(measure.allowed_sources) + (("rpm",) if ctx.spec.param(measure.code, "allow_rpm", False) else ())
    quarantined = ctx.quarantined
    by_component = {}
    for component in measure.components:
        verdicts = []
        for fact in _component_facts(facts, component):
            verdict = run_checks(fact, measure.code, component, MP, allowed, ctx.decisions)
            if fact.fact_id in quarantined and verdict.accepted:
                verdict.verdict, verdict.reason_code, verdict.owner = VERDICT_REJECTED, "LIN", "HEDIS analyst"
            verdicts.append(verdict)
        verdicts.sort(key=lambda item: item.fact.on or date.min)
        by_component[component.key] = verdicts
    return by_component


def in_population(person: Person, measure: Measure, ctx: EvalContext) -> tuple[bool, str]:
    age_min = ctx.spec.param(measure.code, "age_min", measure.age_min)
    if person.age is None:
        return False, "No date of birth on record"
    if not (age_min <= person.age <= measure.age_max):
        return False, f"Age {person.age} is outside {age_min}–{measure.age_max if measure.age_max < 120 else '+'}"
    if measure.sex and person.sex != measure.sex:
        return False, f"Measure applies to {measure.sex.lower()} members"
    if not person.payer_key:
        return False, "No enrollment on file"
    condition = POPULATION_CONDITION.get(measure.code)
    if condition and not person.has_condition(VALUE_SETS[condition]):
        return False, f"No {condition.lower()} diagnosis"
    if measure.code == "COA-FSA" and person.product_line != "Medicare Advantage":
        return False, "Medicare Advantage members only"
    return True, ""


def exclusion_for(person: Person, measure: Measure, facts: list[Fact], ctx: EvalContext) -> tuple[str | None, Fact | None]:
    for fact in facts:
        if fact.status.lower() not in ("final", "corrected", "amended") or not fact.structured or fact.identity_confidence < 0.95:
            continue
        if fact.fact_id in ctx.quarantined:
            continue
        key = fact.extra.get("exclusion")
        if not key and fact.kind == "diagnosis":
            key = next((name for name, value_set in EXCLUSION_DIAGNOSES.items() if value_set.contains("ICD-10-CM", fact.code)), None)
        if key == "hospice" and fact.on and not (MP.start <= fact.on <= MP.end):
            continue
        if key and key in measure.exclusions:
            return key, fact
    return None, None


# ---------------------------------------------------------------------------
# Numerators
# ---------------------------------------------------------------------------


def _accepted(verdicts: list[Verdict]) -> list[Verdict]:
    return [verdict for verdict in verdicts if verdict.accepted]


def _numerator(measure: Measure, verdicts: dict[str, list[Verdict]]) -> tuple[bool, list[ComponentResult], Verdict | None]:
    code = measure.code
    components = []
    if code == "GSD":
        accepted = _accepted(verdicts["glycemic"])
        latest = accepted[-1] if accepted else None
        met = False
        if latest is not None:
            fact = latest.fact
            if fact.kind == "lab":
                value = float(fact.value)
                met = value < 8.0
                detail = f"Most recent HbA1c {value:g}% on {_fmt(fact.on)}" + ("" if met else " (target <8.0%)")
            else:
                met = fact.code in ("3044F", "3051F")
                detail = f"CPT II {fact.code} on {_fmt(fact.on)}"
        else:
            detail = "No glycemic status result this period"
        components.append(ComponentResult("glycemic", "Glycemic status", met, detail, latest))
        return met, components, latest if met else None
    if code == "CBP":
        accepted = [verdict for verdict in _accepted(verdicts["bp"]) if verdict.fact.kind == "bp"]
        if not accepted:
            components.append(ComponentResult("bp", "Most recent BP", False, "No blood pressure reading this period"))
            return False, components, None
        latest_day = max(verdict.fact.on for verdict in accepted)
        same_day = [verdict for verdict in accepted if verdict.fact.on == latest_day]
        systolic = min(verdict.fact.value[0] for verdict in same_day)
        diastolic = min(verdict.fact.value[1] for verdict in same_day)
        met = systolic < 140 and diastolic < 90
        note = f" (lowest of {len(same_day)} readings)" if len(same_day) > 1 else ""
        detail = f"Most recent BP {systolic}/{diastolic} on {_fmt(latest_day)}{note}" + ("" if met else " — not controlled")
        closing = min(same_day, key=lambda verdict: verdict.fact.value[0])
        components.append(ComponentResult("bp", "Most recent BP", met, detail, closing))
        return met, components, closing if met else None
    met_any = False
    closing = None
    for component in measure.components:
        accepted = _accepted(verdicts[component.key])
        latest = accepted[-1] if accepted else None
        met = latest is not None
        if met:
            detail = f"{latest.fact.description} on {_fmt(latest.fact.on)} · {latest.fact.source_label}"
        else:
            rejected = [verdict for verdict in verdicts[component.key] if not verdict.accepted]
            detail = _not_accepted(component.label, rejected[-1]) if rejected else f"{component.label} missing"
        components.append(ComponentResult(component.key, component.label, met, detail, latest))
        if met:
            met_any = True
            closing = closing or latest
    if code == "KED":
        met_all = all(item.met for item in components)
        return met_all, components, components[-1].verdict if met_all else None
    return met_any, components, closing


def _not_accepted(label: str, verdict: Verdict) -> str:
    """Plain-language reason a piece of evidence did not count, from its first failing check."""
    failed = next((check for check in verdict.checks if not check.passed), None)
    if verdict.reason_code == "LIN":
        return f"{label} quarantined — lineage incomplete"
    if verdict.verdict == VERDICT_HELD:
        return f"{label} on hold — awaiting final result"
    if verdict.verdict == VERDICT_REVIEW:
        return f"{label} conflicts between sources — review required"
    return f"{label} not accepted: {failed.detail}" if failed else f"{label} not accepted"


def _open_reason(measure: Measure, components: list[ComponentResult]) -> str:
    if measure.code in ("GSD", "CBP"):
        return components[0].detail
    if measure.code == "KED":
        egfr, uacr = components
        if egfr.met and not uacr.met:
            return f"{uacr.detail}; eGFR ✓ {egfr.verdict.fact.on:%d %b}" if egfr.verdict else uacr.detail
        if uacr.met and not egfr.met:
            return f"{egfr.detail}; uACR ✓"
        return "eGFR and uACR both missing this period" if "missing" in egfr.detail and "missing" in uacr.detail else f"{egfr.detail}; {uacr.detail}"
    if measure.code == "COL-E":
        rejected = [item.detail for item in components if "missing" not in item.detail]
        return rejected[0] if rejected else "No colonoscopy, FIT or FIT-DNA found"
    texts = {
        "EED": "No retinal eye exam found",
        "BCS-E": "No mammogram in the 27-month window",
        "SPD": "No statin dispensing found in period",
        "COA-FSA": "No functional status assessment this period",
        "AIS-E": "No influenza vaccine in the window",
    }
    detail = components[0].detail if components else ""
    return detail if detail and "missing" not in detail else texts.get(measure.code, "No qualifying evidence")


# ---------------------------------------------------------------------------
# Person-level evaluation
# ---------------------------------------------------------------------------


def evaluate_person(person: Person, measure: Measure, facts: list[Fact], documents: list[Fact], ctx: EvalContext) -> MeasureResult:
    pid = person.person_id
    ok, population_reason = in_population(person, measure, ctx)
    if not ok:
        return MeasureResult(pid, measure.code, NOT_IN_POPULATION, population_reason, "No action")
    if not ctx.payer_view and person.identity != "confident":
        return MeasureResult(pid, measure.code, GAP_UNKNOWN, person.identity_note or "Identity under review",
                             "Identity review with the MPI steward", "evidence", "MPI steward", likelihood=0.5, urgency=0.3)
    if not ctx.payer_view and person.coverage != "continuous":
        return MeasureResult(pid, measure.code, GAP_UNKNOWN, person.coverage_note or "Coverage unresolved",
                             "Eligibility file retrieval", "evidence", "Eligibility ops", likelihood=0.5, urgency=0.3)
    exclusion, exclusion_fact = exclusion_for(person, measure, facts, ctx)
    if exclusion:
        result = MeasureResult(pid, measure.code, EXCLUDED, f"Excluded: {exclusion.replace('_', ' ')} ({exclusion_fact.source_label})",
                               "Outreach suppressed", exclusion=exclusion, exclusion_fact=exclusion_fact)
        return result
    verdicts = _verdicts(person, measure, facts, ctx)
    all_verdicts = [verdict for items in verdicts.values() for verdict in items]
    met, components, closing = _numerator(measure, verdicts)
    if met:
        return MeasureResult(pid, measure.code, GAP_CLOSED, components[0].detail if len(components) == 1 else
                             " · ".join(f"{item.label} ✓" for item in components if item.met),
                             "Monitor for reversal; include in payer supplemental if not visible", components=components,
                             verdicts=all_verdicts, closing=closing)
    result = MeasureResult(pid, measure.code, GAP_OPEN, _open_reason(measure, components), components=components, verdicts=all_verdicts)
    _urgency(result, person, measure, components)
    if ctx.payer_view:
        return result
    if measure.code in LAB_DEPENDENT and person.lab_source in ctx.stale_labs:
        result.status = GAP_UNKNOWN
        result.reason = f"{person.lab_source} feed is stale — results may be missing, not absent"
        result.next_action, result.route, result.owner, result.likelihood = "Evidence retrieval", "evidence", "Data ops", 0.5
        return result
    pending = []
    pending += [f"{v.fact.description} {v.verdict.replace('_', ' ').lower()} ({v.reason_code})"
                for v in all_verdicts if v.verdict in (VERDICT_HELD, VERDICT_REVIEW)]
    pending += [doc.description for doc in documents if doc.extra.get("reports") == measure.code]
    request = _latest(ctx.state, pid, measure.code, ("record_request",))
    if request and request.get("status", "open") == "open":
        pending.append(f"Records requested {request['at'][:10]} ({request.get('channel', 'HIE')})")
    if pending:
        result.status, result.pending = PENDING_EVIDENCE, pending
        result.reason = pending[0]
        if any(v.verdict == VERDICT_REVIEW for v in all_verdicts):
            result.next_action, result.route, result.owner = "Resolve source conflict", "review", "HEDIS analyst"
        elif any(v.verdict == VERDICT_HELD for v in all_verdicts):
            result.next_action, result.route, result.owner = "Await final result", "await", "Lab interface owner"
        else:
            result.next_action, result.route, result.owner = "Request report (HIE)", "records", "HEDIS analyst"
        result.likelihood = 0.7
        return result
    preference = _latest(ctx.state, pid, measure.code, ("preference",))
    if preference:
        result.status = PREFERENCE_RECORDED
        result.reason = f"{preference['detail']} ({preference['at'][:10]})"
        result.next_action, result.route, result.owner = f"Re-offer after {preference.get('reoffer', 'next period')}", "none", care_manager_for(person)
        result.likelihood = 0.1
        return result
    _route_open(result, person, measure, ctx)
    return result


def _latest(state: dict, person_id: str, measure: str, kinds: tuple) -> dict | None:
    matches = [
        action for action in state.get("actions", [])
        if action.get("person_id") == person_id and action.get("measure") == measure and action.get("kind") in kinds
        and not action.get("superseded")
    ]
    return matches[-1] if matches else None


def _urgency(result: MeasureResult, person: Person, measure: Measure, components: list[ComponentResult]):
    code = measure.code
    if code == "GSD":
        verdict = components[0].verdict
        value = float(verdict.fact.value) if verdict and verdict.fact.kind == "lab" else None
        result.urgency, result.urgency_note = (
            (0.95, "HbA1c ≥9%") if value is not None and value >= 9 else
            (0.8, "HbA1c 8–9%") if value is not None else (0.6, "No glycemic result this period")
        )
    elif code == "CBP":
        verdict = components[0].verdict
        if verdict and verdict.fact.kind == "bp":
            systolic, diastolic = verdict.fact.value
            result.urgency, result.urgency_note = (0.9, "BP ≥160/100") if systolic >= 160 or diastolic >= 100 else (0.78, "BP ≥140/90")
        else:
            result.urgency, result.urgency_note = 0.55, "No BP this period"
    else:
        result.urgency = URGENCY_BASE.get(code, 0.4)
        result.urgency_note = {"KED": "Kidney screening in diabetes", "EED": "Retinopathy screening", "SPD": "Statin gap in diabetes"}.get(
            code, "Preventive screening")
    if person.barriers:
        result.urgency_note += f" · barrier: {person.barriers[0]}"


def _route_open(result: MeasureResult, person: Person, measure: Measure, ctx: EvalContext):
    order = _latest(ctx.state, person.person_id, measure.code, ("order", "referral"))
    decline = _latest(ctx.state, person.person_id, measure.code, ("decline",))
    visit = person.upcoming_visit
    days = (visit["date"] - ctx.as_of).days if visit and visit.get("date") else None
    if order:
        result.next_action = f"{order['detail']} {order['at'][:10]} — awaiting result"
        result.route, result.owner, result.likelihood = "await", "Lab interface owner", 0.8
    elif decline:
        result.next_action = f"Declined by clinician: {decline['detail']} — conflict review"
        result.route, result.owner, result.likelihood = "none", person.pcp, 0.15
    elif days is not None and days <= 30 and measure.action_kind in ("order", "referral", "visit"):
        result.next_action = f"Visit in {days} day{'s' if days != 1 else ''} → point-of-care card" if days else "Visit today → point-of-care card"
        result.route, result.owner, result.likelihood = "visit", person.pcp, 0.6
    elif measure.action_kind == "outreach":
        result.next_action = f"Outreach: {measure.action_label}"
        result.route, result.owner = "outreach", care_manager_for(person)
        result.likelihood = 0.25 if person.consent_sms else 0.18
    else:
        result.next_action = f"Schedule visit: {measure.action_label}"
        result.route, result.owner, result.likelihood = "outreach", care_manager_for(person), 0.2
    assignment = ctx.state.get("assignments", {}).get(result.key)
    if assignment:
        result.owner = assignment["owner"]


# ---------------------------------------------------------------------------
# Event-based measures
# ---------------------------------------------------------------------------

TRC_COMPONENTS = {
    "NIA": "Notification of inpatient admission",
    "RDI": "Receipt of discharge information",
    "PED": "Patient engagement after discharge",
    "MRP": "Medication reconciliation post-discharge",
}


def _trc_component(name: str, stay: Stay, entry: dict, as_of: date) -> dict:
    if name == "NIA":
        deadline = stay.admit + timedelta(days=2)
    elif stay.discharge is None:
        return {"status": "pending", "label": "—", "deadline": None, "met": False}
    else:
        deadline = stay.discharge + timedelta(days=2 if name == "RDI" else 30)
    status = entry.get("status", "not_started")
    done_on = entry.get("on")
    done_day = date.fromisoformat(done_on) if done_on else None
    if status == "done" and (done_day is None or done_day <= deadline):
        return {"status": status, "label": "Done", "deadline": deadline, "met": True, "by": entry.get("by")}
    if as_of > deadline:
        return {"status": status, "label": "Missed", "deadline": deadline, "met": False}
    label = "Requested" if status == "requested" else ("Due" if (deadline - as_of).days <= 5 else "Not started")
    return {"status": status, "label": label, "deadline": deadline, "met": False}


def transition_cases(stays: list[Stay], people: dict, ctx: EvalContext) -> list[TransitionCase]:
    cases = []
    readmitted = {stay.readmission_of for stay in stays if stay.readmission_of}
    for stay in stays:
        person = people.get(stay.person_id)
        if person is None or (person.age or 0) < 18:
            continue
        if stay.discharge and not (MP.start <= stay.discharge <= date(MP.end.year, 12, 1)):
            continue
        entries = ctx.state.get("transitions", {}).get(stay.stay_id, {})
        components = {name: _trc_component(name, stay, entries.get(name, {}), ctx.as_of) for name in TRC_COMPONENTS}
        cases.append(TransitionCase(stay, stay.person_id, components, stay.stay_id in readmitted))
    return cases


def fmc_cases(visits: list[EdVisit], people: dict, ctx: EvalContext) -> list[FmcCase]:
    cases = []
    for visit in visits:
        person = people.get(visit.person_id)
        if person is None or (person.age or 0) < 18 or len(visit.conditions) < 2:
            continue
        if not (MP.start <= visit.on <= date(MP.end.year, 12, 24)):
            continue
        due = visit.on + timedelta(days=7)
        task = ctx.state.get("fmc_tasks", {}).get(visit.visit_id, {})
        if visit.followed_up:
            status, note = "Done", f"Seen {visit.followed_up:%d %b}"
        elif task.get("status") == "done":
            status, note = "Done", f"Seen {task.get('on', '')}"
        elif ctx.as_of > due:
            status, note = "Missed", task.get("note") or "No follow-up within 7 days"
        elif task.get("status") == "booked":
            status, note = "Booked", task.get("note", "Follow-up booked")
        else:
            status, note = "Due", "Follow-up needed within 7 days"
        cases.append(FmcCase(visit, visit.person_id, status, due, note))
    return cases


def _event_result(person: Person, measure: Measure, cases: list, ctx: EvalContext) -> MeasureResult:
    pid = person.person_id
    if not cases:
        reason = "No inpatient discharge this period" if measure.code == "TRC" else "No eligible ED visit this period"
        return MeasureResult(pid, measure.code, NOT_IN_POPULATION, reason, "No action")
    if measure.code == "TRC":
        open_cases = [case for case in cases if not case.met]
        if not open_cases:
            return MeasureResult(pid, "TRC", GAP_CLOSED, f"All four components done for {len(cases)} discharge(s)", cases=cases)
        case = open_cases[-1]
        missing = [TRC_COMPONENTS[name].split(" ")[0].lower() for name in case.components if not case.components[name]["met"]]
        when = f"Discharged {case.stay.discharge:%d %b}" if case.stay.discharge else f"Inpatient since {case.stay.admit:%d %b}"
        result = MeasureResult(pid, "TRC", GAP_OPEN, f"{when}; outstanding: {', '.join(missing)}", "Work the transition → S10",
                               "transition", "Transitions RN", cases=cases, urgency=0.85 if case.stay.expected_readmit >= 0.25 else 0.65,
                               urgency_note="Recent discharge", likelihood=0.6)
        if not case.open_components:
            result.next_action, result.route, result.likelihood = "Window closed — component missed", "none", 0.0
            result.urgency, result.urgency_note = 0.1, "Window closed — not recoverable this period"
        return result
    open_cases = [case for case in cases if not case.met]
    if not open_cases:
        return MeasureResult(pid, "FMC", GAP_CLOSED, f"Follow-up within 7 days for {len(cases)} ED visit(s)", cases=cases)
    case = open_cases[-1]
    if case.status == "Missed":
        return MeasureResult(pid, "FMC", GAP_OPEN, f"ED visit {case.visit.on:%d %b}: follow-up window closed {case.due:%d %b}",
                             "Missed — recorded; review barrier", "none", care_manager_for(person), cases=cases, urgency=0.1,
                             urgency_note="Window closed — not recoverable this period")
    return MeasureResult(pid, "FMC", GAP_OPEN, f"ED visit {case.visit.on:%d %b}: follow-up due by {case.due:%d %b}",
                         "Book follow-up → S10", "transition", care_manager_for(person), cases=cases, urgency=0.8,
                         urgency_note="Multiple chronic conditions after ED visit", likelihood=0.65)


# ---------------------------------------------------------------------------
# Whole-population evaluation and rates
# ---------------------------------------------------------------------------


def evaluate(people: list[Person], facts_by_person: dict, layer: FeedLayer, ctx: EvalContext, measures: tuple = ()) -> Evaluation:
    measures = measures or tuple(MEASURES)
    by_id = {person.person_id: person for person in people}
    results = {}
    stays = list(layer.stays) + [_manual_stay(item) for item in ctx.state.get("manual_adt", [])]
    trc = transition_cases(stays, by_id, ctx) if "TRC" in measures else []
    fmc = fmc_cases(layer.ed_visits, by_id, ctx) if "FMC" in measures else []
    for person in people:
        all_facts = [fact for fact in facts_by_person.get(person.person_id, []) if fact_available(fact, ctx)]
        facts = [fact for fact in all_facts if fact.kind != "document"]
        documents = [fact for fact in all_facts if fact.kind == "document"]
        for code in measures:
            measure = MEASURES[code]
            if measure.kind == "person":
                results[(person.person_id, code)] = evaluate_person(person, measure, facts, documents, ctx)
            elif code == "TRC":
                results[(person.person_id, code)] = _event_result(person, measure, [c for c in trc if c.person_id == person.person_id], ctx)
            elif code == "FMC":
                results[(person.person_id, code)] = _event_result(person, measure, [c for c in fmc if c.person_id == person.person_id], ctx)
    return Evaluation(results, trc, fmc, stays, ctx)


def _manual_stay(item: dict) -> Stay:
    return Stay(item["stay_id"], item["person_id"], item.get("facility", "Manual notification"), date.fromisoformat(item["admit"]),
                date.fromisoformat(item["discharge"]) if item.get("discharge") else None, float(item.get("expected", 0.15)),
                item.get("ref", "Manual ADT"))


def compute_rate(code: str, evaluation: Evaluation, person_ids: set | None = None) -> Rate:
    measure = MEASURES[code]
    rate = Rate(code)
    if code == "PCR":
        index = [stay for stay in evaluation.pcr_stays if stay.discharge and stay.readmission_of is None
                 and MP.start <= stay.discharge <= date(MP.end.year, 12, 1) and (person_ids is None or stay.person_id in person_ids)]
        readmitted = {stay.readmission_of for stay in evaluation.pcr_stays if stay.readmission_of}
        rate.denominator = len(index)
        rate.observed = sum(1 for stay in index if stay.stay_id in readmitted)
        rate.expected = round(sum(stay.expected_readmit for stay in index), 2)
        rate.numerator = rate.observed
        rate.value = round(rate.observed / rate.expected, 2) if rate.expected else None
        return rate
    if code == "TRC":
        cases = [case for case in evaluation.trc_cases if case.stay.discharge and (person_ids is None or case.person_id in person_ids)]
        rate.denominator = len(cases)
        for name, label in TRC_COMPONENTS.items():
            rate.components[name] = (sum(case.components[name]["met"] for case in cases), len(cases), label)
        rate.numerator = sum(case.met for case in cases)
        rate.value = round(sum(met / total * 100 for met, total, _ in rate.components.values()) / 4, 1) if cases else None
        rate.initial = len(cases)
        return rate
    if code == "FMC":
        cases = [case for case in evaluation.fmc_cases if person_ids is None or case.person_id in person_ids]
        rate.initial = rate.denominator = len(cases)
        rate.numerator = sum(case.met for case in cases)
        rate.value = round(rate.numerator / rate.denominator * 100, 1) if cases else None
        return rate
    results = [result for result in evaluation.for_measure(code) if person_ids is None or result.person_id in person_ids]
    for result in results:
        rate.statuses[result.status] += 1
        if result.status != NOT_IN_POPULATION:
            rate.initial += 1
        if result.status == EXCLUDED:
            rate.exclusions[result.exclusion] += 1
        if result.in_denominator:
            rate.denominator += 1
            rate.numerator += result.met
            for verdict in result.verdicts:
                if not verdict.accepted:
                    rate.rejections[(verdict.component, verdict.reason_code)] += 1
            for component in result.components:
                met, total, label = rate.components.get(component.key, (0, 0, component.label))
                rate.components[component.key] = (met + component.met, total + 1, label)
    if code == "KED" and rate.denominator:
        both = sum(1 for result in results if result.in_denominator and all(item.met for item in result.components))
        rate.components["both"] = (both, rate.denominator, "Both present")
    rate.value = round(rate.numerator / rate.denominator * 100, 1) if rate.denominator else None
    if measure.direction == "lower" and rate.value is not None:
        rate.value = round(rate.value, 2)
    return rate


def workable_results(evaluation: Evaluation, statuses: tuple = WORKABLE_STATUSES) -> list[MeasureResult]:
    return [result for result in evaluation.results.values() if result.status in statuses]


PERSON_MEASURE_CODES = PERSON_MEASURES
