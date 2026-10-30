"""Value-based care (HEDIS / Stars) engine, evidence checks, guardrails and permissions."""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from frontend.admin_service import (
    DEFAULT_PERMISSIONS,
    READ_ONLY_ROLES,
    VBC_ROLES,
    VBC_SCREENS,
)
from frontend.vbc import store as vbc_store
from frontend.vbc.catalog import (
    DENOMINATOR_STATUSES,
    GAP_CLOSED,
    GAP_OPEN,
    GAP_STATUSES,
    GAP_UNKNOWN,
    MEASURES,
    MP,
    PREFERENCE_RECORDED,
    VERDICT_ACCEPTED,
    VERDICT_HELD,
    VERDICT_REJECTED,
    VERDICT_REVIEW,
)
from frontend.vbc.engine import MeasureResult
from frontend.vbc.evidence import Fact, run_checks
from frontend.vbc.service import build_snapshot, fact_to_dict, seed_state
from frontend.vbc.stars import overall_stars, rank_gaps, star_level

EHR_FILE = Path(__file__).resolve().parents[2] / "Epic_Inspired_USA_50_Patient_Demo_Athena_Care_Gap.json"
NOW = datetime(2026, 10, 26, 10, 0)


@pytest.fixture(scope="module")
def export():
    if not EHR_FILE.is_file():
        pytest.skip("Demo EHR export not available")
    return json.loads(EHR_FILE.read_text(encoding="utf-8"))


def snapshot_for(export, state=None):
    return build_snapshot(export, state if state is not None else vbc_store.empty_state(), NOW, "test")


def a1c(**changes):
    values = {
        "fact_id": "F-1", "person_id": "P1", "kind": "lab", "system": "LOINC", "code": "4548-4", "description": "HbA1c",
        "on": date(2026, 5, 1), "received": datetime(2026, 5, 2), "source": "lab_interface", "source_ref": "ORU-1", "value": 7.1, "unit": "%",
    }
    values.update(changes)
    return Fact(**values)


def check(fact, decisions=None, sources=("ehr", "lab_interface", "claims", "hie", "cpt2", "abstraction")):
    measure = MEASURES["GSD"]
    return run_checks(fact, "GSD", measure.components[0], MP, sources, decisions)


# ---------------------------------------------------------------------------
# The eight evidence checks
# ---------------------------------------------------------------------------


def test_final_coded_in_window_result_is_accepted():
    verdict = check(a1c())
    assert verdict.verdict == VERDICT_ACCEPTED
    assert len(verdict.checks) == 8 and all(item.passed for item in verdict.checks)


@pytest.mark.parametrize(("changes", "verdict", "code"), [
    ({"status": "preliminary"}, VERDICT_HELD, "STS"),
    ({"status": "cancelled"}, VERDICT_REJECTED, "STS"),
    ({"system": "LOCAL", "code": "LAB-A1C-01"}, VERDICT_REJECTED, "VSM"),
    ({"on": date(2025, 11, 30)}, VERDICT_REJECTED, "WIN"),
    ({"identity_confidence": 0.81}, VERDICT_REJECTED, "IDN"),
    ({"source": "scanned"}, VERDICT_REJECTED, "SRC"),
    ({"structured": False}, VERDICT_REJECTED, "STR"),
    ({"value": None}, VERDICT_REJECTED, "PRS"),
    ({"conflict_ref": "F-2"}, VERDICT_REVIEW, "CFL"),
])
def test_first_failing_check_sets_the_verdict(changes, verdict, code):
    result = check(a1c(**changes))
    assert (result.verdict, result.reason_code) == (verdict, code)


def test_conflict_is_settled_only_by_a_person():
    fact = a1c(conflict_ref="F-2")
    assert check(fact, {"F-1": {"decision": "accepted", "by": "Analyst"}}).verdict == VERDICT_ACCEPTED
    rejected = check(fact, {"F-1": {"decision": "rejected", "by": "Analyst"}})
    assert (rejected.verdict, rejected.reason_code) == (VERDICT_REJECTED, "CFL")


# ---------------------------------------------------------------------------
# Engine: statuses and guardrails on the demo population
# ---------------------------------------------------------------------------


def test_every_person_measure_has_one_of_the_seven_statuses(export):
    snapshot = snapshot_for(export)
    assert snapshot.evaluation.results
    assert {result.status for result in snapshot.evaluation.results.values()} <= set(GAP_STATUSES)


def test_unknown_is_not_open_for_identity_and_coverage_problems(export):
    snapshot = snapshot_for(export)
    unresolved = [person for person in snapshot.people if not person.workable]
    assert unresolved, "demo data should include an identity and a coverage edge case"
    for person in unresolved:
        assert all(result.status != GAP_OPEN for result in snapshot.evaluation.for_person(person.person_id))


def test_stale_lab_feed_makes_lab_gaps_unknown_not_open(export):
    snapshot = snapshot_for(export)
    lakeside = [person for person in snapshot.people if person.lab_source == "Lakeside Outreach Lab" and person.workable]
    statuses = [snapshot.evaluation.get(person.person_id, code).status for person in lakeside for code in ("GSD", "KED")]
    assert GAP_OPEN not in statuses
    assert GAP_UNKNOWN in statuses


def test_preference_stays_in_the_denominator(export):
    state = vbc_store.empty_state()
    snapshot = snapshot_for(export, state)
    target = next(result for result in snapshot.evaluation.for_measure("COL-E") if result.status == GAP_OPEN)
    vbc_store.add_action(state, "Care manager", target.person_id, "COL-E", "preference", "Declines colonoscopy; FIT offered")
    result = snapshot_for(export, state).evaluation.get(target.person_id, "COL-E")
    assert result.status == PREFERENCE_RECORDED
    assert result.in_denominator and not result.met
    assert PREFERENCE_RECORDED in DENOMINATOR_STATUSES


def test_exclusion_candidates_never_count_until_a_person_confirms(export):
    state = vbc_store.empty_state()
    snapshot = snapshot_for(export, state)
    candidate = next(item for item in snapshot.candidates() if item.exclusion == "bilateral_mastectomy")
    before = snapshot.evaluation.get(candidate.person_id, "BCS-E")
    assert before.status != "EXCLUDED"
    fact = Fact(f"ATT-{candidate.candidate_id}", candidate.person_id, "diagnosis", "ICD-10-CM", candidate.code, "Confirmed", candidate.received,
                NOW, "attestation", "Exclusion review", extra={"exclusion": candidate.exclusion})
    state["posted_facts"].append(fact_to_dict(fact))
    after = snapshot_for(export, state).evaluation.get(candidate.person_id, "BCS-E")
    assert after.status == "EXCLUDED"
    assert not after.in_denominator


def test_quarantined_closure_is_pulled_from_reported_results(export):
    state = vbc_store.empty_state()
    snapshot = snapshot_for(export, state)
    closed = next(result for result in snapshot.evaluation.for_measure("CBP") if result.status == GAP_CLOSED and result.closing.fact.source == "ehr")
    state["quarantine"][closed.closing.fact.fact_id] = {"reason": "missing message ID", "at": NOW.isoformat(), "restored": False}
    after = snapshot_for(export, state).evaluation.get(closed.person_id, "CBP")
    assert after.status != GAP_CLOSED or after.closing.fact.fact_id != closed.closing.fact.fact_id
    assert any(verdict.reason_code == "LIN" for verdict in after.verdicts)


def test_payer_view_only_counts_evidence_the_payer_can_see(export):
    snapshot = snapshot_for(export)
    payer = snapshot.payer_evaluation(("CBP", "BCS-E", "COL-E"))
    for result in payer.results.values():
        if result.status == GAP_CLOSED:
            assert result.closing.fact.payer_visible or result.closing.fact.fact_id in snapshot.payer_visible


def test_seed_is_idempotent_and_the_audit_chain_verifies(export):
    state = vbc_store.empty_state()
    snapshot = snapshot_for(export, state)
    seed_state(state, snapshot)
    events = len(state["audit"])
    seed_state(state, snapshot)
    assert len(state["audit"]) == events
    assert vbc_store.verify_chain(state) == (True, None)


# ---------------------------------------------------------------------------
# Ranking and Stars
# ---------------------------------------------------------------------------


def test_clinical_urgency_outranks_star_weight():
    urgent_low_weight = MeasureResult("P1", "KED", GAP_OPEN, "uACR missing", urgency=0.9, likelihood=0.3)
    heavy_but_routine = MeasureResult("P2", "GSD", GAP_OPEN, "No HbA1c", urgency=0.5, likelihood=0.9)
    ranked = rank_gaps([heavy_but_routine, urgent_low_weight], date(2026, 10, 26))
    assert [item.person_id for item in ranked] == ["P1", "P2"]


def test_star_levels_respect_measure_direction():
    assert star_level("GSD", 59.0) == 1 and star_level("GSD", 82.0) == 5
    assert star_level("PCR", 1.20) == 1 and star_level("PCR", 0.85) == 5
    assert overall_stars({"GSD": 4, "KED": 2}) == 3.5


# ---------------------------------------------------------------------------
# Audit log and permissions
# ---------------------------------------------------------------------------


def test_tampering_with_the_audit_log_is_detected():
    state = vbc_store.empty_state()
    for index in range(3):
        vbc_store.audit(state, "WRK", "tester", f"Action {index}")
    assert vbc_store.verify_chain(state) == (True, None)
    state["audit"][1]["detail"] = "edited after the fact"
    assert vbc_store.verify_chain(state) == (False, 2)


def test_read_only_roles_never_edit_and_approvals_follow_the_matrix():
    for role in READ_ONLY_ROLES:
        assert not any(DEFAULT_PERMISSIONS[role][module]["Edit"] for module in VBC_SCREENS.values())
    assert DEFAULT_PERMISSIONS["HEDIS Lead"][VBC_SCREENS["S08"]]["Approve"]
    assert not DEFAULT_PERMISSIONS["Care Manager"][VBC_SCREENS["S08"]]["Approve"]
    assert DEFAULT_PERMISSIONS["Physician / APP"][VBC_SCREENS["S04"]]["Approve"]
    assert all(any(DEFAULT_PERMISSIONS[role][module]["View"] for module in VBC_SCREENS.values()) for role in VBC_ROLES)


def test_outreach_gateway_blocks_survey_references_and_diagnoses(export):
    from frontend.views.vbc.gaps import _checks

    person = snapshot_for(export).people[0]
    daytime = datetime(2026, 10, 26, 11, 0)

    def failed(text, channel="Phone"):
        return {label for label, ok, _ in _checks(person, text, channel, daytime, True) if not ok}

    assert "No survey reference" in failed("Please rate us on the upcoming survey")
    assert "Minimum necessary PHI" in failed("Your diabetes needs a check")
    assert "Quiet hours respected" in {label for label, ok, _ in _checks(person, "Hello", "Phone", datetime(2026, 10, 26, 21, 30), True) if not ok}
    assert not failed("Your yearly eye exam is due. Reply 1 to book.") - {"Consent on file", "Channel allowed"}
