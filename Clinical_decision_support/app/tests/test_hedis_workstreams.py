"""Workstreams on the 300-member extract: evidence normalization, supplied-vs-calculated QA, measure version
control and the review-to-closure care-gap workflow."""

import pytest
from fastapi.testclient import TestClient

from app.hedis import calculator, ncqa, normalizer, qa, sources, store, versions, workflow
from app.hedis.errors import Conflict, HedisError, SegregationOfDuties
from app.main import create_app
from app.security import rbac, tokens
from app.vbc_poc import repository as poc_repo
from frontend import access_control as ac

OK = lambda module, action: True  # noqa: E731
NO_APPROVE = lambda module, action: action != "Approve"  # noqa: E731


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HEDIS_STATE", str(tmp_path / "hedis.json"))
    monkeypatch.setenv(poc_repo.STATE_ENV, str(tmp_path / "poc.json"))
    monkeypatch.setenv(rbac.ACCESS_STORE_ENV, str(tmp_path / "access.json"))
    monkeypatch.setenv(tokens.SECRET_ENV, "unit-test-secret-0123456789abcdefghijklmnopqrstuv")
    yield


@pytest.fixture(scope="module")
def normalized():
    return normalizer.normalize()


# ---------------------------------------------------------------------------
# Evidence normalizer
# ---------------------------------------------------------------------------


def test_every_source_maps_and_duplicates_merge(normalized):
    summary = normalized["summary"]
    assert summary["mapping_rate"] == 100.0 and not normalized["unmapped"]
    assert set(normalized["sources"]) == {"observations", "procedures", "claims", "conditions", "encounters", "documents"}
    assert summary["raw_records"] == 9797 and summary["golden_records"] < summary["raw_records"] and summary["merged"] > 0
    assert normalized["sources"]["conditions"]["recognised_not_evidence"] > 0  # Z codes recognised, never evidence


def test_flags_capture_real_data_problems(normalized):
    flags = normalized["flags"]
    assert flags["MISSING_DATE"] == 4 and flags["IMPLAUSIBLE"] == 3 and flags["CLAIM_DENIED"] == 73
    assert all(not record.usable for record in normalized["evidence"] if {"MISSING_DATE", "IMPLAUSIBLE"} & set(record.flags))


def test_bp_values_are_parsed_and_lineage_is_kept(normalized):
    bp = next(r for r in normalized["evidence"] if r.concept == "bp" and r.usable)
    assert bp.value > bp.value2 > 0 and bp.unit == "mmHg" and "/" in bp.raw_value
    hba1c = next(r for r in normalized["evidence"] if r.concept == "hba1c" and len(r.lineage) > 1)
    sources_ = {item["source"] for item in hba1c.lineage}
    assert "observations" in sources_ and sources_ & {"procedures", "documents"}  # lab result merged with its procedure / report


def test_mammogram_inherits_report_verification(normalized):
    films = [r for r in normalized["evidence"] if r.concept == "mammogram"]
    assert sum(r.verification == "verified" for r in films) >= 90
    assert any(r.verification == "pending" for r in films)  # the external report still pending verification


def test_hba1c_unit_conversion():
    row = {"observation_id": "X1", "patient_id": "P", "type": "HbA1c", "value": "64", "unit": "mmol/mol", "date": "2026-01-02"}
    record = normalizer._observation(row, sources.period()[1])
    assert record.value == 8.0 and record.unit == "%" and "UNIT_CONVERTED" in record.flags


def test_missing_dob_falls_back_with_a_flag():
    members = normalizer.patient_demographics()
    flagged = [m for m in members.values() if m["age_flag"]]
    assert len(flagged) == 5 and all(m["age"] is not None for m in flagged)


# ---------------------------------------------------------------------------
# Calculator and QA
# ---------------------------------------------------------------------------


def test_calculated_populations_match_the_documented_rules(normalized):
    results = calculator.calculate(versions.BASELINE_SPEC, normalized, normalizer.patient_demographics())
    rates = calculator.rates(results)
    assert rates["GSD"]["denominator"] == rates["BPD-E"]["denominator"] == rates["KED"]["denominator"] == 120
    assert rates["TRC"]["denominator"] == 50
    assert rates["KED"]["numerator"] == 0  # no eGFR or uACR anywhere in the source feeds


def test_qa_finds_supplied_results_the_evidence_does_not_support():
    result = qa.compare()
    measures = result["measures"]
    assert measures["GSD"]["pass"] and measures["BPD-E"]["pass"]
    assert measures["KED"]["categories"]["SUPPLIED_UNSUPPORTED"] == 75 and not measures["KED"]["pass"]
    assert measures["COL-E"]["categories"]["SUPPLIED_UNDERCOUNT"] >= 150
    unsupported = next(i for i in result["items"] if i["category"] == "SUPPLIED_UNSUPPORTED" and i["measure_id"] == "TRC")
    assert "missing" in unsupported["calculated_reason"]


def test_qa_sign_off_rules():
    run = qa.record_run("analyst")["run"]
    with pytest.raises(SegregationOfDuties):
        qa.sign_off(run["run_id"], "analyst", "REJECTED", "Returned to the supplier")
    with pytest.raises(Conflict):
        qa.sign_off(run["run_id"], "lead", "ACCEPTED", "Looks fine to me overall")  # measures outside tolerance
    signed = qa.sign_off(run["run_id"], "lead", "REJECTED", "KED and screening results returned to the supplier")
    assert signed["signoff"]["decision"] == "REJECTED"
    with pytest.raises(Conflict):
        qa.sign_off(run["run_id"], "lead2", "REJECTED", "second time around")


# ---------------------------------------------------------------------------
# Version control center
# ---------------------------------------------------------------------------


def test_version_lifecycle_with_impact_and_four_eyes():
    assert versions.active_spec()[0] == versions.BASELINE_ID
    draft = versions.create_draft(versions.BASELINE_ID, {"GSD.numerator.target_lt": 9.0}, "analyst", "Contract target for MY2026 is 9.0")
    assert draft["status"] == "DRAFT" and draft["changes"] == [{"path": "GSD.numerator.target_lt", "from": 8.0, "to": 9.0}]
    versions.submit(draft["version_id"], "analyst")
    with pytest.raises(Conflict):
        versions.approve(draft["version_id"], "lead")  # no impact analysis yet
    impact = versions.analyze_impact(draft["version_id"], "analyst")
    assert impact["measures"]["GSD"]["rate_delta"] > 0 and impact["members_changed"] > 0
    assert all(change["measure_id"] == "GSD" and change["from"] == "OUTCOME_NOT_CONTROLLED" for change in impact["changes"])
    with pytest.raises(SegregationOfDuties):
        versions.approve(draft["version_id"], "analyst")
    versions.approve(draft["version_id"], "lead")
    versions.activate(draft["version_id"], "lead", "2026-11-01")
    assert versions.active_spec()[0] == draft["version_id"]
    assert qa.compare()["version_id"] == draft["version_id"]
    rolled = versions.rollback(versions.BASELINE_ID, "lead", "Contract amendment withdrawn by the payer")
    assert rolled["status"] == "ACTIVE" and versions.active_spec()[0] == versions.BASELINE_ID


@pytest.mark.parametrize("changes", [{"GSD.numerator.target_lt": 30}, {"BPD-E.numerator.same_day_rule": "average"},
                                     {"GSD.numerator.nonexistent": 1}, {"XYZ.numerator.target_lt": 7}])
def test_draft_validation(changes):
    with pytest.raises(HedisError):
        versions.create_draft(versions.BASELINE_ID, changes, "analyst", "Testing validation of drafts")


def test_diff_between_versions():
    draft = versions.create_draft(versions.BASELINE_ID, {"TRC.numerator.followup_days": 14}, "analyst", "Tighter follow-up window")
    assert versions.diff(versions.BASELINE_ID, draft["version_id"]) == [
        {"path": "TRC.numerator.followup_days", versions.BASELINE_ID: 30, draft["version_id"]: 14}]


# ---------------------------------------------------------------------------
# Review-to-closure workflow
# ---------------------------------------------------------------------------


def test_extract_gaps_are_seeded_into_their_specialization():
    summary = workflow.summary()
    assert summary["total"] == 505 and summary["open"] == 505
    assert set(summary["by_type"]) == set(workflow.WORKFLOWS)
    assert "CLINICAL_REVIEW" in summary["by_type"]["Outcome Management"]["states"]


def test_clinical_gap_closes_only_after_verified_evidence_recalculates_compliant():
    gap = next(g for g in workflow.list_gaps(gap_type="Clinical Opportunity", status="SCHEDULED") if g["measure_id"] == "KED")
    with pytest.raises(HedisError):
        workflow.transition(gap["gap_id"], "COMPLETED", "nurse", "Care Manager", OK, {"service_date": "2026-10-01"})  # no value
    workflow.transition(gap["gap_id"], "COMPLETED", "nurse", "Care Manager", OK, {"service_date": "2026-10-01", "value": 72, "component": "egfr"})
    with pytest.raises(SegregationOfDuties):
        workflow.transition(gap["gap_id"], "VERIFIED", "nurse", "Care Manager", OK)
    with pytest.raises(SegregationOfDuties):
        workflow.transition(gap["gap_id"], "VERIFIED", "analyst", "HEDIS Analyst", NO_APPROVE)
    workflow.transition(gap["gap_id"], "VERIFIED", "analyst", "HEDIS Analyst", OK)
    with pytest.raises(Conflict, match="UACR"):  # KED needs both components
        workflow.transition(gap["gap_id"], "CLOSED", "analyst", "HEDIS Analyst", OK)
    detail = workflow.detail(gap["gap_id"])
    assert detail["calculated"]["state"] == "OPEN_GAP" and detail["captures"][0]["verified_by"] == "analyst"


def test_verified_workflow_evidence_flows_through_normalization():
    gap = next(g for g in workflow.list_gaps(gap_type="Clinical Opportunity", status="SCHEDULED") if g["measure_id"] == "BCS-E")
    workflow.transition(gap["gap_id"], "COMPLETED", "nurse", "Care Manager", OK, {"service_date": "2026-09-30", "document_ref": "RAD-2231"})
    pending = normalizer.normalize(versions._workflow_evidence(store.load()))
    assert pending["sources"]["workflow"]["pending_verification"] == 1  # captured, not yet counted
    workflow.transition(gap["gap_id"], "VERIFIED", "analyst", "HEDIS Analyst", OK)
    counted = normalizer.normalize(versions._workflow_evidence(store.load()))
    assert counted["sources"]["workflow"]["mapped"] == 1
    closed = workflow.transition(gap["gap_id"], "CLOSED", "analyst", "HEDIS Analyst", OK)
    assert closed["status"] == "CLOSED"


def test_outcome_workflow_needs_a_clinician_and_a_controlled_recheck():
    gap = next(g for g in workflow.list_gaps(gap_type="Outcome Management", status="CLINICAL_REVIEW") if g["measure_id"] == "GSD")
    with pytest.raises(SegregationOfDuties):
        workflow.transition(gap["gap_id"], "PLAN_UPDATED", "nurse", "Care Manager", OK, {"plan": "titrate"})
    workflow.transition(gap["gap_id"], "PLAN_UPDATED", "dr", "Physician / APP", OK, {"plan": "Add GLP-1 agonist; recheck in 90 days"})
    workflow.transition(gap["gap_id"], "FOLLOW_UP_SCHEDULED", "nurse", "Care Manager", OK, {"schedule_date": "2026-10-15"})
    workflow.transition(gap["gap_id"], "RECHECK_RESULTED", "nurse", "Care Manager", OK, {"service_date": "2026-10-20", "value": 7.2})
    workflow.transition(gap["gap_id"], "VERIFIED", "analyst", "HEDIS Analyst", OK)
    assert workflow.transition(gap["gap_id"], "CLOSED", "analyst", "HEDIS Analyst", OK)["status"] == "CLOSED"


def test_dispositions():
    gap = next(g for g in workflow.list_gaps(gap_type="Clinical Opportunity", status="ASSIGNED"))
    with pytest.raises(Conflict):
        workflow.close_with_disposition(gap["gap_id"], "UNABLE_TO_CONTACT", "lead", OK, "No answer at all")
    for _ in range(workflow.MIN_ATTEMPTS):
        status = workflow.detail(gap["gap_id"])["gap"]["status"]
        workflow.transition(gap["gap_id"], "OUTREACH", "nurse", "Care Manager", OK, {"channel": "Phone"})
        assert status in ("ASSIGNED", "OUTREACH")
    with pytest.raises(SegregationOfDuties):
        workflow.close_with_disposition(gap["gap_id"], "UNABLE_TO_CONTACT", "nurse", NO_APPROVE, "Three calls, no answer")
    closed = workflow.close_with_disposition(gap["gap_id"], "UNABLE_TO_CONTACT", "lead", OK, "Three calls and a letter, no response")
    assert closed["status"] == "CLOSED" and closed["disposition"] == "UNABLE_TO_CONTACT"


def test_invalid_transition_and_agents_are_refused():
    gap = next(g for g in workflow.list_gaps(gap_type="Clinical Opportunity", status="DETECTED"))
    with pytest.raises(Conflict):
        workflow.transition(gap["gap_id"], "CLOSED", "lead", "HEDIS Lead", OK)
    with pytest.raises(SegregationOfDuties):
        workflow.transition(gap["gap_id"], "REVIEWED", "agent:x", "HEDIS Analyst", OK, {"note": "ok"})


# ---------------------------------------------------------------------------
# Readiness controls and API
# ---------------------------------------------------------------------------


def test_new_readiness_controls():
    controls = {c["control_id"]: c for c in ncqa.evaluate()["controls"]}
    assert controls["IS1-3"]["status"] == "PASS" and controls["HD-4"]["status"] == "PASS"
    assert controls["IS7-5"]["status"] == "FAIL"  # supplied results never compared yet
    qa.record_run("analyst")
    assert next(c for c in ncqa.evaluate()["controls"] if c["control_id"] == "IS7-5")["status"] == "WARN"


def test_workstream_api_permissions():
    access = ac.empty_store()
    for username, role in {"carem": "Care Manager", "analyst": "HEDIS Analyst", "lead": "HEDIS Lead", "exec": "Executive"}.items():
        ac.create_user(access, username, username.title(), "", role, "Passw0rd!x")
    ac.save_store(rbac.store_path(), access)
    client = TestClient(create_app())
    token = lambda u: {"Authorization": "Bearer " + client.post("/hedis/auth/token", json={"username": u, "password": "Passw0rd!x"}).json()["access_token"]}  # noqa: E731
    cm, analyst, lead, executive = token("carem"), token("analyst"), token("lead"), token("exec")
    assert client.get("/hedis/evidence/normalization", headers=cm).status_code == 403
    assert client.get("/hedis/evidence/normalization", headers=executive).json()["summary"]["mapping_rate"] == 100.0
    assert client.post("/hedis/qa/runs", headers=executive).status_code == 403
    run = client.post("/hedis/qa/runs", headers=analyst).json()
    assert client.post(f"/hedis/qa/runs/{run['run_id']}/signoff", headers=analyst,
                       json={"decision": "REJECTED", "note": "Returned to supplier"}).status_code == 403
    assert client.post(f"/hedis/qa/runs/{run['run_id']}/signoff", headers=lead,
                       json={"decision": "REJECTED", "note": "Returned to supplier with differences"}).status_code == 200
    gaps = client.get("/hedis/workflow/gaps", headers=cm, params={"gap_type": "Clinical Opportunity", "status": "DETECTED"}).json()
    moved = client.post(f"/hedis/workflow/gaps/{gaps[0]['gap_id']}/transition", headers=cm, json={"to": "REVIEWED", "fields": {"note": "Confirmed"}})
    assert moved.status_code == 200 and moved.json()["status"] == "REVIEWED"
    assert client.get("/hedis/workflow", headers=executive).status_code == 403
    draft = client.post("/hedis/versions", headers=analyst, json={"based_on": "2026.1", "changes": {"GSD.numerator.target_lt": 9},
                                                                  "rationale": "Payer contract target"}).json()
    assert client.post(f"/hedis/versions/{draft['version_id']}/approve", headers=analyst).status_code == 403
