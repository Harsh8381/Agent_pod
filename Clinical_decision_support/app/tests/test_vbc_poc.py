"""Value-Based Care POC: golden patients, evidence rules, gap workflow, metrics, audit, API and permissions."""

from datetime import date

import pytest

from app.vbc_poc import catalog, engine
from app.vbc_poc import repository as repo
from app.vbc_poc import service as vbc
from app.vbc_poc.dataset import EVENTS, PATIENTS_BY_ID
from app.vbc_poc.golden import EXPECTED, GOLDEN_AS_OF, MEASURE_ORDER, run_golden

AS_OF = GOLDEN_AS_OF


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv(repo.STATE_ENV, str(tmp_path / "vbc_poc_state.json"))
    monkeypatch.setattr(vbc, "today", lambda: AS_OF)
    monkeypatch.setattr("app.vbc_poc.metrics.today", lambda: AS_OF)
    yield


def result(patient_id, measure_id):
    patient = PATIENTS_BY_ID[patient_id]
    return engine.evaluate_measure(patient, [e for e in EVENTS if e.patient_id == patient_id], catalog.MEASURES[measure_id], AS_OF)


def gap_for(patient_id, measure_id, line="SERVICE"):
    return next(row for row in vbc.list_gaps(include_closed=True) if row["patient_id"] == patient_id and row["measure_id"] == measure_id
                and row["line"] == line)


# ---------------------------------------------------------------------------
# Golden patients and rule behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", run_golden(), ids=lambda row: f"{row['patient_id']}-{row['measure_id']}")
def test_golden_patient(row):
    assert row["actual"] == row["expected"], row["scenario"]


def test_golden_catalogue_covers_every_patient_and_measure():
    assert len(EXPECTED) == 20 and all(len(states) == len(MEASURE_ORDER) for states, _ in EXPECTED.values())


def test_evaluation_follows_the_fixed_stage_order_with_a_full_trace():
    stages = [step["stage"] for step in result("VBC-006", "BPD-P")["trace"]]
    assert stages == list(range(1, 12))
    short = [step["stage"] for step in result("VBC-020", "COL-P")["trace"]]
    assert short == [1, 2, 11]  # not eligible stops scoring


def test_duplicate_lab_and_claim_are_counted_once():
    evidence = result("VBC-001", "GSD-P")["evidence"]
    duplicates = [item for item in evidence if item["verdict"] == "duplicate"]
    assert len(duplicates) == 2 and all(item["source"] == "claim" for item in duplicates)


def test_completed_test_and_controlled_outcome_are_separate_questions():
    outcome = result("VBC-007", "GSD-P")
    assert outcome["numerator"]["met"] and outcome["state"] == "OUTCOME_NOT_CONTROLLED"


def test_same_day_bp_uses_lowest_systolic_and_diastolic():
    assert result("VBC-010", "BPD-P")["outcome"]["value"] == "138/84"


@pytest.mark.parametrize(("patient", "measure", "code"), [
    ("VBC-003", "GSD-P", "FUTURE_DATE"), ("VBC-002", "BPD-P", "IMPLAUSIBLE"), ("VBC-008", "GSD-P", "MISSING_DATE"),
    ("VBC-008", "BPD-P", "INCOMPLETE_READING"), ("VBC-002", "BCS-P", "IDENTITY_MISMATCH"), ("VBC-015", "BPD-P", "CONTEXT"),
    ("VBC-007", "BCS-P", "NOT_IN_VALUE_SET"), ("VBC-011", "COL-P", "NOT_COMPLETED"), ("VBC-014", "COL-P", "OUTSIDE_WINDOW"),
    ("VBC-013", "COL-P", "NARRATIVE"), ("VBC-009", "GSD-P", "PENDING_VERIFICATION"),
])
def test_evidence_reason_codes(patient, measure, code):
    assert code in {item["reason_code"] for item in result(patient, measure)["evidence"]}


# ---------------------------------------------------------------------------
# Gap lifecycle
# ---------------------------------------------------------------------------


def test_gaps_are_created_once_and_never_duplicated():
    first = {row["key"] for row in vbc.list_gaps()}
    vbc.evaluate_population("tester")
    vbc.evaluate_population("tester")
    assert {row["key"] for row in vbc.list_gaps()} == first
    assert len(first) == len(vbc.list_gaps())
    assert not any(row["patient_id"] in ("VBC-017", "VBC-020", "VBC-018") for row in vbc.list_gaps())  # excluded / ineligible / compliant


def test_transitions_follow_the_workflow_and_need_an_owner():
    gap = gap_for("VBC-006", "GSD-P")["gap_id"]
    with pytest.raises(vbc.VbcError):
        vbc.update_gap(gap, "t", status="ASSIGNED")
    with pytest.raises(vbc.VbcError):
        vbc.update_gap(gap, "t", status="CLOSED")
    vbc.update_gap(gap, "t", status="REVIEWED")
    with pytest.raises(vbc.VbcError, match="owner"):
        vbc.update_gap(gap, "t", status="ASSIGNED")
    assert vbc.update_gap(gap, "t", owner="P. Nair", status="ASSIGNED")["status"] == "ASSIGNED"


def _assigned(patient_id, measure_id, line="SERVICE"):
    gap = gap_for(patient_id, measure_id, line)["gap_id"]
    vbc.update_gap(gap, "coordinator", status="REVIEWED")
    vbc.update_gap(gap, "manager", owner="P. Nair", status="ASSIGNED")
    return gap


def test_scheduling_and_outreach_never_close_a_gap():
    gap = _assigned("VBC-006", "GSD-P")
    vbc.update_gap(gap, "scheduler", status="SCHEDULED", schedule_date="2026-11-02")
    detail = vbc.gap_detail(gap)
    assert detail["gap"]["status"] == "SCHEDULED" and detail["result"]["state"] == "OPEN_GAP"


def test_end_to_end_closure_after_verified_evidence():
    gap = _assigned("VBC-006", "GSD-P")
    attached = vbc.attach_evidence(gap, "MA", event_date="2026-10-25", value=7.4)
    assert vbc.gap_detail(gap)["result"]["state"] == "COMPLETED_PENDING_VERIFICATION"
    assert vbc.gap_detail(gap)["gap"]["status"] == "COMPLETED"
    vbc.verify_evidence(attached["event_id"], "analyst", "accepted")
    detail = vbc.gap_detail(gap)
    assert detail["gap"]["status"] == "CLOSED" and detail["result"]["state"] == "MET"
    path = [entry["to"] for entry in detail["gap"]["history"] if entry["from"] != entry["to"]]
    assert path == ["DETECTED", "REVIEWED", "ASSIGNED", "COMPLETED", "VERIFIED", "CLOSED"]
    assert detail["gap"]["closure"]["evidence_ids"] == [attached["event_id"]]


def test_uncontrolled_result_closes_the_test_gap_and_opens_an_outcome_gap():
    gap = _assigned("VBC-006", "GSD-P")
    attached = vbc.attach_evidence(gap, "MA", event_date="2026-10-25", value=9.3)
    vbc.verify_evidence(attached["event_id"], "analyst", "accepted")
    assert vbc.gap_detail(gap)["gap"]["status"] == "CLOSED"
    outcome = gap_for("VBC-006", "GSD-P", "OUTCOME")
    assert outcome["status"] == "DETECTED" and outcome["gap_type"] == "OUTCOME"


def test_rejected_evidence_returns_the_gap_and_invalidation_reopens_it():
    gap = _assigned("VBC-006", "GSD-P")
    bad = vbc.attach_evidence(gap, "MA", event_date="2026-10-25", value=7.0)
    vbc.verify_evidence(bad["event_id"], "analyst", "rejected", "Wrong patient")
    assert vbc.gap_detail(gap)["gap"]["status"] == "ASSIGNED"
    good = vbc.attach_evidence(gap, "MA", event_date="2026-10-25", value=7.1)
    vbc.verify_evidence(good["event_id"], "analyst", "accepted")
    assert vbc.gap_detail(gap)["gap"]["status"] == "CLOSED"
    vbc.verify_evidence(good["event_id"], "auditor", "rejected", "Report belonged to another patient")
    reopened = vbc.gap_detail(gap)["gap"]
    assert reopened["status"] == "DETECTED" and reopened["reopened"] == 1


def test_conflict_is_resolved_by_a_reviewer_not_the_engine():
    assert result("VBC-010", "GSD-P")["state"] == "EVIDENCE_REVIEW"
    vbc.verify_evidence("VBC-010-E04", "analyst", "rejected", "Mislabelled specimen")
    assert vbc.patient_360("VBC-010")["measures"][0]["result"]["state"] == "MET"


def test_narrative_can_never_be_accepted_as_evidence():
    with pytest.raises(vbc.VbcError, match="Free text"):
        vbc.verify_evidence("VBC-013-E02", "analyst", "accepted")


def test_dispositions_do_not_change_the_measure():
    gap = _assigned("VBC-011", "BCS-P")
    vbc.update_gap(gap, "manager", disposition="PATIENT_DECLINED")
    detail = vbc.gap_detail(gap)
    assert detail["gap"]["disposition"] == "PATIENT_DECLINED" and detail["result"]["state"] == "OPEN_GAP" and detail["result"]["in_denominator"]


def test_outreach_needs_approval_and_assignment():
    gap = gap_for("VBC-011", "BCS-P")["gap_id"]
    draft = vbc.draft_outreach(gap, "SMS", "manager")
    assert "mammogram" in draft["text"] and "cancer" not in draft["text"].lower()
    vbc.approve_outreach(draft["outreach_id"], "lead")
    with pytest.raises(vbc.VbcError, match="Assign"):
        vbc.send_outreach(draft["outreach_id"], "manager")
    _assigned_gap = gap
    vbc.update_gap(_assigned_gap, "c", status="REVIEWED")
    vbc.update_gap(_assigned_gap, "m", owner="J. Ortiz", status="ASSIGNED")
    vbc.send_outreach(draft["outreach_id"], "manager")
    vbc.record_outreach_outcome(draft["outreach_id"], "manager", "SCHEDULED", "2026-11-05")
    assert vbc.gap_detail(gap)["gap"]["status"] == "SCHEDULED"


def test_unapproved_outreach_cannot_be_sent_and_data_corrections_get_none():
    draft = vbc.draft_outreach(gap_for("VBC-006", "COL-P")["gap_id"], "Letter", "manager")
    with pytest.raises(vbc.VbcError, match="approve"):
        vbc.send_outreach(draft["outreach_id"], "manager")
    with pytest.raises(vbc.VbcError, match="internal"):
        vbc.draft_outreach(gap_for("VBC-008", "GSD-P")["gap_id"], "SMS", "manager")


def test_care_plan_needs_approval_and_flags_changes():
    plan = vbc.patient_360("VBC-006")["care_plan"]
    assert plan["status"] == "draft" and len(plan["tasks"]) == 3
    vbc.approve_care_plan("VBC-006", "Dr. Rivera")
    gap = _assigned("VBC-006", "GSD-P")
    attached = vbc.attach_evidence(gap, "MA", event_date="2026-10-25", value=7.2)
    vbc.verify_evidence(attached["event_id"], "analyst", "accepted")
    assert vbc.patient_360("VBC-006")["care_plan"]["status"] == "revision_pending"


# ---------------------------------------------------------------------------
# Risk, metrics and audit
# ---------------------------------------------------------------------------


def test_risk_score_is_transparent():
    risk = vbc.patient_360("VBC-006")["risk"]
    assert risk["category"] == "High" and risk["score"] == sum(item["points"] for item in risk["factors"])
    assert {item["signal"] for item in risk["factors"]} == {"ed_90", "multiple_gaps", "uncontrolled", "age_65", "no_pcp"}
    assert vbc.patient_360("VBC-018")["risk"]["category"] == "Low"


def test_dashboard_baseline_and_improvement():
    dashboard = vbc.dashboard()
    assert dashboard["totals"]["eligible"] == 45 and dashboard["totals"]["met"] == 24 and dashboard["kpis"]["baseline_rate"] == 53.3
    assert dashboard["kpis"]["readmissions"] == 1
    gap = _assigned("VBC-006", "GSD-P")
    attached = vbc.attach_evidence(gap, "MA", event_date="2026-10-25", value=7.2)
    vbc.verify_evidence(attached["event_id"], "analyst", "accepted")
    after = vbc.dashboard()
    assert after["totals"]["met"] == 25 and after["kpis"]["compliance_rate"] > after["kpis"]["baseline_rate"]
    change = next(row for row in after["changes"] if row["patient_id"] == "VBC-006" and row["measure_id"] == "GSD-P")
    assert change["from"] == "OPEN_GAP" and change["to"] == "MET" and any(attached["event_id"] in text or "attached" in text for text in change["evidence"])
    assert after["trend"][-1]["rate"] == after["kpis"]["compliance_rate"]


def test_every_transition_is_audited_and_the_chain_detects_tampering():
    gap = _assigned("VBC-006", "GSD-P")
    state = repo.load()
    transitions = [event for event in state["audit"] if event["action"] == "gap.transition" and event["object_id"] == gap]
    assert {event["new_state"] for event in transitions} >= {"REVIEWED", "ASSIGNED"}
    assert all(event["actor"] and event["trigger"] for event in transitions)
    assert repo.verify_chain(state) == (True, None)
    state["audit"][3]["details"] = "edited"
    assert repo.verify_chain(state)[0] is False


def test_results_are_versioned_not_overwritten():
    gap = _assigned("VBC-006", "GSD-P")
    attached = vbc.attach_evidence(gap, "MA", event_date="2026-10-25", value=7.2)
    vbc.verify_evidence(attached["event_id"], "analyst", "accepted")
    versions = repo.load()["results"]["VBC-006|GSD-P"]
    assert [version["state"] for version in versions] == ["OPEN_GAP", "COMPLETED_PENDING_VERIFICATION", "MET"]
    assert all(version["rule_version"] == catalog.RULE_VERSION for version in versions)


def test_enrollment_gap_rule():
    gaps = engine.enrollment_gaps(PATIENTS_BY_ID["VBC-004"], date(2026, 10, 26))
    assert [(start.isoformat(), end.isoformat()) for start, end in gaps] == [("2026-05-01", "2026-07-14")]


# ---------------------------------------------------------------------------
# API, feature flag and permissions
# ---------------------------------------------------------------------------


def test_api_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    assert client.get("/vbc/dashboard").json()["totals"]["met"] == 24
    assert client.get("/vbc/patients/VBC-006/360").json()["risk"]["category"] == "High"
    gaps = client.get("/vbc/gaps", params={"measure_id": "GSD-P"}).json()
    gap_id = next(row["gap_id"] for row in gaps if row["patient_id"] == "VBC-006")
    assert client.patch(f"/vbc/gaps/{gap_id}", json={"actor": "t", "status": "ASSIGNED"}).status_code == 400
    assert client.patch(f"/vbc/gaps/{gap_id}", json={"actor": "t", "status": "REVIEWED"}).json()["status"] == "REVIEWED"
    upload = client.post(f"/vbc/gaps/{gap_id}/evidence", json={"actor": "t", "event_date": "2026-10-25", "value": 7.0}).json()
    client.post(f"/vbc/evidence/{upload['event_id']}/verify", json={"actor": "r", "decision": "accepted"})
    assert client.get(f"/vbc/gaps/{gap_id}").json()["gap"]["status"] == "CLOSED"
    assert client.get("/vbc/gaps/GAP-9999").status_code == 404
    assert client.post("/vbc/evaluate/VBC-001").status_code == 200
    monkeypatch.setenv("VBC_POC_ENABLED", "0")
    assert TestClient(create_app()).get("/vbc/dashboard").status_code == 404


def test_poc_permissions():
    from frontend.admin_service import DEFAULT_PERMISSIONS, POC_MODULE

    assert DEFAULT_PERMISSIONS["Auditor"][POC_MODULE] == {"View": True, "Edit": False, "Approve": False, "Admin": False}
    assert DEFAULT_PERMISSIONS["Care Manager"][POC_MODULE]["Approve"]
    assert DEFAULT_PERMISSIONS["Operations Admin"][POC_MODULE]["Admin"]
    assert not DEFAULT_PERMISSIONS["Coding Specialist"][POC_MODULE]["View"]
