"""Assemble a value-based care snapshot: people, facts, evaluation, rates and payer views.

The snapshot is rebuilt on every page run from the EHR export plus the persisted workflow
state; it is cheap (tens of milliseconds for the demo population) and always consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cached_property

from frontend.vbc import store as vbc_store
from frontend.vbc.catalog import (
    BASE_VERSION,
    CHANGE_REQUESTS,
    CHANGE_REQUESTS_BY_ID,
    DASHBOARD_MEASURES,
    GAP_CLOSED,
    MEASURES,
    PAYERS,
    SOURCES,
)
from frontend.vbc.engine import (
    EvalContext,
    Evaluation,
    Rate,
    Spec,
    compute_rate,
    evaluate,
)
from frontend.vbc.evidence import Fact
from frontend.vbc.feeds import (
    FeedLayer,
    ehr_facts,
    ehr_rows_facts,
    feed_registry,
    synthetic_layer,
)
from frontend.vbc.population import Person, build_population, draw
from frontend.vbc.stars import overall_stars, rank_gaps, scorecard


@dataclass
class Snapshot:
    now: datetime
    as_of: date
    export_source: str
    people: list[Person]
    facts: dict
    layer: FeedLayer
    state: dict
    evaluation: Evaluation
    spec: Spec
    feeds: list
    payer_visible: set = field(default_factory=set)

    @cached_property
    def people_by_id(self) -> dict[str, Person]:
        return {person.person_id: person for person in self.people}

    @property
    def version(self) -> str:
        return self.spec.version

    def person(self, person_id: str) -> Person | None:
        return self.people_by_id.get(person_id)

    def person_ids(self, product_line: str = "All", site: str = "All sites") -> set:
        return {
            person.person_id for person in self.people
            if (product_line == "All" or person.product_line == product_line) and (site == "All sites" or person.site == site)
        }

    def rate(self, code: str, product_line: str = "All", site: str = "All sites") -> Rate:
        ids = None if product_line == "All" and site == "All sites" else self.person_ids(product_line, site)
        return compute_rate(code, self.evaluation, ids)

    def rates(self, product_line: str = "All", site: str = "All sites") -> dict[str, Rate]:
        return {code: self.rate(code, product_line, site) for code in MEASURES}

    def scorecard(self, product_line: str = "All", site: str = "All sites") -> list[dict]:
        ids = None if product_line == "All" and site == "All sites" else self.person_ids(product_line, site)
        return scorecard(self.rates(product_line, site), self.evaluation, ids)

    def overall(self, rows: list[dict], key: str = "level") -> float | None:
        return overall_stars({row["code"]: row[key] for row in rows})

    def ranked(self, results=None):
        return rank_gaps(list(results if results is not None else self.evaluation.results.values()), self.as_of)

    def stale_feeds(self) -> list[dict]:
        return [feed for feed in self.feeds if (self.now - feed["last"]).total_seconds() / 3600 > feed["expected_hours"]]

    def candidates(self) -> list:
        return self.layer.candidates

    def payer_evaluation(self, measures: tuple = ("CBP",)) -> Evaluation:
        ctx = EvalContext(self.as_of, self.spec, self.state, payer_view=True, payer_visible=self.payer_visible)
        return evaluate(self.people, self.facts, self.layer, ctx, measures=measures)

    def evaluate_with(self, spec: Spec, measures: tuple) -> Evaluation:
        ctx = EvalContext(self.as_of, spec, self.state, self.evaluation.context.stale_labs)
        return evaluate(self.people, self.facts, self.layer, ctx, measures=measures)

    def evaluate_as_of(self, day: date, measures: tuple) -> Evaluation:
        ctx = EvalContext(day, self.spec, self.state, self.evaluation.context.stale_labs)
        return evaluate(self.people, self.facts, self.layer, ctx, measures=measures)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def fact_from_dict(item: dict) -> Fact:
    return Fact(
        item["fact_id"], item["person_id"], item["kind"], item["system"], item["code"], item["description"],
        date.fromisoformat(item["on"]) if item.get("on") else None,
        datetime.fromisoformat(item["received"]) if item.get("received") else None,
        item["source"], item.get("source_ref", ""), status=item.get("status", "final"), value=_value(item.get("value")),
        unit=item.get("unit", ""), structured=item.get("structured", True), identity_confidence=item.get("identity_confidence", 1.0),
        origin="workspace", note=item.get("note", ""), provider=item.get("provider", ""),
        payer_visible=bool(item.get("payer_visible") or SOURCES.get(item["source"], SOURCES["ehr"]).payer_visible),
        extra=item.get("extra", {}),
    )


def fact_to_dict(fact: Fact) -> dict:
    return {
        "fact_id": fact.fact_id, "person_id": fact.person_id, "kind": fact.kind, "system": fact.system, "code": fact.code,
        "description": fact.description, "on": fact.on.isoformat() if fact.on else None,
        "received": fact.received.isoformat(timespec="seconds") if fact.received else None, "source": fact.source,
        "source_ref": fact.source_ref, "status": fact.status, "value": list(fact.value) if isinstance(fact.value, tuple) else fact.value,
        "unit": fact.unit, "structured": fact.structured, "identity_confidence": fact.identity_confidence, "note": fact.note,
        "provider": fact.provider, "payer_visible": fact.payer_visible, "extra": fact.extra,
    }


def _value(value):
    return tuple(value) if isinstance(value, list) else value


def active_spec(state: dict) -> Spec:
    overrides: dict = {}
    published = [cr_id for cr_id, item in state.get("change_requests", {}).items() if item.get("status") == "Published"]
    for cr_id in published:
        for measure, params in CHANGE_REQUESTS_BY_ID[cr_id].spec_override.items():
            overrides.setdefault(measure, {}).update(params)
    version = state.get("versions", [{}])[-1].get("version", BASE_VERSION)
    return Spec(version, overrides)


def payer_visible_ids(state: dict) -> set:
    visible = set()
    for batch in state.get("batches", []):
        for record in batch.get("records", []):
            if record.get("state") == "accepted":
                visible.add(record["fact_id"])
    return visible


def collect_facts(people: list[Person], export: dict, layer: FeedLayer, state: dict) -> dict[str, list[Fact]]:
    ids = {person.person_id for person in people}
    facts = {person.person_id: ehr_facts(person) for person in people}
    for person_id, items in ehr_rows_facts(export, ids).items():
        facts.setdefault(person_id, []).extend(items)
    for person_id, items in layer.facts.items():
        facts.setdefault(person_id, []).extend(items)
    for item in state.get("posted_facts", []):
        if item.get("person_id") in ids:
            facts.setdefault(item["person_id"], []).append(fact_from_dict(item))
    return facts


def build_snapshot(export: dict, state: dict, now: datetime, export_source: str) -> Snapshot:
    as_of = now.date()
    people = build_population(export, as_of)
    for person in people:
        overrides = state.get("person_prefs", {}).get(person.person_id)
        if overrides:
            person.preferences.update({key: value for key, value in overrides.items() if key in ("channel", "time")})
            if "barriers" in overrides:
                person.barriers = list(overrides["barriers"])
        withdrawn = state.get("consent_withdrawn", {}).get(person.person_id, {})
        if withdrawn.get("SMS"):
            person.consent_sms = False
    layer = synthetic_layer(people, as_of)
    facts = collect_facts(people, export, layer, state)
    feeds = feed_registry(now)
    stale = {feed["feed"].split(" · ")[0] for feed in feeds
             if feed["kind"] == "Lab" and (now - feed["last"]).total_seconds() / 3600 > feed["expected_hours"]}
    spec = active_spec(state)
    ctx = EvalContext(as_of, spec, state, stale)
    evaluation = evaluate(people, facts, layer, ctx)
    return Snapshot(now, as_of, export_source, people, facts, layer, state, evaluation, spec, feeds, payer_visible_ids(state))


# ---------------------------------------------------------------------------
# One-time seed of operational history (so a fresh demo shows real work in flight)
# ---------------------------------------------------------------------------

SEED_ACTOR = "seed"


def seed_state(state: dict, snapshot: Snapshot) -> None:
    if state.get("seeded"):
        return
    as_of = snapshot.as_of
    _seed_preferences(state, snapshot)
    _seed_record_requests(state, snapshot)
    _seed_transitions(state, snapshot)
    _seed_change_requests(state, snapshot)
    _seed_batches(state, snapshot)
    _seed_outreach(state, snapshot)
    _seed_quarantine(state, snapshot)
    base = datetime.combine(as_of, datetime.min.time())
    for days_ago, family, actor, action, detail, outcome in (
        (9, "VER", "A. Patel (HEDIS lead)", "Change request published", "CR-26-03 SPD moved to ECDS — two approvers", "Recorded"),
        (7, "RBAC", "Governance agent", "Care manager tried to accept non-standard evidence", "Routed to HEDIS analyst", "Blocked"),
        (6, "SRV", "Governance agent", "Outreach template referenced an upcoming survey", "Template T-BCS-02 returned for correction", "Blocked"),
    ):
        vbc_store.audit(state, family, actor, action, detail, outcome, at=base - timedelta(days=days_ago, hours=-10))
    state["seeded"] = True
    state["seeded_at"] = as_of.isoformat()


def _seed_preferences(state: dict, snapshot: Snapshot):
    for person in snapshot.people:
        if 45 <= (person.age or 0) <= 75 and 0.46 <= draw(person.person_id, "col") < 0.52:
            state["actions"].append({
                "id": vbc_store.next_id(state, "ACT"), "at": "2026-10-18T11:20:00", "by": "J. Ortiz (care manager)",
                "person_id": person.person_id, "measure": "COL-E", "kind": "preference",
                "detail": "Declined colonoscopy; FIT offered", "reoffer": "Jan 2027", "channel": "Phone",
            })


def _seed_record_requests(state: dict, snapshot: Snapshot):
    for person_id, facts in snapshot.layer.facts.items():
        for fact in facts:
            if fact.kind == "document" and fact.extra.get("reports"):
                state["actions"].append({
                    "id": vbc_store.next_id(state, "ACT"), "at": f"{fact.received:%Y-%m-%dT%H:%M:%S}", "by": "J. Ortiz (care manager)",
                    "person_id": person_id, "measure": fact.extra["reports"], "kind": "record_request",
                    "detail": "Outside report requested", "channel": "HIE", "status": "open",
                })


def _seed_transitions(state: dict, snapshot: Snapshot):
    """Completed transition work so far; nothing is recorded with a date after today."""
    as_of = snapshot.as_of

    def done(day: date, by: str) -> dict | None:
        return {"status": "done", "on": day.isoformat(), "by": by} if day <= as_of else None

    for stay in snapshot.layer.stays:
        entry = {"NIA": done(stay.admit + timedelta(days=1), "Transitions agent")}
        if stay.discharge:
            roll = draw(stay.stay_id, "rdi")
            entry["RDI"] = done(stay.discharge + timedelta(days=1), "RN K. Shah") if roll < 0.7 else {"status": "requested", "on": None}
            if draw(stay.stay_id, "ped") < (0.75 if as_of > stay.discharge + timedelta(days=30) else 0.4):
                entry["PED"] = done(stay.discharge + timedelta(days=4), "RN K. Shah")
            if draw(stay.stay_id, "mrp") < 0.55:
                entry["MRP"] = done(stay.discharge + timedelta(days=6), "PharmD R. Gomez")
        state["transitions"][stay.stay_id] = {name: item for name, item in entry.items() if item}


def _seed_change_requests(state: dict, snapshot: Snapshot):
    lead, governance = "A. Patel (HEDIS lead)", "M. Ruiz (Quality governance)"
    for cr in CHANGE_REQUESTS:
        approvals = []
        status = "Draft"
        if cr.cr_id == "CR-26-03":
            approvals = [{"by": lead, "at": "2026-10-15T10:02:00"}, {"by": governance, "at": "2026-10-17T11:20:00"}]
            status = "Published"
        elif cr.cr_id in ("CR-26-02", "CR-26-05"):
            approvals = [{"by": lead, "at": "2026-10-20T09:40:00"}]
            status = "Awaiting 2nd approver"
        elif not cr.tests_ok:
            status = "Blocked"
        state["change_requests"][cr.cr_id] = {"status": status, "approvals": approvals}
    base_rates = {code: compute_rate(code, snapshot.evaluation).value for code in DASHBOARD_MEASURES}
    state["snapshots"][BASE_VERSION] = {"frozen_at": "2026-10-17", "rates": base_rates, "note": "Frozen when CR-26-03 published"}
    state["versions"].append({"version": "v2026.2", "published": "2026-10-17", "by": f"{lead} + {governance}", "crs": ["CR-26-03"]})


def _closing_candidates(snapshot: Snapshot) -> list:
    rows = []
    for result in snapshot.evaluation.results.values():
        if result.status != GAP_CLOSED or not result.closing:
            continue
        fact = result.closing.fact
        person = snapshot.person(result.person_id)
        if fact.payer_visible or not person or not person.payer_key or result.measure not in DASHBOARD_MEASURES:
            continue
        rows.append((draw(fact.fact_id, "batch"), result, fact, person))
    rows.sort(key=lambda item: item[0])
    return rows


def _seed_batches(state: dict, snapshot: Snapshot):
    candidates = _closing_candidates(snapshot)
    payer_a = [row for row in candidates if row[3].payer_key == "A"]
    payer_b = [row for row in candidates if row[3].payer_key == "B"]

    def records(rows, accept=True, rejected_reasons=()):
        out = []
        for index, (_, result, fact, _) in enumerate(rows):
            reason = rejected_reasons[index] if index < len(rejected_reasons) else None
            out.append({"fact_id": fact.fact_id, "person_id": result.person_id, "measure": result.measure,
                        "state": "rejected" if reason else ("accepted" if accept else "pending"), "reason": reason})
        return out

    first_a = payer_a[: max(1, len(payer_a) * 2 // 5)]
    later_a = payer_a[len(first_a): len(first_a) + max(1, len(payer_a) // 5)]
    first_b = payer_b[: max(2, len(payer_b) * 2 // 5)]
    state["batches"] = [
        {"batch_id": "SB-1042", "payer": "A", "format": PAYERS["A"].format, "state": "ACCEPTED", "created": "2026-10-13",
         "submitted": "2026-10-14", "signed_by": "A. Patel (HEDIS lead)", "records": records(first_a)},
        {"batch_id": "SB-1043", "payer": "B", "format": PAYERS["B"].format, "state": "PARTIAL", "created": "2026-10-16",
         "submitted": "2026-10-17", "signed_by": "A. Patel (HEDIS lead)",
         "records": records(first_b, rejected_reasons=("Missing rendering provider NPI", "Member ID mismatch"))},
        {"batch_id": "SB-1044", "payer": "A", "format": PAYERS["A"].format, "state": "AWAITING_ACK", "created": "2026-10-18",
         "submitted": "2026-10-19", "signed_by": "A. Patel (HEDIS lead)", "records": records(later_a, accept=False)},
    ]
    state.setdefault("counters", {})["SB"] = 1044
    for batch in state["batches"]:
        vbc_store.audit(state, "SUP", batch["signed_by"], f"Batch {batch['batch_id']} submitted to {PAYERS[batch['payer']].label}",
                        f"{len(batch['records'])} record(s) · {batch['format']}", batch["state"].replace("_", " ").title(),
                        {"batch": batch["batch_id"]}, at=datetime.fromisoformat(batch["submitted"] + "T15:00:00"))


def _seed_outreach(state: dict, snapshot: Snapshot):
    open_screening = [
        result for result in snapshot.evaluation.results.values()
        if result.measure in ("COL-E", "BCS-E") and result.status == "GAP_OPEN"
    ]
    open_screening.sort(key=lambda result: draw(result.person_id, "outreach-seed"))
    stories = (
        ("SMS", "Replied 1: send kit", "FIT kit mailed"),
        ("Phone", "Left voicemail", "Retry in 3 days"),
    )
    for (channel, outcome, next_step), result in zip(stories, open_screening, strict=False):
        state["outreach"].append({
            "id": vbc_store.next_id(state, "OUT"), "at": "2026-10-14T10:05:00", "by": "J. Ortiz (care manager)",
            "person_id": result.person_id, "measure": result.measure, "campaign": result.measure, "channel": channel,
            "attempt": 1, "outcome": outcome, "next": next_step, "template": f"T-{result.measure}-01 v3",
        })


def _seed_quarantine(state: dict, snapshot: Snapshot):
    closed = [
        result for result in snapshot.evaluation.results.values()
        if result.status == GAP_CLOSED and result.closing and result.measure in ("CBP", "AIS-E", "GSD")
        and result.closing.fact.source == "ehr"
    ]
    if not closed:
        return
    result = min(closed, key=lambda item: draw(item.person_id, "quarantine"))
    state["quarantine"][result.closing.fact.fact_id] = {
        "reason": "Accepted closure missing its source message ID", "at": "2026-10-18T17:15:00", "restored": False,
        "person_id": result.person_id, "measure": result.measure,
    }
    vbc_store.audit(state, "AUD", "Audit check", "Accepted closure missing source message ID",
                    f"{result.measure} for {result.person_id} pulled from reported results", "Quarantined",
                    {"fact": result.closing.fact.fact_id}, at=datetime(2026, 10, 18, 17, 15))
