"""HEDIS engine: registry, supplemental submission, payer reconciliation, tiered agents and NCQA readiness."""

import pytest

from app.hedis import agents, ncqa, reconciliation, registry, store, supplemental
from app.hedis.config import PAYERS, PRACTITIONERS, is_valid_npi, member_id_for, npi_check_digit
from app.hedis.errors import Conflict, HedisError, SegregationOfDuties
from app.vbc_poc import repository as poc_repo
from app.vbc_poc import service as vbc
from app.vbc_poc.golden import GOLDEN_AS_OF

ALLOW = lambda module, action: True  # noqa: E731
DENY = lambda module, action: False  # noqa: E731


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(poc_repo.STATE_ENV, str(tmp_path / "poc.json"))
    monkeypatch.setenv("HEDIS_STATE", str(tmp_path / "hedis.json"))
    monkeypatch.setenv("HEDIS_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setenv("NUUCARE_ACCESS_STORE", str(tmp_path / "access.json"))  # default role matrix, not the machine's store
    monkeypatch.setattr(vbc, "today", lambda: GOLDEN_AS_OF)
    monkeypatch.setattr("app.vbc_poc.metrics.today", lambda: GOLDEN_AS_OF)
    yield


def submit_all(payer, builder="analyst", signer="lead"):
    batch = supplemental.build_batch(payer, builder)
    supplemental.sign_batch(batch["batch_id"], signer)
    supplemental.submit_batch(batch["batch_id"], signer)
    return supplemental.ingest_ack(batch["batch_id"], supplemental.simulate_ack(batch["batch_id"]), signer)


# ---------------------------------------------------------------------------
# Configuration and registry
# ---------------------------------------------------------------------------


def test_npi_check_digit_matches_published_example():
    assert npi_check_digit("123456789") == "3" and is_valid_npi("1234567893")
    assert not is_valid_npi("1234567890") and not is_valid_npi("123") and not is_valid_npi(None)
    assert all(is_valid_npi(p["npi"]) and is_valid_npi(p["group_npi"]) for p in PRACTITIONERS.values())


def test_registry_ties_out_to_the_measure_engine():
    rows = registry.build_rows()
    engine = vbc.summarize(vbc.ensure_initialized())["totals"]
    totals = registry.totals(rows)
    assert len(rows) == 80 and totals["members"] == 20
    assert (totals["denominator"], totals["numerator"]) == (engine["eligible"], engine["met"]) == (45, 24)
    assert len({row["key"] for row in rows}) == len(rows)


def test_registry_classifies_numerator_source_by_ncqa_class():
    rows = {(r["patient_id"], r["measure_id"]): r for r in registry.build_rows()}
    claim_backed = rows[("VBC-001", "BCS-P")]
    assert claim_backed["numerator_source"] == "ADMINISTRATIVE" and claim_backed["evidence"][0]["code"] == "77067"
    # Duplicate lab + claim HbA1c: the engine keeps the lab (it carries the value the outcome needs), so the payer needs it as supplemental data
    assert rows[("VBC-001", "GSD-P")]["numerator_source"] == "STANDARD_SUPPLEMENTAL"
    assert rows[("VBC-005", "BPD-P")]["numerator_source"] == "STANDARD_SUPPLEMENTAL"  # device-transmitted home BP
    assert all(r["numerator_source"] is None for r in rows.values() if not r["numerator_met"])
    assert rows[("VBC-017", "GSD-P")]["excluded"] and rows[("VBC-017", "GSD-P")]["exclusion_concept"] == "hospice"


def test_member_ids_are_stable_and_payer_formatted():
    rows = registry.build_rows()
    for row in rows:
        assert row["member_id"] == member_id_for(row["patient_id"]) and row["member_id"].startswith(PAYERS[row["payer"]].member_prefix)


def test_phi_masking_removes_name_and_dob():
    view = registry.view(phi=False)
    assert all(r["name"].startswith("Member ") and r["dob"] is None for r in view["rows"])
    assert any(r["name"] == "Maria Alvarez" for r in registry.view(phi=True)["rows"])


def test_snapshot_lock_requires_four_eyes_and_no_drift():
    snapshot = registry.publish_snapshot("analyst")
    assert snapshot["row_count"] == 80 and len(snapshot["content_hash"]) == 64
    with pytest.raises(Conflict):
        registry.publish_snapshot("analyst")  # unchanged registry
    with pytest.raises(SegregationOfDuties):
        registry.lock_period("analyst")
    locked = registry.lock_period("lead")
    assert locked["snapshot_id"] == snapshot["snapshot_id"]
    with pytest.raises(Conflict):
        supplemental.build_batch("ATH", "analyst")  # submissions close with the period
    with pytest.raises(HedisError):
        registry.reopen_period("lead", "   ")
    registry.reopen_period("lead", "Late lab feed received for Q3")
    assert registry.view()["locked"] is None


def test_snapshot_diff_reports_gained_compliance():
    registry.publish_snapshot("analyst")
    gap = next(g for g in vbc.list_gaps() if g["patient_id"] == "VBC-006" and g["measure_id"] == "GSD-P")
    event = vbc.attach_evidence(gap["gap_id"], "nurse", event_date="2026-10-20", value=7.1)
    vbc.verify_evidence(event["event_id"], "analyst", "accepted")
    second = registry.publish_snapshot("analyst")
    assert any("GSD-P" in key for key in second["diff"]["gained_compliance"])


# ---------------------------------------------------------------------------
# Supplemental submission
# ---------------------------------------------------------------------------


def test_only_supplemental_numerators_become_records():
    records = supplemental.candidates()
    assert len(records) == 22
    assert all(r["source_class"] in ("STANDARD_SUPPLEMENTAL", "NON_STANDARD_SUPPLEMENTAL") for r in records)
    assert len({r["record_key"] for r in records}) == len(records)
    assert all(not r["errors"] and r["status"] == "READY" for r in records)


def test_validation_rules():
    record = dict(next(r for r in supplemental.candidates() if r["measure_id"] == "GSD-P"))
    errors, _ = supplemental.validate({**record, "value": None, "rendering_npi": "1234567890", "member_id": "XX1"})
    assert {e["code"] for e in errors} == {"E-VALUE", "E-NPI", "E-MEMBER"}
    errors, _ = supplemental.validate({**record, "service_date": "2024-01-01"})
    assert [e["code"] for e in errors] == ["E-DOS"]
    errors, _ = supplemental.validate({**record, "source_class": "NON_STANDARD_SUPPLEMENTAL", "psv": "PENDING"})
    assert [e["code"] for e in errors] == ["E-PSV"]
    errors, _ = supplemental.validate({**record, "payer": "CGH", "member_id": "CGH123456789", "code_system": "ICD-10-CM"})
    assert "E-CODESYS" in {e["code"] for e in errors}


def test_batch_lifecycle_file_controls_and_ack():
    batch = supplemental.build_batch("ATH", "analyst")
    assert batch["status"] == "DRAFT" and len(batch["records"]) == 8
    with pytest.raises(Conflict):
        supplemental.build_batch("ATH", "analyst")  # one open draft per payer
    with pytest.raises(SegregationOfDuties):
        supplemental.sign_batch(batch["batch_id"], "analyst")
    with pytest.raises(SegregationOfDuties):
        supplemental.sign_batch(batch["batch_id"], "agent:x")
    with pytest.raises(Conflict):
        supplemental.submit_batch(batch["batch_id"], "lead")  # not signed
    supplemental.sign_batch(batch["batch_id"], "lead")
    sent = supplemental.submit_batch(batch["batch_id"], "lead")
    text = open(sent["file"], encoding="utf-8").read().splitlines()
    assert text[0].startswith("HDR|ATH|SUPP|") and text[-1].startswith(f"TRL|{len(batch['records'])}|")
    assert text[-1].split("|")[2] == sent["control"]["detail_sha256"]
    acked = supplemental.ingest_ack(batch["batch_id"], supplemental.simulate_ack(batch["batch_id"]), "lead")
    assert acked["ack"]["accepted"] + acked["ack"]["rejected"] == 8 and acked["status"] in ("ACKNOWLEDGED", "PARTIAL")
    with pytest.raises(Conflict):
        supplemental.ingest_ack(batch["batch_id"], "ACK|x", "lead")


def test_ack_with_foreign_records_is_refused():
    batch = supplemental.build_batch("CGH", "analyst")
    supplemental.sign_batch(batch["batch_id"], "lead")
    supplemental.submit_batch(batch["batch_id"], "lead")
    forged = "batch_id,record_key,status,reason_code,reason\nSUP-9999,deadbeef,ACCEPTED,,\n"
    with pytest.raises(HedisError):
        supplemental.ingest_ack(batch["batch_id"], forged, "lead")


def test_rejection_correction_and_linked_resubmission():
    first = submit_all("CGH")
    assert first["ack"]["rejected"] == 10  # Dr. Rivera's individual NPI is not on the Care Gap roster
    rejected = [r for r in supplemental.candidates("CGH") if r["status"] == "NEEDS_CORRECTION"]
    with pytest.raises(HedisError):
        supplemental.apply_correction(rejected[0]["record_key"], "rendering_npi", "1234567890", "analyst", "typo")
    for record in rejected:
        supplemental.apply_correction(record["record_key"], "rendering_npi", PRACTITIONERS["Dr. A. Rivera"]["group_npi"], "analyst", "Group NPI")
    second = supplemental.build_batch("CGH", "analyst")
    assert second["resubmissions"] == 10 and all(r["resubmission_of"] == first["batch_id"] for r in second["records"])
    supplemental.sign_batch(second["batch_id"], "lead")
    supplemental.submit_batch(second["batch_id"], "lead")
    done = supplemental.ingest_ack(second["batch_id"], supplemental.simulate_ack(second["batch_id"]), "lead")
    assert done["status"] == "ACKNOWLEDGED"
    assert all(r["status"] == "ACCEPTED" for r in supplemental.candidates("CGH"))


def test_accepted_records_are_never_resubmitted():
    submit_all("ATH")
    remaining = [r for r in supplemental.candidates("ATH") if r["submittable"]]
    assert all(r["status"] != "ACCEPTED" for r in remaining)


# ---------------------------------------------------------------------------
# Payer reconciliation
# ---------------------------------------------------------------------------


def test_reconciliation_traces_root_cause_through_the_ledger():
    before = reconciliation.reconcile("ATH", reconciliation.generate_payer_file("ATH"), "analyst")
    causes = {i["root_cause"] for i in before["items"] if i["category"] == "INTERNAL_ONLY_COMPLIANT"}
    assert causes == {"SUPPLEMENTAL_NOT_SUBMITTED"}
    assert {"PAYER_ONLY_COMPLIANT", "NOT_ON_PAYER_FILE", "NOT_IN_REGISTRY"} <= set(before["summary"]["counts"])
    submit_all("ATH")
    after = reconciliation.reconcile("ATH", reconciliation.generate_payer_file("ATH"), "analyst")
    assert after["summary"]["agreement"] > before["summary"]["agreement"]
    remaining = {i["root_cause"] for i in after["items"] if i["category"] == "INTERNAL_ONLY_COMPLIANT"}
    assert remaining <= {"SUPPLEMENTAL_REJECTED"}


def test_payer_file_validation():
    with pytest.raises(HedisError, match="missing column"):
        reconciliation.parse_payer_file("ATH", "member,measure\n")
    bad = "member_id,measure_id,payer_status,numerator_source,last_service_date\nCGH123,GSD-P,MAYBE,NONE,\n"
    with pytest.raises(HedisError, match="rejected"):
        reconciliation.parse_payer_file("ATH", bad)


def test_rate_impact_projection():
    run = reconciliation.reconcile("CGH", reconciliation.generate_payer_file("CGH"), "analyst")
    for entry in run["summary"]["measures"].values():
        if entry["payer"]["denominator"]:
            assert entry["payer_rate_if_resolved"] >= entry["payer"]["rate"]


def test_disputes_only_for_payer_side_root_causes():
    run = reconciliation.reconcile("ATH", reconciliation.generate_payer_file("ATH"), "analyst")
    internal = next(i for i in run["items"] if i["root_cause"] == "SUPPLEMENTAL_NOT_SUBMITTED")
    with pytest.raises(Conflict):
        reconciliation.open_dispute(run["run_id"], internal["item_id"], "stars", "basis")
    payer_side = next(i for i in run["items"] if i["disputable"])
    with pytest.raises(SegregationOfDuties):
        reconciliation.open_dispute(run["run_id"], payer_side["item_id"], "agent:x", "basis")
    dispute = reconciliation.open_dispute(run["run_id"], payer_side["item_id"], "stars", "Roster shows attribution since January")
    with pytest.raises(Conflict):
        reconciliation.open_dispute(run["run_id"], payer_side["item_id"], "stars", "again")
    assert reconciliation.resolve_dispute(dispute["dispute_id"], "stars", "UPHELD", "Payer corrected")["status"] == "UPHELD"


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def test_l1_executes_reversible_actions_and_is_idempotent():
    runs = agents.run_tier("L1", "analyst")
    assert sum(r["executed"] for r in runs) > 0 and all(r["failed"] == 0 for r in runs)
    drafts = [b for b in supplemental.view()["batches"] if b["status"] == "DRAFT"]
    assert {b["built_by"] for b in drafts} == {"agent:supplemental_preparer"} and len(drafts) == 2
    again = agents.run_tier("L1", "analyst")
    assert sum(r["executed"] for r in again) == 0
    # the agent built the draft, so any person may sign it — but never the agent itself
    with pytest.raises(SegregationOfDuties):
        supplemental.sign_batch(drafts[0]["batch_id"], "agent:supplemental_preparer")
    supplemental.sign_batch(drafts[0]["batch_id"], "lead")


def test_l1_actions_can_be_reverted():
    agents.run_agent("supplemental_preparer", "analyst")
    executed = next(p for p in agents.view()["proposals"] if p["action"] == "supplemental.build_draft")
    agents.revert(executed["proposal_id"], "lead")
    batch = next(b for b in supplemental.view()["batches"] if b["batch_id"] == executed["result"]["batch_id"])
    assert batch["status"] == "DISCARDED"
    assert all(r["status"] == "READY" for r in supplemental.candidates(batch["payer"]))


def test_l2_needs_a_permitted_human():
    submit_all("CGH")
    run = agents.run_agent("rejection_resolver", "analyst")
    assert run["proposed"] == 10 and run["executed"] == 0
    proposal = next(p for p in agents.view()["proposals"] if p["status"] == "PROPOSED")
    with pytest.raises(SegregationOfDuties):
        agents.decide(proposal["proposal_id"], "agent:rejection_resolver", "approve", ALLOW)
    with pytest.raises(SegregationOfDuties):
        agents.decide(proposal["proposal_id"], "viewer", "approve", DENY)
    approved = agents.decide(proposal["proposal_id"], "analyst", "approve", ALLOW)
    assert approved["status"] == "APPROVED" and approved["decided_by"] == "analyst"
    assert supplemental.view()["payers"]["CGH"]["ready_resubmit"] == 1
    rejected = agents.decide(next(p for p in agents.view()["proposals"] if p["status"] == "PROPOSED")["proposal_id"], "analyst", "reject", ALLOW)
    assert rejected["status"] == "REJECTED"


def test_l3_escalates_and_never_executes():
    run = agents.run_agent("clinical_escalation", "analyst")
    assert run["escalated"] == 5 and run["executed"] == 0
    item = next(p for p in agents.view()["proposals"] if p["status"] == "ESCALATED")
    assert item["escalate_to"] == ["Physician / APP"] and item["result"] is None
    with pytest.raises(SegregationOfDuties):
        agents.resolve_escalation(item["proposal_id"], "carol", "Care Manager", "RESOLVED", "Not mine")
    with pytest.raises(Conflict):
        agents.decide(item["proposal_id"], "lead", "approve", ALLOW)
    resolved = agents.resolve_escalation(item["proposal_id"], "dr", "Physician / APP", "RESOLVED", "Treatment intensified")
    assert resolved["status"] == "RESOLVED" and resolved["result"] is None


def test_kill_switch_stops_a_tier():
    agents.set_tier("L2", False, "ops", "incident review")
    with pytest.raises(Conflict):
        agents.run_agent("reconciliation_analyst", "analyst")
    agents.run_agent("dq_sentinel", "analyst")  # other tiers keep running
    agents.set_tier("L2", True, "ops")
    agents.run_agent("reconciliation_analyst", "analyst")


def test_audit_sentinel_flags_a_tampered_chain():
    agents.run_agent("dq_sentinel", "analyst")
    with store.transaction() as state:
        state["audit"][0]["details"] = "edited after the fact"
    run = agents.run_agent("audit_sentinel", "analyst")
    assert run["escalated"] >= 1
    assert any(p["title"] == "HEDIS audit chain broken" for p in agents.view()["proposals"])


# ---------------------------------------------------------------------------
# NCQA readiness
# ---------------------------------------------------------------------------


def test_readiness_is_honest_about_licensed_specs():
    result = ncqa.evaluate()
    by_id = {c["control_id"]: c for c in result["controls"]}
    assert by_id["HD-1"]["status"] == "FAIL" and result["verdict"] == "Not certifiable yet"
    assert by_id["IS7-1"]["status"] == "PASS" and by_id["IS6-1"]["status"] == "PASS"
    assert by_id["HD-3"]["status"] == "WARN"  # internal test deck passes; NCQA's deck still required
    assert by_id["SEC-2"]["status"] == "PASS"  # every HEDIS API route is behind require()
    assert 0 < result["score"] < 100


def test_readiness_improves_as_work_is_done():
    start = ncqa.evaluate()["score"]
    for payer in PAYERS:
        submit_all(payer)
    for record in [r for r in supplemental.candidates() if r["status"] == "NEEDS_CORRECTION"]:
        supplemental.apply_correction(record["record_key"], "rendering_npi", PRACTITIONERS[record["provider"]]["group_npi"], "analyst", "roster")
    for payer in PAYERS:
        if any(r["submittable"] for r in supplemental.candidates(payer)):
            submit_all(payer)
        reconciliation.reconcile(payer, reconciliation.generate_payer_file(payer), "analyst")
    registry.publish_snapshot("analyst")
    assert ncqa.evaluate()["score"] > start


def test_attestation_rules():
    with pytest.raises(HedisError):
        ncqa.attest("IS6-1", "lead", "HEDIS Lead", "Automated controls cannot be attested over")
    with pytest.raises(HedisError):
        ncqa.attest("IS4-2", "analyst", "HEDIS Analyst", "Over-read of 30 charts completed")
    with pytest.raises(HedisError):
        ncqa.attest("IS4-2", "lead", "HEDIS Lead", "done")
    ncqa.attest("IS4-2", "lead", "HEDIS Lead", "Over-read of 30 abstracted charts completed; 29/30 agreed")
    assert next(c for c in ncqa.evaluate()["controls"] if c["control_id"] == "IS4-2")["status"] == "ATTESTED"


def test_assessment_and_evidence_pack():
    assessment = ncqa.record_assessment("lead")
    assert assessment["assessments"][0]["assessment_id"].startswith("NCQ-")
    pack = ncqa.evidence_pack()
    assert "# NCQA readiness evidence pack" in pack["markdown"] and "HD-1" in pack["markdown"] and "Maria" not in pack["markdown"]


def test_every_write_is_on_the_hash_chain():
    agents.run_agent("dq_sentinel", "analyst")
    submit_all("ATH", builder="analyst", signer="lead")
    state = store.load()
    assert store.verify_chain(state) == (True, None)
    actions = {e["action"] for e in state["audit"]}
    assert {"agent.executing", "agent.action_executed", "agent.run", "supplemental.batch_built", "supplemental.batch_signed",
            "supplemental.batch_submitted", "supplemental.ack_ingested"} <= actions
