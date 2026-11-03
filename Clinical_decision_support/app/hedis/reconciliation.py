"""Payer reconciliation: our registry against the payer's member-level measure file.

Every (member, measure) pair is classified. Agreement needs no work; each disagreement gets a root
cause traced through the supplemental ledger, an owner role, a recommended action and its effect on
the payer-reported rate. Disputes are a separate, approval-gated workflow.

Inbound file (CSV): member_id, measure_id, payer_status (COMPLIANT | NON_COMPLIANT | EXCLUDED),
numerator_source (CLAIM | SUPPLEMENTAL | NONE), last_service_date.
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from app.hedis import registry, store, supplemental
from app.hedis.config import ADMINISTRATIVE, MEASURE_IDS, PAYERS
from app.hedis.errors import Conflict, HedisError, NotFound, SegregationOfDuties

PAYER_COLUMNS = ("member_id", "measure_id", "payer_status", "numerator_source", "last_service_date")
PAYER_STATUSES = ("COMPLIANT", "NON_COMPLIANT", "EXCLUDED")

CATEGORIES = {
    "AGREE": ("Agree", "gray", "low"),
    "INTERNAL_ONLY_COMPLIANT": ("Compliant internally, open at payer", "red", "high"),
    "PAYER_ONLY_COMPLIANT": ("Compliant at payer, open internally", "amber", "high"),
    "EXCLUSION_MISMATCH": ("Exclusion mismatch", "amber", "high"),
    "DENOMINATOR_MISMATCH": ("Denominator mismatch", "violet", "medium"),
    "NOT_ON_PAYER_FILE": ("Not attributed by payer", "violet", "medium"),
    "NOT_IN_REGISTRY": ("Payer member not in registry", "blue", "medium"),
}
ROOT_CAUSES = {
    "SUPPLEMENTAL_NOT_SUBMITTED": ("Supplemental record never submitted", "Build, sign and submit the supplemental record", "HEDIS Analyst"),
    "SUPPLEMENTAL_IN_DRAFT": ("Supplemental record in an unsigned draft", "Sign and submit the open draft batch", "HEDIS Lead"),
    "SUPPLEMENTAL_PENDING": ("Submitted, acknowledgement pending", "Await the payer acknowledgement; chase after the SLA", "HEDIS Analyst"),
    "SUPPLEMENTAL_REJECTED": ("Payer rejected the supplemental record", "Correct the record and resubmit", "HEDIS Analyst"),
    "SUPPLEMENTAL_BLOCKED": ("Supplemental record fails validation", "Fix the source data so the record validates", "HEDIS Analyst"),
    "PAYER_NOT_APPLIED": ("Payer accepted the record but still reports open", "Open a dispute citing the accepted record", "Stars / Contract"),
    "CLAIM_NOT_APPLIED": ("Our numerator is a claim the payer should hold", "Open a dispute with the claim reference", "Stars / Contract"),
    "PAYER_HAS_EVIDENCE": ("Payer holds evidence we do not", "Request the payer's supporting claim and load it as evidence", "HEDIS Analyst"),
    "EXCLUSION_NOT_SENT": ("Exclusion evidence not visible to the payer", "Submit the exclusion evidence to the payer", "HEDIS Analyst"),
    "PAYER_EXCLUSION": ("Payer applied an exclusion we have no evidence for", "Obtain the payer's exclusion evidence and review", "HEDIS Analyst"),
    "ELIGIBILITY": ("Enrollment / continuous-enrollment difference", "Reconcile the eligibility feed; correct or accept the payer denominator", "HEDIS Analyst"),
    "ATTRIBUTION": ("Payer does not attribute this member to us", "Raise an attribution dispute with roster evidence", "Stars / Contract"),
    "CROSSWALK": ("Member ID unknown to the registry", "Resolve the member crosswalk / eligibility load", "HEDIS Analyst"),
}
DISPUTABLE = ("PAYER_NOT_APPLIED", "CLAIM_NOT_APPLIED", "ATTRIBUTION", "PAYER_EXCLUSION", "ELIGIBILITY")


def _digest(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:8], 16)


def _internal_status(row: dict) -> str:
    if row["excluded"]:
        return "EXCLUDED"
    if row["numerator_met"]:
        return "COMPLIANT"
    return "NON_COMPLIANT" if row["in_denominator"] else "OUT"


def generate_payer_file(payer: str, path: Path | None = None, poc_path: Path | None = None) -> str:
    """Synthetic payer member-level file that models what the payer can see (stands in for the payer portal download).

    The payer counts administrative numerators and accepted supplemental records, knows hospice from claims, and
    differs from us in the ways real files do: one claim we lack, one unattributed member, one unknown member,
    and continuous-enrollment rules applied differently.
    """
    if payer not in PAYERS:
        raise NotFound(f"Unknown payer {payer}.")
    rows = [row for row in registry.build_rows(poc_path) if row["payer"] == payer]
    ledger = {record["registry_key"]: record["status"] for record in supplemental.candidates(payer, path, poc_path)}
    open_rows = sorted((row for row in rows if row["state"] == "OPEN_GAP"), key=lambda r: _digest("claim", r["key"]))
    in_denominator = sorted((row for row in rows if row["in_denominator"] and row["key"] not in {r["key"] for r in open_rows[:1]}),
                            key=lambda r: _digest("attr", r["key"]))
    payer_claim = {open_rows[0]["key"]} if open_rows else set()
    unattributed = {in_denominator[0]["key"]} if in_denominator else set()
    out = []
    for row in rows:
        if row["key"] in unattributed:
            continue
        status, source, when = "NON_COMPLIANT", "NONE", ""
        if row["key"] in payer_claim:
            status, source, when = "COMPLIANT", "CLAIM", "2026-09-30"
        elif row["excluded"]:
            status = "EXCLUDED" if row["exclusion_concept"] == "hospice" else "NON_COMPLIANT"
        elif row["numerator_met"]:
            if row["numerator_source"] == ADMINISTRATIVE:
                status, source, when = "COMPLIANT", "CLAIM", row["compliant_date"] or ""
            elif ledger.get(row["key"]) == "ACCEPTED":
                status, source, when = "COMPLIANT", "SUPPLEMENTAL", row["compliant_date"] or ""
        elif not row["in_denominator"] and row["state"] != "DENOMINATOR_NOT_MET":
            continue  # outside the payer's denominator too
        out.append((row["member_id"], row["measure_id"], status, source, when))
    out.append((f"{PAYERS[payer].member_prefix}999000111", "COL-P", "NON_COMPLIANT", "NONE", ""))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(PAYER_COLUMNS)
    writer.writerows(sorted(out))
    return buffer.getvalue()


def parse_payer_file(payer: str, text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [column for column in PAYER_COLUMNS if column not in (reader.fieldnames or [])]
    if missing:
        raise HedisError(f"Payer file is missing column(s): {', '.join(missing)}.")
    rows, errors, seen = [], [], set()
    prefix = PAYERS[payer].member_prefix
    for line, item in enumerate(reader, start=2):
        member, measure, status = (item["member_id"] or "").strip(), (item["measure_id"] or "").strip(), (item["payer_status"] or "").strip().upper()
        if not member.startswith(prefix):
            errors.append(f"line {line}: member {member or '—'} is not a {PAYERS[payer].name} member ID")
        if measure not in MEASURE_IDS:
            errors.append(f"line {line}: unknown measure {measure or '—'}")
        if status not in PAYER_STATUSES:
            errors.append(f"line {line}: payer_status {status or '—'} not in {', '.join(PAYER_STATUSES)}")
        if (member, measure) in seen:
            errors.append(f"line {line}: duplicate {member} / {measure}")
        seen.add((member, measure))
        rows.append({"member_id": member, "measure_id": measure, "payer_status": status,
                     "numerator_source": (item["numerator_source"] or "NONE").strip().upper(),
                     "last_service_date": (item["last_service_date"] or "").strip() or None})
    if errors:
        raise HedisError(f"Payer file rejected ({len(errors)} error(s)): " + "; ".join(errors[:5]))
    return rows


def _supplemental_cause(record: dict | None) -> str:
    if record is None:
        return "SUPPLEMENTAL_NOT_SUBMITTED"
    if record["errors"]:
        return "SUPPLEMENTAL_BLOCKED"
    return {"IN_DRAFT": "SUPPLEMENTAL_IN_DRAFT", "PENDING_ACK": "SUPPLEMENTAL_PENDING", "ACCEPTED": "PAYER_NOT_APPLIED",
            "NEEDS_CORRECTION": "SUPPLEMENTAL_REJECTED", "READY_RESUBMIT": "SUPPLEMENTAL_REJECTED"}.get(record["status"], "SUPPLEMENTAL_NOT_SUBMITTED")


def _item(category: str, cause: str | None, key: str, row: dict | None, payer_row: dict | None, record: dict | None = None) -> dict:
    label, tone, priority = CATEGORIES[category]
    cause_label, action, owner = ROOT_CAUSES[cause] if cause else ("Both sides agree", "No action", "")
    if cause == "SUPPLEMENTAL_REJECTED" and record and record.get("reason"):
        action = f"Correct and resubmit — payer reason {record['reason_code']}: {record['reason']}"
    return {"item_id": key, "category": category, "category_label": label, "tone": tone, "priority": priority,
            "root_cause": cause, "root_cause_label": cause_label, "action": action, "owner_role": owner,
            "member_id": (row or payer_row)["member_id"], "measure_id": (row or payer_row)["measure_id"],
            "internal_state": row["state"] if row else None, "internal_status": _internal_status(row) if row else None,
            "payer_status": payer_row["payer_status"] if payer_row else None,
            "payer_source": payer_row["numerator_source"] if payer_row else None,
            "record_key": record["record_key"] if record else None, "disputable": cause in DISPUTABLE,
            "registry_key": row["key"] if row else None}


def classify(payer: str, payer_rows: list[dict], rows: list[dict], records: list[dict]) -> list[dict]:
    internal = {(row["member_id"], row["measure_id"]): row for row in rows if row["payer"] == payer}
    external = {(item["member_id"], item["measure_id"]): item for item in payer_rows}
    by_registry = {record["registry_key"]: record for record in records}
    items = []
    for key in sorted(set(internal) | set(external)):
        row, theirs = internal.get(key), external.get(key)
        item_key = f"{key[0]}|{key[1]}"
        if row is None:
            items.append(_item("NOT_IN_REGISTRY", "CROSSWALK", item_key, None, theirs))
            continue
        ours = _internal_status(row)
        if theirs is None:
            if row["in_denominator"]:
                items.append(_item("NOT_ON_PAYER_FILE", "ATTRIBUTION", item_key, row, None))
            continue
        status = theirs["payer_status"]
        if ours == "OUT":
            items.append(_item("DENOMINATOR_MISMATCH", "ELIGIBILITY", item_key, row, theirs))
        elif ours == status:
            items.append(_item("AGREE", None, item_key, row, theirs))
        elif ours == "COMPLIANT" and status == "NON_COMPLIANT":
            record = by_registry.get(row["key"])
            cause = "CLAIM_NOT_APPLIED" if row["numerator_source"] == ADMINISTRATIVE else _supplemental_cause(record)
            items.append(_item("INTERNAL_ONLY_COMPLIANT", cause, item_key, row, theirs, record))
        elif status == "COMPLIANT":
            items.append(_item("PAYER_ONLY_COMPLIANT", "PAYER_HAS_EVIDENCE", item_key, row, theirs))
        elif ours == "EXCLUDED":
            items.append(_item("EXCLUSION_MISMATCH", "EXCLUSION_NOT_SENT", item_key, row, theirs))
        else:
            items.append(_item("EXCLUSION_MISMATCH", "PAYER_EXCLUSION", item_key, row, theirs))
    return items


def summarize(items: list[dict], rows: list[dict], payer: str) -> dict:
    measures = {}
    for measure_id in MEASURE_IDS:
        mine = [row for row in rows if row["payer"] == payer and row["measure_id"] == measure_id]
        compared = [item for item in items if item["measure_id"] == measure_id]
        theirs = [item for item in compared if item["payer_status"] in ("COMPLIANT", "NON_COMPLIANT")]
        payer_num = sum(item["payer_status"] == "COMPLIANT" for item in theirs)
        recoverable = sum(item["category"] == "INTERNAL_ONLY_COMPLIANT" for item in compared)
        internal_den = sum(row["in_denominator"] for row in mine)
        internal_num = sum(row["numerator_met"] for row in mine)
        measures[measure_id] = {
            "internal": {"numerator": internal_num, "denominator": internal_den,
                         "rate": round(internal_num / internal_den * 100, 1) if internal_den else None},
            "payer": {"numerator": payer_num, "denominator": len(theirs), "rate": round(payer_num / len(theirs) * 100, 1) if theirs else None},
            "recoverable": recoverable,
            "payer_rate_if_resolved": round((payer_num + recoverable) / len(theirs) * 100, 1) if theirs else None,
            "agreement": round(sum(item["category"] == "AGREE" for item in compared) / len(compared) * 100, 1) if compared else None,
        }
    counts = {}
    for item in items:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    total = len(items)
    return {"measures": measures, "counts": counts, "compared": total,
            "agreement": round(counts.get("AGREE", 0) / total * 100, 1) if total else None,
            "open_items": total - counts.get("AGREE", 0)}


def reconcile(payer: str, text: str, actor: str, source_name: str = "payer upload", path: Path | None = None,
              poc_path: Path | None = None) -> dict:
    if payer not in PAYERS:
        raise NotFound(f"Unknown payer {payer}.")
    payer_rows = parse_payer_file(payer, text)
    rows = registry.build_rows(poc_path)
    records = supplemental.candidates(payer, path, poc_path)
    items = classify(payer, payer_rows, rows, records)
    summary = summarize(items, rows, payer)
    with store.transaction(path) as state:
        run_id = store.next_id(state, "REC")
        run = {"run_id": run_id, "payer": payer, "at": store.now_iso(), "by": actor, "source": source_name,
               "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "payer_rows": len(payer_rows),
               "registry_hash": registry.content_hash(rows), "summary": summary, "items": items}
        state["reconciliation"]["runs"][run_id] = run
        for old in sorted(state["reconciliation"]["runs"])[:-30]:
            state["reconciliation"]["runs"].pop(old)
        store.audit(state, actor, "reconciliation.run", "reconciliation_run", run_id, new=f"{summary['agreement']}% agree", trigger="reconcile",
                    details=f"{PAYERS[payer].name}: {summary['compared']} compared · {summary['open_items']} open item(s) · file {run['file_sha256'][:12]}")
        return run


def _run(state: dict, run_id: str) -> dict:
    run = state["reconciliation"]["runs"].get(run_id)
    if not run:
        raise NotFound(f"Reconciliation run {run_id} not found.")
    return run


def open_dispute(run_id: str, item_id: str, actor: str, note: str, path: Path | None = None) -> dict:
    if actor.startswith("agent:"):
        raise SegregationOfDuties("An agent can propose a dispute; a person must open it.")
    if not note.strip():
        raise HedisError("A dispute needs a written basis.")
    with store.transaction(path) as state:
        run = _run(state, run_id)
        item = next((i for i in run["items"] if i["item_id"] == item_id), None)
        if not item:
            raise NotFound(f"Item {item_id} is not in {run_id}.")
        if not item["disputable"]:
            raise Conflict(f"{item['root_cause_label']} is resolved by an internal action, not a payer dispute.")
        if any(d["item_id"] == item_id and d["payer"] == run["payer"] and d["status"] == "OPEN" for d in state["reconciliation"]["disputes"].values()):
            raise Conflict("A dispute is already open for this member and measure.")
        dispute_id = store.next_id(state, "DSP")
        dispute = {"dispute_id": dispute_id, "run_id": run_id, "payer": run["payer"], "item_id": item_id, "member_id": item["member_id"],
                   "measure_id": item["measure_id"], "root_cause": item["root_cause"], "note": note.strip(), "status": "OPEN",
                   "opened_by": actor, "opened_at": store.now_iso(), "resolution": None}
        state["reconciliation"]["disputes"][dispute_id] = dispute
        store.audit(state, actor, "reconciliation.dispute_opened", "dispute", dispute_id, new="OPEN", trigger="dispute",
                    details=f"{PAYERS[run['payer']].name} · {item['member_id']} · {item['measure_id']} · {item['root_cause_label']}")
        return dispute


def resolve_dispute(dispute_id: str, actor: str, outcome: str, note: str = "", path: Path | None = None) -> dict:
    if outcome not in ("UPHELD", "DENIED", "WITHDRAWN"):
        raise HedisError("Outcome must be UPHELD, DENIED or WITHDRAWN.")
    with store.transaction(path) as state:
        dispute = state["reconciliation"]["disputes"].get(dispute_id)
        if not dispute:
            raise NotFound(f"Dispute {dispute_id} not found.")
        if dispute["status"] != "OPEN":
            raise Conflict(f"{dispute_id} is already {dispute['status']}.")
        dispute.update(status=outcome, resolution={"by": actor, "at": store.now_iso(), "note": note})
        store.audit(state, actor, "reconciliation.dispute_resolved", "dispute", dispute_id, previous="OPEN", new=outcome,
                    trigger="resolve", details=note)
        return dispute


def create_task(run_id: str, item_id: str, actor: str, path: Path | None = None) -> dict:
    """Route an internal action (submit, correct, request evidence) to the owner role's queue."""
    with store.transaction(path) as state:
        run = _run(state, run_id)
        item = next((i for i in run["items"] if i["item_id"] == item_id), None)
        if not item or item["category"] == "AGREE":
            raise HedisError("Only an open reconciliation item can become a task.")
        task_key = f"{run['payer']}|{item_id}|{item['root_cause']}"
        existing = next((t for t in state["reconciliation"]["tasks"].values() if t["key"] == task_key and t["status"] == "OPEN"), None)
        if existing:
            return existing
        task_id = store.next_id(state, "RTK")
        task = {"task_id": task_id, "key": task_key, "run_id": run_id, "payer": run["payer"], "item_id": item_id, "member_id": item["member_id"],
                "measure_id": item["measure_id"], "action": item["action"], "owner_role": item["owner_role"], "status": "OPEN",
                "created_by": actor, "created_at": store.now_iso()}
        state["reconciliation"]["tasks"][task_id] = task
        store.audit(state, actor, "reconciliation.task_created", "reconciliation_task", task_id, new="OPEN", trigger="route",
                    details=f"{item['member_id']} · {item['measure_id']} → {item['owner_role']}: {item['action']}")
        return task


def latest_runs(state: dict) -> dict:
    latest = {}
    for run in state["reconciliation"]["runs"].values():
        if run["payer"] not in latest or run["run_id"] > latest[run["payer"]]["run_id"]:
            latest[run["payer"]] = run
    return latest


def view(path: Path | None = None) -> dict:
    state = store.load(path)
    runs = sorted(state["reconciliation"]["runs"].values(), key=lambda r: r["run_id"], reverse=True)
    return {"latest": latest_runs(state), "runs": [{k: v for k, v in run.items() if k != "items"} for run in runs],
            "disputes": sorted(state["reconciliation"]["disputes"].values(), key=lambda d: d["dispute_id"], reverse=True),
            "tasks": sorted(state["reconciliation"]["tasks"].values(), key=lambda t: t["task_id"], reverse=True),
            "categories": CATEGORIES, "root_causes": ROOT_CAUSES}
