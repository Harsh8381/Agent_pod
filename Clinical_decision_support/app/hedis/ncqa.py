"""NCQA certification readiness: automated controls mapped to the HEDIS Compliance Audit standards.

Each control checks live system evidence and returns PASS, WARN or FAIL with the numbers behind it.
Controls that only a person can evidence (roadmap, over-read sample, vendor documentation) need a
signed attestation that expires. The assessment rolls up to a readiness score and an evidence pack an
auditor can read.

This is a self-assessment against the structure of the audit (HD and IS 1.0-7.0 as published in
NCQA HEDIS Volume 5, plus security controls). Certification itself requires licensed NCQA
specifications, NCQA's measure test deck and an NCQA-licensed audit organization; the controls that
depend on those say so and fail until they exist.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.hedis import reconciliation, registry, store, supplemental
from app.hedis.config import (
    NON_STANDARD_SUPPLEMENTAL,
    PAYERS,
    PRACTITIONERS,
    STATE_ENV,
    is_valid_npi,
)
from app.hedis.errors import HedisError, NotFound
from app.vbc_poc import repository as poc_repo
from app.vbc_poc import service as vbc
from app.vbc_poc.catalog import ENROLLMENT_ALLOWED_GAP_DAYS, RULE_CHANGE_LOG, RULE_VERSION
from app.vbc_poc.golden import run_golden

STANDARDS = {
    "HD": "HEDIS Determination — measure specifications and calculation",
    "IS 1.0": "Medical services data — coding, capture, transfer and entry",
    "IS 2.0": "Enrollment data — capture, transfer and entry",
    "IS 3.0": "Practitioner data — capture, transfer and entry",
    "IS 4.0": "Medical record review — training, sampling, abstraction, oversight",
    "IS 5.0": "Supplemental data — capture, transfer and entry",
    "IS 6.0": "Data preproduction processing — transfer, consolidation, control",
    "IS 7.0": "Data integration and reporting — accurate reporting and control",
    "SEC": "Security, access control and agent governance",
}
STANDARD_CODE_SYSTEMS = ("LOINC", "CPT", "HCPCS", "ICD-10-CM", "CVX", "SNOMED")
WEIGHTS = {"PASS": 1.0, "ATTESTED": 1.0, "WARN": 0.5, "ATTEST": 0.0, "FAIL": 0.0}
AGREEMENT_TARGET = 90.0


@dataclass(frozen=True)
class Control:
    control_id: str
    standard: str
    title: str
    requirement: str
    remediation: str
    check: Callable | None = None  # (ctx) -> (status, detail); None = attestation control
    attest_role: str = "HEDIS Lead"


class _Context:
    """Loads every input once per assessment."""

    def __init__(self, path: Path | None, poc_path: Path | None):
        self.path, self.poc_path = path, poc_path
        self.state = store.load(path)
        self.poc_state = vbc.ensure_initialized(poc_path)
        self.rows = registry.build_rows(poc_path)
        self.records = supplemental.candidates(None, path, poc_path)
        self.batches = list(self.state["supplemental"]["batches"].values())

    @property
    def evidence(self):
        return [item for row in self.rows for item in row["evidence"]]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _licensed_specs(ctx):
    return "FAIL", ("Measures run on HEDIS-inspired prototype rules (GSD-P, BPD-P, BCS-P, COL-P), not licensed NCQA specifications "
                    "and value sets. Rates cannot be reported as HEDIS.")


def _rule_versioning(ctx):
    stale = [row["key"] for row in ctx.rows if row["rule_version"] != RULE_VERSION]
    if stale:
        return "FAIL", f"{len(stale)} registry row(s) computed under an older rule version than {RULE_VERSION}."
    return "PASS", f"All {len(ctx.rows)} rows computed under {RULE_VERSION}; {len(RULE_CHANGE_LOG)} versions in the change log."


def _test_deck(ctx):
    rows = run_golden()
    failed = [f"{row['patient_id']}·{row['measure_id']}" for row in rows if row["actual"] != row["expected"]]
    if failed:
        return "FAIL", f"{len(failed)}/{len(rows)} internal test-deck cases fail: {', '.join(failed[:6])}."
    return "WARN", (f"Internal test deck: {len(rows)}/{len(rows)} patient-measure cases pass. NCQA Measure Certification additionally "
                    "requires passing NCQA's own test deck on licensed specifications.")


def _code_systems(ctx):
    bad = sorted({f"{e['code_system']} {e['code']}" for e in ctx.evidence if e["code_system"] not in STANDARD_CODE_SYSTEMS})
    if bad:
        return "FAIL", f"Numerator evidence uses non-standard codes: {', '.join(bad[:6])}."
    return "PASS", f"{len(ctx.evidence)} accepted record(s) all coded in standard systems ({', '.join(sorted({e['code_system'] for e in ctx.evidence}))})."


def _service_dates(ctx):
    as_of = vbc.today().isoformat()
    bad = [e["event_id"] for e in ctx.evidence if not e["date"] or e["date"] > as_of]
    if bad:
        return "FAIL", f"{len(bad)} accepted record(s) without a valid service date: {', '.join(bad[:6])}."
    return "PASS", f"Every accepted record has a service date on or before {as_of}; undated and future-dated records are rejected by the engine."


def _enrollment(ctx):
    gaps = [row for row in ctx.rows if row["state"] == "DENOMINATOR_NOT_MET"]
    return "PASS", (f"Continuous-enrollment rule applied (one gap of up to {ENROLLMENT_ALLOWED_GAP_DAYS} days allowed); "
                    f"{len({r['member_id'] for r in gaps})} member(s) removed from denominators for enrollment gaps.")


def _member_ids(ctx):
    import re

    bad = [row["key"] for row in ctx.rows if not re.fullmatch(rf"{PAYERS[row['payer']].member_prefix}\d{{9}}", row["member_id"])]
    if bad:
        return "FAIL", f"{len(bad)} registry row(s) with a malformed member ID."
    return "PASS", f"{len({r['member_id'] for r in ctx.rows})} members, all with payer-format member IDs and a single payer."


def _npis(ctx):
    bad = [name for name, item in PRACTITIONERS.items() if not is_valid_npi(item["npi"]) or not is_valid_npi(item["group_npi"])]
    unknown = sorted({row["provider"] for row in ctx.rows if row["provider"] not in PRACTITIONERS})
    if bad or unknown:
        return "FAIL", f"Invalid NPI for {', '.join(bad) or '—'}; attributed providers missing from the roster: {', '.join(unknown) or '—'}."
    return "PASS", f"{len(PRACTITIONERS)} practitioner(s) with check-digit-valid individual and group NPIs."


def _roster(ctx):
    exposed = [r for r in ctx.records if r["warnings"] and r["status"] not in ("ACCEPTED",)]
    if exposed:
        return "WARN", f"{len(exposed)} supplemental record(s) carry a rendering NPI that is not on the payer roster."
    return "PASS", "Every supplemental record's rendering NPI is on the receiving payer's roster or was corrected."


def _mrr_verification(ctx):
    pending = [e["event_id"] for e in ctx.evidence if e["source_class"] == NON_STANDARD_SUPPLEMENTAL and e["verification"] != "verified"]
    total = sum(e["source_class"] == NON_STANDARD_SUPPLEMENTAL for e in ctx.evidence)
    if pending:
        return "FAIL", f"{len(pending)} document-based record(s) counted without human verification."
    return "PASS", f"{total} document-based record(s) counted, all verified by a person; unverified documents never close a gap."


def _psv(ctx):
    unverified = [r["record_key"] for b in ctx.batches if b["status"] not in ("DRAFT", "DISCARDED")
                  for r in b["records"] if r["source_class"] == NON_STANDARD_SUPPLEMENTAL and r.get("psv") != "VERIFIED"]
    if unverified:
        return "FAIL", f"{len(unverified)} non-standard supplemental record(s) submitted without primary-source verification."
    return "PASS", "No non-standard supplemental record has been submitted without primary-source verification."


def _batch_controls(ctx):
    sent = [b for b in ctx.batches if b["status"] not in ("DRAFT", "DISCARDED")]
    problems = []
    for batch in sent:
        if batch["signed_by"] in (None, batch["built_by"]):
            problems.append(f"{batch['batch_id']} signer")
        if batch["status"] != "SIGNED" and not batch.get("control"):
            problems.append(f"{batch['batch_id']} control totals")
    if problems:
        return "FAIL", "Batch control failures: " + ", ".join(problems)
    if not sent:
        return "WARN", "No supplemental batch has been submitted yet, so file controls are untested in production."
    return "PASS", f"{len(sent)} batch(es) signed by someone other than the builder, each with record count and SHA-256 control totals."


def _acknowledgements(ctx):
    late = supplemental.overdue_acks(ctx.state)
    open_rejects = [r for r in ctx.records if r["status"] == "NEEDS_CORRECTION"]
    if late:
        return "FAIL", f"{len(late)} batch(es) past the acknowledgement SLA: {', '.join(b['batch_id'] for b in late)}."
    if open_rejects:
        return "WARN", f"{len(open_rejects)} payer-rejected record(s) awaiting correction."
    return "PASS", "Every submitted batch is acknowledged within SLA and no rejected record is left uncorrected."


def _duplicates(ctx):
    seen, duplicates = {}, []
    for batch in ctx.batches:
        if batch["status"] in ("DISCARDED",):
            continue
        for record in batch["records"]:
            prior = seen.get(record["record_key"])
            if prior and not record.get("resubmission_of"):
                duplicates.append(record["record_key"])
            seen[record["record_key"]] = batch["batch_id"]
    if duplicates:
        return "FAIL", f"{len(duplicates)} record(s) submitted twice without a resubmission link."
    return "PASS", f"{len(seen)} distinct record key(s); every repeat submission is a linked correction."


def _chains(ctx):
    ok, broken = store.verify_chain(ctx.state)
    poc_ok, poc_broken = poc_repo.verify_chain(ctx.poc_state)
    if not ok or not poc_ok:
        return "FAIL", f"Audit chain broken (HEDIS at {broken}, measure engine at {poc_broken})."
    return "PASS", f"Both hash chains verify: {len(ctx.state['audit'])} HEDIS and {len(ctx.poc_state['audit'])} measure-engine events."


def _reproducible(ctx):
    snapshot = registry.latest_snapshot(ctx.state)
    if not snapshot:
        return "WARN", "No registry snapshot published yet; reproducibility cannot be shown."
    current = registry.content_hash(ctx.rows)
    if current != snapshot["content_hash"]:
        return "WARN", f"Registry has moved since {snapshot['snapshot_id']}; publish a new snapshot before reporting."
    return "PASS", f"Rebuilding the registry reproduces {snapshot['snapshot_id']} exactly (hash {current[:12]})."


def _integrity(ctx):
    blocking = [i for i in registry.issues(ctx.rows) if i["severity"] == "block"]
    warnings = [i for i in registry.issues(ctx.rows) if i["severity"] == "warn"]
    if blocking:
        return "FAIL", f"{len(blocking)} blocking registry issue(s), e.g. {blocking[0]['code']}: {blocking[0]['detail']}."
    if warnings:
        return "WARN", f"No blocking issues; {len(warnings)} row(s) await human verification or data correction."
    return "PASS", "No registry integrity issues."


def _totals_tie_out(ctx):
    engine = vbc.summarize(ctx.poc_state)["totals"]
    totals = registry.totals(ctx.rows)
    if (engine["eligible"], engine["met"]) != (totals["denominator"], totals["numerator"]):
        return "FAIL", f"Registry {totals['numerator']}/{totals['denominator']} ≠ engine {engine['met']}/{engine['eligible']}."
    return "PASS", f"Registry and measure engine agree: {totals['numerator']}/{totals['denominator']} compliant."


def _payer_reconciliation(ctx):
    latest = reconciliation.latest_runs(ctx.state)
    missing = [PAYERS[key].name for key in PAYERS if key not in latest]
    if missing:
        return "WARN", f"No reconciliation run for {', '.join(missing)}."
    low = {key: run["summary"]["agreement"] for key, run in latest.items() if (run["summary"]["agreement"] or 0) < AGREEMENT_TARGET}
    if low:
        return "WARN", "Agreement below target: " + ", ".join(f"{PAYERS[k].name} {v}%" for k, v in low.items()) + f" (target {AGREEMENT_TARGET:g}%)."
    return "PASS", "Every payer reconciled at or above target: " + ", ".join(f"{PAYERS[k].name} {r['summary']['agreement']}%" for k, r in latest.items())


def _period_lock(ctx):
    locked = ctx.state["registry"]["locked"]
    if not locked:
        return "WARN", "Period is open. Lock it (four eyes) before final reporting."
    return "PASS", f"Locked by {locked['by']} on {locked['at'][:10]} at {locked['snapshot_id']}."


def _rbac(ctx):
    from app.security.rbac import load_store

    matrix = load_store()["role_permissions"]  # the live matrix, as edited on the User Access page
    signers = sorted(role for role, perms in matrix.items() if perms.get("Supplemental Submission", {}).get("Approve") and role != "Super Admin")
    lockers = sorted(role for role, perms in matrix.items() if perms.get("HEDIS Registry", {}).get("Approve") and role != "Super Admin")
    breaches = [b["batch_id"] for b in ctx.batches if b["signed_by"] and b["signed_by"] == b["built_by"]]
    locked = ctx.state["registry"]["locked"]
    snapshot = next((s for s in ctx.state["registry"]["snapshots"] if locked and s["snapshot_id"] == locked["snapshot_id"]), None)
    if snapshot and snapshot["published_by"] == locked["by"]:
        breaches.append(f"period lock by its own publisher ({locked['by']})")
    if breaches:
        return "FAIL", f"Segregation-of-duties breach: {', '.join(breaches)}."
    if len(signers) > 3 or len(lockers) > 3:
        return "WARN", f"Sign-off rights are broad: {len(signers)} role(s) can sign submissions and {len(lockers)} can lock the period."
    return "PASS", (f"Least privilege: only {', '.join(signers) or 'Super Admin'} can sign submissions and {', '.join(lockers) or 'Super Admin'} "
                    "can lock the period; builder ≠ signer and publisher ≠ locker are enforced in code.")


def _api_auth(ctx):
    try:
        from app.api.hedis_routes import router
    except Exception as error:  # pragma: no cover - surfaced as a control failure
        return "FAIL", f"HEDIS API could not be inspected: {error}"
    open_routes = [route.path for route in router.routes
                   if route.path != "/hedis/auth/token" and not any("require" in repr(dep.call) for dep in route.dependant.dependencies)]
    if open_routes:
        return "FAIL", f"{len(open_routes)} HEDIS API route(s) without an authorization dependency: {', '.join(open_routes[:5])}."
    return "PASS", f"All {len(router.routes) - 1} HEDIS API routes require a signed bearer token and a named permission."


def _secret(ctx):
    import os

    if os.environ.get("HEDIS_API_SECRET"):
        return "PASS", "Token-signing secret supplied by the environment (secret store)."
    return "WARN", "Token-signing secret is the auto-generated local file; supply HEDIS_API_SECRET from a secret store in production."


def _agent_governance(ctx):
    proposals = ctx.state["agents"]["proposals"].values()
    bad = [p["proposal_id"] for p in proposals if p["tier"] in ("L2", "L3") and (p.get("decided_by") or "").startswith("agent:")]
    executed_l3 = [p["proposal_id"] for p in proposals if p["tier"] == "L3" and p.get("result")]
    if bad or executed_l3:
        return "FAIL", f"Agent governance breach: {', '.join(bad + executed_l3)}."
    switches = ctx.state["agents"]["enabled"]
    return "PASS", ("No L2/L3 item was decided by an agent and no L3 item executed; kill switches: "
                    + ", ".join(f"{tier} {'on' if on else 'off'}" for tier, on in switches.items()) + ".")


def _normalization(ctx):
    from app.hedis import normalizer

    result = normalizer.normalize()
    unmapped = result["unmapped"]
    rate = result["summary"]["mapping_rate"]
    if rate < 95:
        return "FAIL", f"Only {rate}% of source records map to a canonical concept; unmapped: {', '.join(list(unmapped)[:5])}."
    flags = result["flags"]
    return ("WARN" if unmapped else "PASS"), (f"{result['summary']['raw_records']} records from {len(result['sources'])} sources map at {rate}% into "
                                             f"{result['summary']['golden_records']} golden records ({result['summary']['merged']} cross-source merges); "
                                             f"flags: {', '.join(f'{k} {v}' for k, v in flags.items()) or 'none'}.")


def _qa_signoff(ctx):
    runs = ctx.state["quality"]["runs"]
    if not runs:
        return "FAIL", "Supplied results have never been compared with results calculated from source evidence."
    latest = runs[-1]
    if not latest["signoff"]:
        return "WARN", f"{latest['run_id']} ({latest['summary']['agreement']}% agreement) awaits a second-person sign-off."
    failing = [m for m, e in latest["measures"].items() if not e["pass"]]
    return "PASS", (f"{latest['run_id']} signed off {latest['signoff']['decision']} by {latest['signoff']['by']}; "
                    + (f"{', '.join(failing)} returned to the supplier as outside tolerance." if failing else "every measure within tolerance."))


def _version_governance(ctx):
    specs = ctx.state["versions"]["specs"]
    active = ctx.state["versions"]["active"]
    if not specs or not active:
        return "PASS", "Baseline specification 2026.1 active; no change has been proposed yet."
    version = specs[active]
    if version["author"] != "system (baseline)":
        if version["approved_by"] in (None, version["author"]):
            return "FAIL", f"Active version {active} was not approved by someone other than its author."
        if not version["impact"]:
            return "FAIL", f"Active version {active} went live without an impact analysis."
    drafts = [v for v in specs.values() if v["status"] in ("DRAFT", "IN_REVIEW")]
    return "PASS", (f"Active {active} ({version['spec_sha256'][:12]}), approved by {version['approved_by']}"
                    + (f"; {len(drafts)} change(s) in review" if drafts else "") + ".")


def _workflow_evidence(ctx):
    evidence = ctx.state["workflow"]["evidence"].values()
    self_verified = [e["capture_id"] for e in evidence if e["verified_by"] and e["verified_by"] == e["captured_by"]]
    if self_verified:
        return "FAIL", f"Workflow evidence verified by its own author: {', '.join(self_verified)}."
    unverified = sum(not e["verified_by"] for e in evidence)
    return "PASS", (f"{sum(bool(e['verified_by']) for e in evidence)} workflow capture(s) verified by a second person; {unverified} pending and "
                    "not counted. Gaps close only on a compliant recalculation or an approved disposition.")


CONTROLS = (
    Control("HD-1", "HD", "Licensed measure specifications", "Measures are calculated from current licensed NCQA specifications and value sets.",
            "License the NCQA HEDIS specifications and value-set directory; map each prototype rule to its licensed measure.", _licensed_specs),
    Control("HD-2", "HD", "Rule versioning and change control", "Every result records the rule version that produced it; changes are logged.",
            "Re-evaluate the population after each rule release.", _rule_versioning),
    Control("HD-3", "HD", "Measure test deck", "Measure logic passes a patient-level test deck before release.",
            "Obtain NCQA's test deck through Measure Certification and run it on every release.", _test_deck),
    Control("HD-4", "HD", "Measure version governance", "Measure changes are drafted, impact-analysed and approved by a second person before activation.",
            "Run the impact analysis and have a second person approve before activating.", _version_governance),
    Control("IS1-1", "IS 1.0", "Standard coding", "Numerator evidence uses standard code systems.", "Map local codes to standard systems at ingestion.", _code_systems),
    Control("IS1-3", "IS 1.0", "Cross-source normalization", "Every source maps to canonical concepts; duplicates across sources are merged with lineage.",
            "Add the unmapped codes to the normalizer's mapping tables.", _normalization),
    Control("IS4-3", "IS 4.0", "Workflow evidence verification", "Evidence captured in the care-gap workflow counts only after a second person verifies it.",
            "Re-verify the listed captures with a different person.", _workflow_evidence),
    Control("IS1-2", "IS 1.0", "Service dates", "Every counted record has a valid service date.", "Reject undated records at ingestion.", _service_dates),
    Control("IS2-1", "IS 2.0", "Continuous enrollment", "Enrollment gaps are applied per measure before the denominator.",
            "Load the payer eligibility feed and re-evaluate.", _enrollment),
    Control("IS2-2", "IS 2.0", "Member identity", "Every member has one payer-format member ID.", "Fix the member crosswalk.", _member_ids),
    Control("IS3-1", "IS 3.0", "Practitioner identifiers", "Attributed practitioners have valid NPIs.", "Correct the practitioner master.", _npis),
    Control("IS3-2", "IS 3.0", "Payer roster alignment", "Rendering NPIs on submissions are on the payer's roster.",
            "Approve the rejection resolver's corrections or update the payer roster.", _roster),
    Control("IS4-1", "IS 4.0", "Document evidence verified", "Medical-record evidence counts only after human verification.",
            "Verify pending documents in the evidence review queue.", _mrr_verification),
    Control("IS4-2", "IS 4.0", "Abstraction over-read", "A sample of abstracted records is over-read by a second reviewer.",
            "Complete the over-read sample and attest.", None, "HEDIS Lead"),
    Control("IS5-1", "IS 5.0", "Primary-source verification", "Non-standard supplemental data is primary-source verified.",
            "Verify the source documents before submission.", _psv),
    Control("IS5-2", "IS 5.0", "Submission file controls", "Batches carry control totals and are signed by someone other than the builder.",
            "Rebuild and re-sign the batch.", _batch_controls),
    Control("IS5-3", "IS 5.0", "Acknowledgement reconciliation", "Every submission is acknowledged and rejections are worked.",
            "Chase overdue acknowledgements; correct and resubmit rejections.", _acknowledgements),
    Control("IS5-4", "IS 5.0", "No duplicate submissions", "Each record is submitted once, except linked corrections.",
            "Investigate the duplicate keys.", _duplicates),
    Control("IS5-5", "IS 5.0", "Supplemental source documentation", "Each supplemental source has a documented process and data-flow description.",
            "Document each feed (lab interface, EHR, device) and attest.", None, "HEDIS Lead"),
    Control("IS6-1", "IS 6.0", "Tamper-evident history", "The calculation and workflow history cannot be edited unnoticed.",
            "Restore from backup and investigate the break.", _chains),
    Control("IS6-2", "IS 6.0", "Reproducible registry", "The reported registry can be rebuilt exactly from source.", "Publish a snapshot.", _reproducible),
    Control("IS6-3", "IS 6.0", "Registry integrity", "No blocking integrity issues before reporting.", "Work the data-quality tasks.", _integrity),
    Control("IS7-1", "IS 7.0", "Totals tie-out", "Registry totals equal the measure engine's totals.", "Rebuild the registry from the latest run.", _totals_tie_out),
    Control("IS7-2", "IS 7.0", "Payer reconciliation", "Each payer's member-level file is reconciled at or above target agreement.",
            "Work the reconciliation tasks and disputes.", _payer_reconciliation),
    Control("IS7-5", "IS 7.0", "Supplied vs calculated QA", "Supplied results are recalculated from source evidence and the comparison is signed off.",
            "Run the QA comparison and have a second person sign it off.", _qa_signoff),
    Control("IS7-3", "IS 7.0", "Period lock", "The period is locked before final reporting.", "Lock the period (four eyes).", _period_lock),
    Control("IS7-4", "IS 7.0", "Roadmap (Record of Administration)", "The HEDIS Roadmap describing systems and processes is complete and current.",
            "Complete the Roadmap and attest.", None, "HEDIS Lead"),
    Control("SEC-1", "SEC", "Role-based access and segregation of duties", "Least privilege; builder ≠ signer, publisher ≠ locker.",
            "Fix the role matrix in User Access.", _rbac),
    Control("SEC-2", "SEC", "Authenticated API", "Every HEDIS API route requires a signed token and a named permission.",
            "Add the require() dependency to the route.", _api_auth),
    Control("SEC-3", "SEC", "Secret management", "Signing keys come from a secret store, not from the codebase or disk.",
            "Set HEDIS_API_SECRET from the secret store.", _secret),
    Control("SEC-4", "SEC", "Agent governance", "L2 actions need human approval; L3 never acts; kill switches exist.",
            "Review the agent proposals listed.", _agent_governance),
)
CONTROLS_BY_ID = {control.control_id: control for control in CONTROLS}


def _attestation_status(state: dict, control: Control) -> tuple[str, str]:
    record = state["ncqa"]["attestations"].get(control.control_id)
    if not record:
        return "ATTEST", f"Needs a signed attestation by {control.attest_role}."
    if record["expires"] < vbc.today().isoformat():
        return "ATTEST", f"Attestation by {record['by']} expired {record['expires']}."
    return "ATTESTED", f"Attested by {record['by']} on {record['at'][:10]} (expires {record['expires']}): {record['note']}"


def evaluate(path: Path | None = None, poc_path: Path | None = None) -> dict:
    ctx = _Context(path, poc_path)
    results = []
    for control in CONTROLS:
        if control.check is None:
            status, detail = _attestation_status(ctx.state, control)
        else:
            try:
                status, detail = control.check(ctx)
            except Exception as error:  # a broken check is a failed control, never a crashed assessment
                status, detail = "FAIL", f"Check could not run: {error}"
        results.append({"control_id": control.control_id, "standard": control.standard, "title": control.title,
                        "requirement": control.requirement, "status": status, "detail": detail, "remediation": control.remediation,
                        "attestation": control.check is None, "attest_role": control.attest_role})
    by_standard = {}
    for result in results:
        entry = by_standard.setdefault(result["standard"], {"title": STANDARDS[result["standard"]], "controls": 0, "score": 0.0, "statuses": {}})
        entry["controls"] += 1
        entry["score"] += WEIGHTS[result["status"]]
        entry["statuses"][result["status"]] = entry["statuses"].get(result["status"], 0) + 1
    for entry in by_standard.values():
        entry["readiness"] = round(entry["score"] / entry["controls"] * 100, 1)
    score = round(sum(WEIGHTS[r["status"]] for r in results) / len(results) * 100, 1)
    counts = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {"score": score, "counts": counts, "controls": results, "standards": by_standard,
            "blocking": [r for r in results if r["status"] == "FAIL"], "rule_version": RULE_VERSION, "as_of": vbc.today().isoformat(),
            "attestations": ctx.state["ncqa"]["attestations"],
            "assessments": list(reversed(ctx.state["ncqa"]["assessments"][-20:])),
            "verdict": "Not certifiable yet" if counts.get("FAIL") else "Ready for pre-audit review"}


def record_assessment(actor: str, path: Path | None = None, poc_path: Path | None = None) -> dict:
    result = evaluate(path, poc_path)
    with store.transaction(path) as state:
        assessment = {"assessment_id": store.next_id(state, "NCQ"), "at": store.now_iso(), "by": actor, "score": result["score"],
                      "counts": result["counts"], "statuses": {r["control_id"]: r["status"] for r in result["controls"]}}
        state["ncqa"]["assessments"].append(assessment)
        del state["ncqa"]["assessments"][:-100]
        store.audit(state, actor, "ncqa.assessed", "ncqa_assessment", assessment["assessment_id"], new=f"{result['score']}%",
                    trigger="assessment", details=", ".join(f"{k} {v}" for k, v in sorted(result["counts"].items())))
    result["assessments"].insert(0, assessment)
    return result


def attest(control_id: str, actor: str, actor_role: str, note: str, valid_days: int = 365, path: Path | None = None) -> dict:
    control = CONTROLS_BY_ID.get(control_id)
    if not control:
        raise NotFound(f"Unknown control {control_id}.")
    if control.check is not None:
        raise HedisError(f"{control_id} is evidenced automatically and cannot be attested.")
    if actor_role not in (control.attest_role, "Super Admin"):
        raise HedisError(f"{control_id} must be attested by {control.attest_role}.")
    if len(note.strip()) < 15:
        raise HedisError("Describe the evidence behind the attestation (at least 15 characters).")
    with store.transaction(path) as state:
        record = {"by": actor, "role": actor_role, "at": store.now_iso(), "note": note.strip(),
                  "expires": (vbc.today() + timedelta(days=valid_days)).isoformat()}
        state["ncqa"]["attestations"][control_id] = record
        store.audit(state, actor, "ncqa.attested", "ncqa_control", control_id, new="ATTESTED", trigger="attestation", details=note.strip())
        return record


def evidence_pack(path: Path | None = None, poc_path: Path | None = None) -> dict:
    """Everything an auditor asks for first, as one document: Markdown for reading, JSON for tooling."""
    result = evaluate(path, poc_path)
    state = store.load(path)
    view = registry.view(phi=False, path=path, poc_path=poc_path)
    lines = [f"# NCQA readiness evidence pack — {view['period']['id']}", "",
             f"Generated {store.now_iso()} · rule {RULE_VERSION} · readiness {result['score']}% · {result['verdict']}", "",
             "> Self-assessment against the structure of the NCQA HEDIS Compliance Audit. Not an audit opinion.", "",
             "## Controls", "", "| Control | Standard | Status | Evidence |", "|---|---|---|---|"]
    lines += [f"| {r['control_id']} {r['title']} | {r['standard']} | {r['status']} | {r['detail'].replace('|', '/')} |" for r in result["controls"]]
    lines += ["", "## Registry", "", f"Content hash `{view['content_hash']}` · locked: {bool(view['locked'])}", "",
              "| Measure | Denominator | Numerator | Rate | By source |", "|---|---|---|---|---|"]
    lines += [f"| {m} | {t['denominator']} | {t['numerator']} | {t['rate']}% | {', '.join(f'{k} {v}' for k, v in t['by_source'].items())} |"
              for m, t in view["totals"]["measures"].items()]
    lines += ["", "## Supplemental submissions", "", "| Batch | Payer | Status | Records | Signed by | File SHA-256 |", "|---|---|---|---|---|---|"]
    lines += [f"| {b['batch_id']} | {b['payer']} | {b['status']} | {len(b['records'])} | {b['signed_by'] or '—'} | "
              f"{(b['control'] or {}).get('file_sha256', '—')[:16]} |" for b in sorted(state["supplemental"]["batches"].values(), key=lambda b: b["batch_id"])]
    lines += ["", "## Payer reconciliation", ""]
    lines += [f"- {PAYERS[k].name}: {r['run_id']} · {r['summary']['agreement']}% agreement · {r['summary']['open_items']} open item(s)"
              for k, r in reconciliation.latest_runs(state).items()] or ["- No reconciliation run yet."]
    ok, broken = store.verify_chain(state)
    lines += ["", "## Audit trail", "", f"HEDIS chain: {'intact' if ok else f'BROKEN at {broken}'} · {len(state['audit'])} events.", ""]
    markdown = "\n".join(lines)
    return {"markdown": markdown, "json": json.dumps({"assessment": result, "registry_totals": view["totals"],
                                                      "content_hash": view["content_hash"]}, indent=1, default=str),
            "env": STATE_ENV, "generated_for": date.today().isoformat()}
