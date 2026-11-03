"""Supplemental data submission engine (NCQA IS 5.0).

Numerators supported only by data the payer cannot see in its claims (labs, EHR results, device
readings, verified outside reports) must reach the payer as supplemental data. The engine:

  1. derives one record per compliant registry row whose numerator source is supplemental;
  2. validates it against the payer's file spec (member ID, service date window, code system, result
     values, NPI check digit, primary-source verification, payer roster);
  3. assigns an idempotency key so the same evidence is never in flight twice;
  4. runs the batch lifecycle  DRAFT -> SIGNED -> SUBMITTED -> ACKNOWLEDGED | PARTIAL | REJECTED,
     with signing by someone other than the builder (agents can build drafts, never sign);
  5. writes the payer file with control totals, ingests the acknowledgement, and queues corrected
     rejections for resubmission.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, timedelta
from pathlib import Path

from app.hedis import registry, store
from app.hedis.config import (
    NON_STANDARD_SUPPLEMENTAL,
    PAYERS,
    SOURCE_CLASS_LABELS,
    SUPPLEMENTAL_CLASSES,
    VALUE_REQUIRED,
    is_valid_npi,
    outbox_dir,
)
from app.hedis.errors import Conflict, HedisError, NotFound, SegregationOfDuties
from app.vbc_poc import service as vbc

IN_FLIGHT = ("IN_DRAFT", "PENDING_ACK")
ACK_REASONS = {
    "R02": "Member not eligible on the date of service",
    "R04": "Code not accepted for this measure",
    "R11": "Rendering provider NPI not on the payer roster",
    "R15": "Duplicate of a record already accepted",
}
CSV_COLUMNS = ("record_key", "member_id", "measure_id", "service_date", "code_system", "code", "value", "value2", "unit",
               "rendering_npi", "source_class", "source_record_id")


def record_key(payer: str, row: dict, evidence: dict) -> str:
    parts = (payer, row["member_id"], row["measure_id"], evidence["code_system"], evidence["code"], evidence["date"] or "",
             str(evidence["value"]), str(evidence["value2"]))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _supporting_evidence(row: dict) -> dict | None:
    """The record that carries the numerator for the payer: same class as the row's numerator source, latest date."""
    candidates = [item for item in row["evidence"] if item["source_class"] == row["numerator_source"]]
    return max(candidates, key=lambda item: item["date"] or "") if candidates else None


def _record(row: dict, evidence: dict, overrides: dict) -> dict:
    key = record_key(row["payer"], row, evidence)
    override = overrides.get(key, {})
    return {
        "record_key": key, "payer": row["payer"], "registry_key": row["key"], "member_id": row["member_id"], "patient_id": row["patient_id"],
        "measure_id": row["measure_id"], "service_date": evidence["date"], "code_system": evidence["code_system"], "code": evidence["code"],
        "value": evidence["value"], "value2": evidence["value2"], "unit": evidence["unit"],
        "rendering_npi": override.get("rendering_npi") or row["provider_npi"], "provider": row["provider"],
        "source": evidence["source"], "source_class": evidence["source_class"], "source_record_id": evidence["source_record_id"],
        "evidence_id": evidence["event_id"], "concept": evidence["concept"],
        "psv": "VERIFIED" if evidence["verification"] == "verified" else "PENDING", "override": override or None,
    }


def validate(record: dict, as_of: date | None = None) -> tuple[list[dict], list[dict]]:
    """Payer file-spec rules. Errors block the record; warnings predict a likely payer rejection."""
    payer = PAYERS[record["payer"]]
    as_of = as_of or vbc.today()
    errors, warnings = [], []
    if not re.fullmatch(rf"{payer.member_prefix}\d{{9}}", record["member_id"] or ""):
        errors.append({"code": "E-MEMBER", "detail": f"Member ID does not match the {payer.name} format"})
    if not record["service_date"]:
        errors.append({"code": "E-DOS", "detail": "No date of service"})
    else:
        start, end = registry.measure_window(record["measure_id"], record["concept"])
        service = date.fromisoformat(record["service_date"])
        if not start <= service <= min(end, as_of):
            errors.append({"code": "E-DOS", "detail": f"Date of service {service} outside {start} – {min(end, as_of)}"})
    if record["code_system"] not in payer.allowed_code_systems:
        errors.append({"code": "E-CODESYS", "detail": f"{record['code_system']} is not accepted by {payer.name}"})
    for field in VALUE_REQUIRED.get(record["measure_id"], ()):
        if record[field] is None:
            errors.append({"code": "E-VALUE", "detail": f"{record['measure_id']} requires a result value ({field})"})
    if not is_valid_npi(record["rendering_npi"]):
        errors.append({"code": "E-NPI", "detail": f"Rendering NPI {record['rendering_npi'] or '—'} fails the check digit"})
    if record["source_class"] == NON_STANDARD_SUPPLEMENTAL and record["psv"] != "VERIFIED":
        errors.append({"code": "E-PSV", "detail": "Non-standard supplemental data needs primary-source verification"})
    if record["rendering_npi"] not in payer.roster_npis:
        warnings.append({"code": "W-ROSTER", "detail": f"Rendering NPI {record['rendering_npi']} is not on the {payer.name} roster"})
    return errors, warnings


def _ledger_status(state: dict, record: dict) -> str:
    entry = state["supplemental"]["ledger"].get(record["record_key"])
    if not entry:
        return "READY"
    if entry["status"] == "REJECTED":
        override = state["supplemental"]["overrides"].get(record["record_key"])
        return "READY_RESUBMIT" if override and override.get("corrects_batch") == entry["batch_id"] else "NEEDS_CORRECTION"
    return entry["status"]


def candidates(payer: str | None = None, path: Path | None = None, poc_path: Path | None = None) -> list[dict]:
    """Every supplemental record the registry implies, with validation and ledger status."""
    state = store.load(path)
    out = []
    for row in registry.build_rows(poc_path):
        if not row["numerator_met"] or row["numerator_source"] not in SUPPLEMENTAL_CLASSES:
            continue
        if payer and row["payer"] != payer:
            continue
        evidence = _supporting_evidence(row)
        if not evidence:
            continue
        record = _record(row, evidence, state["supplemental"]["overrides"])
        errors, warnings = validate(record)
        status = _ledger_status(state, record)
        entry = state["supplemental"]["ledger"].get(record["record_key"], {})
        out.append({**record, "errors": errors, "warnings": warnings, "status": status, "batch_id": entry.get("batch_id"),
                    "reason_code": entry.get("reason_code"), "reason": entry.get("reason"),
                    "submittable": not errors and status in ("READY", "READY_RESUBMIT")})
    return out


def _set_ledger(state: dict, record: dict, status: str, batch_id: str, actor: str, reason_code: str | None = None, reason: str | None = None):
    ledger = state["supplemental"]["ledger"]
    entry = ledger.setdefault(record["record_key"], {"history": [], "submissions": 0, "registry_key": record["registry_key"],
                                                      "payer": record["payer"], "measure_id": record["measure_id"],
                                                      "member_id": record["member_id"]})
    entry.update(status=status, batch_id=batch_id, reason_code=reason_code, reason=reason, updated_at=store.now_iso())
    if status == "PENDING_ACK":
        entry["submissions"] += 1
    entry["history"].append({"at": entry["updated_at"], "status": status, "batch_id": batch_id, "by": actor, "reason_code": reason_code})
    del entry["history"][:-20]


def build_batch(payer: str, actor: str, path: Path | None = None, poc_path: Path | None = None) -> dict:
    if payer not in PAYERS:
        raise NotFound(f"Unknown payer {payer}.")
    open_draft = next((b for b in store.load(path)["supplemental"]["batches"].values() if b["payer"] == payer and b["status"] == "DRAFT"), None)
    if open_draft:
        raise Conflict(f"{open_draft['batch_id']} is already an open draft for {PAYERS[payer].name}; sign or discard it first.")
    ready = [record for record in candidates(payer, path, poc_path) if record["submittable"]]
    if not ready:
        raise HedisError(f"No validated supplemental records are ready for {PAYERS[payer].name}.")
    with store.transaction(path) as state:
        if state["registry"]["locked"]:
            raise Conflict("The period is locked; supplemental submissions are closed for this period.")
        open_draft = next((b for b in state["supplemental"]["batches"].values() if b["payer"] == payer and b["status"] == "DRAFT"), None)
        if open_draft:
            raise Conflict(f"{open_draft['batch_id']} is already an open draft for {PAYERS[payer].name}; sign or discard it first.")
        ready = [record for record in ready if _ledger_status(state, record) in ("READY", "READY_RESUBMIT")]
        ready = ready[: PAYERS[payer].max_records_per_batch]
        batch_id = store.next_id(state, "SUP")
        records = [{key: value for key, value in record.items() if key not in ("errors", "submittable", "status", "batch_id",
                                                                                 "reason_code", "reason")}
                   for record in ready]
        for record in records:
            prior = state["supplemental"]["ledger"].get(record["record_key"])
            record["resubmission_of"] = prior["batch_id"] if prior and prior["status"] == "REJECTED" else None
            _set_ledger(state, record, "IN_DRAFT", batch_id, actor)
        batch = {"batch_id": batch_id, "payer": payer, "format": PAYERS[payer].supplemental_format, "status": "DRAFT",
                 "built_by": actor, "built_at": store.now_iso(), "records": records, "signed_by": None, "signed_at": None,
                 "signature": None, "submitted_by": None, "submitted_at": None, "file": None, "control": None, "ack": None,
                 "resubmissions": sum(bool(r["resubmission_of"]) for r in records)}
        state["supplemental"]["batches"][batch_id] = batch
        store.audit(state, actor, "supplemental.batch_built", "supplemental_batch", batch_id, new="DRAFT", trigger="build",
                    evidence_ids=[r["evidence_id"] for r in records],
                    details=f"{PAYERS[payer].name}: {len(records)} record(s), {batch['resubmissions']} resubmission(s)")
        return batch


def _batch(state: dict, batch_id: str) -> dict:
    batch = state["supplemental"]["batches"].get(batch_id)
    if not batch:
        raise NotFound(f"Supplemental batch {batch_id} not found.")
    return batch


def _payload_digest(batch: dict) -> str:
    return hashlib.sha256("\n".join(sorted(r["record_key"] for r in batch["records"])).encode("utf-8")).hexdigest()


def discard_batch(batch_id: str, actor: str, reason: str = "", path: Path | None = None) -> dict:
    with store.transaction(path) as state:
        batch = _batch(state, batch_id)
        if batch["status"] != "DRAFT":
            raise Conflict(f"Only a draft can be discarded; {batch_id} is {batch['status']}.")
        batch["status"] = "DISCARDED"
        for record in batch["records"]:
            state["supplemental"]["ledger"].pop(record["record_key"], None) if not record.get("resubmission_of") else \
                _set_ledger(state, record, "REJECTED", record["resubmission_of"], actor, "DISCARDED", "Resubmission draft discarded")
        store.audit(state, actor, "supplemental.batch_discarded", "supplemental_batch", batch_id, previous="DRAFT", new="DISCARDED",
                    trigger="discard", details=reason)
        return batch


def sign_batch(batch_id: str, actor: str, path: Path | None = None) -> dict:
    """Authorized signatory attests the batch. Never the builder, never an agent."""
    with store.transaction(path) as state:
        batch = _batch(state, batch_id)
        if batch["status"] != "DRAFT":
            raise Conflict(f"{batch_id} is {batch['status']}; only a draft can be signed.")
        if actor.startswith("agent:"):
            raise SegregationOfDuties("An agent cannot sign a payer submission.")
        if actor == batch["built_by"]:
            raise SegregationOfDuties("The person who built the batch cannot also sign it.")
        batch.update(status="SIGNED", signed_by=actor, signed_at=store.now_iso(), signature=_payload_digest(batch))
        store.audit(state, actor, "supplemental.batch_signed", "supplemental_batch", batch_id, previous="DRAFT", new="SIGNED",
                    trigger="sign", details=f"Signature {batch['signature'][:16]} over {len(batch['records'])} record(s)")
        return batch


def _fmt_value(value) -> str:
    return "" if value is None else (f"{value:g}" if isinstance(value, float) else str(value))


def render_file(batch: dict) -> tuple[str, dict]:
    """Payer file text plus control totals (record count and SHA-256 over the detail lines)."""
    payer = PAYERS[batch["payer"]]
    created = (batch["signed_at"] or batch["built_at"])[:10].replace("-", "")
    if payer.supplemental_format == "PIPE-v2":
        details = ["|".join(("DTL", str(index), r["member_id"], r["measure_id"], (r["service_date"] or "").replace("-", ""),
                             r["code_system"], r["code"], _fmt_value(r["value"]), _fmt_value(r["value2"]), r["unit"] or "",
                             r["rendering_npi"] or "", r["source_class"], r["record_key"]))
                   for index, r in enumerate(batch["records"], start=1)]
        digest = hashlib.sha256("\n".join(details).encode("utf-8")).hexdigest()
        lines = ["|".join(("HDR", payer.key, "SUPP", batch["batch_id"], created, payer.submitter_id, payer.contract_id))]
        lines += details + ["|".join(("TRL", str(len(details)), digest))]
        text = "\n".join(lines) + "\n"
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("#batch", batch["batch_id"], payer.submitter_id, payer.contract_id, created))
        writer.writerow(CSV_COLUMNS)
        detail_rows = [[_fmt_value(r[col]) if col in ("value", "value2") else (r[col] or "") for col in CSV_COLUMNS] for r in batch["records"]]
        writer.writerows(detail_rows)
        digest = hashlib.sha256("\n".join(",".join(row) for row in detail_rows).encode("utf-8")).hexdigest()
        writer.writerow(("#trailer", len(detail_rows), digest))
        text = buffer.getvalue()
    return text, {"records": len(batch["records"]), "detail_sha256": digest, "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def submit_batch(batch_id: str, actor: str, path: Path | None = None) -> dict:
    with store.transaction(path) as state:
        batch = _batch(state, batch_id)
        if batch["status"] != "SIGNED":
            raise Conflict(f"{batch_id} is {batch['status']}; sign it before submitting.")
        if actor.startswith("agent:"):
            raise SegregationOfDuties("An agent cannot transmit a payer submission.")
        if _payload_digest(batch) != batch["signature"]:
            raise Conflict(f"{batch_id} changed after it was signed; rebuild and sign again.")
        text, control = render_file(batch)
        extension = "txt" if batch["format"] == "PIPE-v2" else "csv"
        target = outbox_dir() / batch["payer"] / f"{batch_id}.{extension}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        batch.update(status="SUBMITTED", submitted_by=actor, submitted_at=store.now_iso(), file=str(target), control=control)
        for record in batch["records"]:
            _set_ledger(state, record, "PENDING_ACK", batch_id, actor)
        store.audit(state, actor, "supplemental.batch_submitted", "supplemental_batch", batch_id, previous="SIGNED", new="SUBMITTED",
                    trigger="submit", details=f"{control['records']} record(s) · file {control['file_sha256'][:16]} → {target.name}")
        return batch


def simulate_ack(batch_id: str, path: Path | None = None) -> str:
    """Stand-in for the payer's response file: roster rule enforced, everything else accepted."""
    batch = _batch(store.load(path), batch_id)
    if batch["status"] != "SUBMITTED":
        raise Conflict(f"{batch_id} has not been submitted or was already acknowledged.")
    payer = PAYERS[batch["payer"]]
    results = [(r["record_key"], "R", "R11") if r["rendering_npi"] not in payer.roster_npis else (r["record_key"], "A", "")
               for r in batch["records"]]
    if payer.supplemental_format == "PIPE-v2":
        return "\n".join("|".join(("ACK", batch_id, key, status, code, ACK_REASONS.get(code, ""))) for key, status, code in results) + "\n"
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("batch_id", "record_key", "status", "reason_code", "reason"))
    writer.writerows((batch_id, key, "ACCEPTED" if status == "A" else "REJECTED", code, ACK_REASONS.get(code, "")) for key, status, code in results)
    return buffer.getvalue()


def parse_ack(payer: str, text: str) -> list[dict]:
    rows = []
    if PAYERS[payer].supplemental_format == "PIPE-v2":
        for line in text.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 4 or parts[0] != "ACK":
                continue
            rows.append({"batch_id": parts[1], "record_key": parts[2], "accepted": parts[3] == "A",
                         "reason_code": parts[4] if len(parts) > 4 else "", "reason": parts[5] if len(parts) > 5 else ""})
    else:
        for item in csv.DictReader(io.StringIO(text)):
            rows.append({"batch_id": item.get("batch_id", ""), "record_key": item.get("record_key", ""),
                         "accepted": (item.get("status") or "").upper() == "ACCEPTED", "reason_code": item.get("reason_code") or "",
                         "reason": item.get("reason") or ""})
    if not rows:
        raise HedisError("The acknowledgement file has no recognizable detail rows.")
    return rows


def ingest_ack(batch_id: str, text: str, actor: str, path: Path | None = None) -> dict:
    with store.transaction(path) as state:
        batch = _batch(state, batch_id)
        if batch["status"] != "SUBMITTED":
            raise Conflict(f"{batch_id} is {batch['status']}; acknowledgements apply only to submitted batches.")
        rows = parse_ack(batch["payer"], text)
        by_key = {record["record_key"]: record for record in batch["records"]}
        unknown = [row["record_key"] for row in rows if row["batch_id"] != batch_id or row["record_key"] not in by_key]
        if unknown:
            raise HedisError(f"Acknowledgement references {len(unknown)} record(s) not in {batch_id}.")
        accepted = rejected = 0
        for row in rows:
            record = by_key[row["record_key"]]
            if row["accepted"]:
                accepted += 1
                _set_ledger(state, record, "ACCEPTED", batch_id, actor)
            else:
                rejected += 1
                _set_ledger(state, record, "REJECTED", batch_id, actor, row["reason_code"],
                            row["reason"] or ACK_REASONS.get(row["reason_code"], "Rejected by payer"))
        missing = len(by_key) - len(rows)
        status = "ACKNOWLEDGED" if rejected == 0 and missing == 0 else "REJECTED" if accepted == 0 else "PARTIAL"
        batch.update(status=status, ack={"at": store.now_iso(), "by": actor, "accepted": accepted, "rejected": rejected, "missing": missing,
                                         "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
        store.audit(state, actor, "supplemental.ack_ingested", "supplemental_batch", batch_id, previous="SUBMITTED", new=status,
                    trigger="ack", details=f"{accepted} accepted · {rejected} rejected · {missing} without a response")
        return batch


def apply_correction(record_key_: str, field: str, value: str, actor: str, reason: str, path: Path | None = None) -> dict:
    """Correct a rejected record; the correction is what makes it eligible for resubmission."""
    if field not in ("rendering_npi",):
        raise HedisError(f"{field} cannot be corrected here; fix the source record and re-evaluate.")
    if field == "rendering_npi" and not is_valid_npi(value):
        raise HedisError(f"{value} is not a valid NPI.")
    with store.transaction(path) as state:
        entry = state["supplemental"]["ledger"].get(record_key_)
        if not entry or entry["status"] != "REJECTED":
            raise Conflict("Only a payer-rejected record can be corrected.")
        state["supplemental"]["overrides"][record_key_] = {field: value, "by": actor, "at": store.now_iso(), "reason": reason,
                                                           "rejected_for": entry["reason_code"], "corrects_batch": entry["batch_id"]}
        store.audit(state, actor, "supplemental.record_corrected", "supplemental_record", record_key_, previous=entry["reason_code"],
                    new="READY_RESUBMIT", trigger="correction", details=f"{field} → {value}: {reason}")
        return state["supplemental"]["overrides"][record_key_]


def overdue_acks(state: dict, as_of: date | None = None) -> list[dict]:
    as_of = as_of or vbc.today()
    late = []
    for batch in state["supplemental"]["batches"].values():
        if batch["status"] == "SUBMITTED":
            due = date.fromisoformat(batch["submitted_at"][:10]) + timedelta(days=PAYERS[batch["payer"]].ack_sla_days)
            if as_of > due:
                late.append({"batch_id": batch["batch_id"], "payer": batch["payer"], "due": due.isoformat()})
    return late


def view(path: Path | None = None, poc_path: Path | None = None) -> dict:
    state = store.load(path)
    records = candidates(None, path, poc_path)
    payers = {}
    for key, payer in PAYERS.items():
        mine = [r for r in records if r["payer"] == key]
        payers[key] = {"name": payer.name, "format": payer.supplemental_format, "product": payer.product,
                       "ready": sum(r["submittable"] for r in mine), "blocked": sum(bool(r["errors"]) for r in mine),
                       "warnings": sum(bool(r["warnings"]) for r in mine),
                       **{status.lower(): sum(r["status"] == status for r in mine)
                          for status in ("IN_DRAFT", "PENDING_ACK", "ACCEPTED", "NEEDS_CORRECTION", "READY_RESUBMIT")}}
    batches = sorted(state["supplemental"]["batches"].values(), key=lambda b: b["batch_id"], reverse=True)
    return {"payers": payers, "records": records, "batches": batches, "overdue_acks": overdue_acks(state),
            "source_classes": SOURCE_CLASS_LABELS, "ack_reasons": ACK_REASONS,
            "accepted_rate": round(sum(r["status"] == "ACCEPTED" for r in records) / len(records) * 100, 1) if records else None}
