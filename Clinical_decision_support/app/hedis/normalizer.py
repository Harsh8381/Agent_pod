"""Cross-source evidence normalization (Evidence & Quality workstream).

Six source feeds — observations, procedures, claims, conditions, encounters, documents — plus evidence
captured and verified in the care-gap workflow, become one canonical evidence set:

  1. Map      each source code to a canonical concept and standard code (LOINC / CPT / ICD-10-CM).
  2. Parse    values into canonical form: "132/84" -> systolic 132, diastolic 84; HbA1c mmol/mol -> %;
              qualitative FIT -> positive / negative.
  3. Check    dates (missing, future, outside the history window), plausibility, completion status,
              denied claims; each problem is a flag, never a silent drop.
  4. Merge    records of the same event from different sources (lab result + procedure + claim + report)
              into one golden record that keeps the lineage of every contributing row and inherits
              the report's verification status.
  5. Report   per-source mapping rate, unmapped codes, flags and merges, so normalization is auditable.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date

from app.hedis import sources
from app.hedis.config import ADMINISTRATIVE, NON_STANDARD_SUPPLEMENTAL, STANDARD_SUPPLEMENTAL

# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------
OBSERVATION_MAP = {
    "HbA1c": ("hba1c", "LOINC", "4548-4"),
    "Blood Pressure": ("bp", "LOINC", "85354-9"),
    "FIT Result": ("fit_result", "LOINC", "29771-3"),
    "eGFR": ("egfr", "LOINC", "98979-8"),
    "uACR": ("uacr", "LOINC", "9318-7"),
}
PROCEDURE_MAP = {
    "83036": ("hba1c_test", "CPT"), "83037": ("hba1c_test", "CPT"),
    "82274": ("fit_test", "CPT"), "G0328": ("fit_test", "HCPCS"),
    "77067": ("mammogram", "CPT"), "77066": ("mammogram", "CPT"), "77065": ("mammogram", "CPT"),
    "45378": ("colonoscopy", "CPT"), "45380": ("colonoscopy", "CPT"), "45385": ("colonoscopy", "CPT"),
    "82565": ("egfr", "CPT"), "82043": ("uacr", "CPT"), "82570": ("uacr", "CPT"),
}
CLAIM_MAP = {
    **{code: ("outpatient_visit", "CPT") for code in ("99202", "99203", "99204", "99205", "99211", "99212", "99213", "99214", "99215")},
    "G0438": ("outpatient_visit", "HCPCS"), "G0439": ("outpatient_visit", "HCPCS"),
    "99221": ("inpatient_stay", "CPT"), "99222": ("inpatient_stay", "CPT"), "99223": ("inpatient_stay", "CPT"),
    "99281": ("ed_visit", "CPT"), "99282": ("ed_visit", "CPT"), "99283": ("ed_visit", "CPT"), "99284": ("ed_visit", "CPT"),
    "99285": ("ed_visit", "CPT"),
    **{code: (concept, system) for code, (concept, system) in PROCEDURE_MAP.items()},
}
CONDITION_PREFIX_MAP = {"E10": "diabetes", "E11": "diabetes", "E13": "diabetes", "I10": "hypertension", "E78": "hyperlipidemia",
                        "N18": "ckd", "Z51.5": "palliative_care", "Z90.13": "bilateral_mastectomy", "C18": "colorectal_cancer"}
# Reason-for-encounter Z codes are recognised but are never numerator evidence.
CONDITION_RECOGNISED = ("Z00", "Z12")
ENCOUNTER_MAP = {
    "PCP visit": "outpatient_visit", "Annual wellness visit": "outpatient_visit", "Diabetes follow-up": "outpatient_visit",
    "Specialist consultation": "outpatient_visit", "Preventive screening visit": "outpatient_visit", "Telehealth visit": "outpatient_visit",
    "ED visit": "ed_visit", "Inpatient admission": "inpatient_stay",
}
DOCUMENT_MAP = {
    "HbA1c Lab Report": "hba1c_report", "Mammogram Report": "mammogram_report", "Discharge Summary": "discharge_summary",
    "ED Summary": "ed_summary", "Colonoscopy Report": "colonoscopy_report", "SOAP Note": "visit_note",
}
# Concepts that describe the same clinical event; records in one family on one date merge into a golden record.
FAMILY = {
    "hba1c": "hba1c", "hba1c_test": "hba1c", "hba1c_report": "hba1c",
    "fit_result": "fit", "fit_test": "fit",
    "mammogram": "mammogram", "mammogram_report": "mammogram",
    "colonoscopy": "colonoscopy", "colonoscopy_report": "colonoscopy",
    "outpatient_visit": "outpatient_visit", "visit_note": "outpatient_visit",
    "inpatient_stay": "inpatient_stay", "ed_visit": "ed_visit", "ed_summary": "ed_visit",
}
# Which concept the golden record takes, best first (the record that carries the result wins).
GOLDEN_CONCEPT = {"hba1c": ("hba1c", "hba1c_test"), "fit": ("fit_result", "fit_test"), "mammogram": ("mammogram",),
                  "colonoscopy": ("colonoscopy",), "outpatient_visit": ("outpatient_visit",), "inpatient_stay": ("inpatient_stay",),
                  "ed_visit": ("ed_visit",)}
REPORT_CONCEPTS = {"hba1c_report", "mammogram_report", "colonoscopy_report", "visit_note", "ed_summary"}
REPORT_WINDOW_DAYS = 14  # a report may be filed up to two weeks after the service
SOURCE_RANK = {"observations": 1, "workflow": 2, "procedures": 3, "claims": 4, "encounters": 5, "documents": 6, "conditions": 7}
SOURCE_CLASS = {"observations": STANDARD_SUPPLEMENTAL, "procedures": STANDARD_SUPPLEMENTAL, "encounters": STANDARD_SUPPLEMENTAL,
                "conditions": STANDARD_SUPPLEMENTAL, "claims": ADMINISTRATIVE, "documents": NON_STANDARD_SUPPLEMENTAL,
                "workflow": NON_STANDARD_SUPPLEMENTAL}
PLAUSIBLE = {"hba1c": (3.0, 20.0), "systolic": (60, 300), "diastolic": (30, 200), "egfr": (1, 200), "uacr": (0, 5000)}
HISTORY_YEARS = 10
INVALID_FLAGS = {"MISSING_DATE", "FUTURE_DATE", "INVALID_VALUE", "IMPLAUSIBLE", "NOT_COMPLETED", "CLAIM_DENIED"}


@dataclass
class Evidence:
    evidence_id: str
    patient_id: str
    concept: str
    code_system: str
    code: str
    service_date: str | None
    source: str
    source_record_id: str
    source_class: str
    value: float | None = None
    value2: float | None = None
    unit: str = ""
    result_flag: str = ""
    status: str = ""
    verification: str = ""
    raw_value: str = ""
    flags: list = field(default_factory=list)
    lineage: list = field(default_factory=list)
    merged_from: list = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not (set(self.flags) & INVALID_FLAGS)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["usable"] = self.usable
        return data


def _eid(*parts) -> str:
    return "EV-" + hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%d-%b-%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(text[:11] if pattern == "%d-%b-%Y" else text[:10], pattern).date()
        except ValueError:
            continue
    return None


def _date_flags(raw: str, as_of: date) -> tuple[str | None, list]:
    if not (raw or "").strip():
        return None, ["MISSING_DATE"]
    parsed = _parse_date(raw)
    if parsed is None:
        return None, ["MISSING_DATE"]
    flags = []
    if parsed > as_of:
        flags.append("FUTURE_DATE")
    if parsed < date(as_of.year - HISTORY_YEARS, 1, 1):
        flags.append("OUTSIDE_HISTORY")
    return parsed.isoformat(), flags


def _plausible(key: str, value: float | None) -> bool:
    low, high = PLAUSIBLE[key]
    return value is not None and low <= value <= high


def _observation(row: dict, as_of: date) -> Evidence | None:
    mapped = OBSERVATION_MAP.get(row["type"])
    if not mapped:
        return None
    concept, system, code = mapped
    service_date, flags = _date_flags(row["date"], as_of)
    raw, unit = (row["value"] or "").strip(), (row["unit"] or "").strip()
    value = value2 = None
    result_flag = ""
    if concept == "bp":
        match = re.fullmatch(r"(\d{2,3})\s*/\s*(\d{2,3})", raw)
        if match:
            value, value2 = float(match.group(1)), float(match.group(2))
            if not (_plausible("systolic", value) and _plausible("diastolic", value2)) or value <= value2:
                flags.append("IMPLAUSIBLE")
        else:
            flags.append("INVALID_VALUE")
        unit = "mmHg"
    elif concept == "fit_result":
        lowered = raw.lower()
        result_flag = "positive" if lowered.startswith(("pos", "abn", "detected")) else "negative" if lowered.startswith(("neg", "normal", "not det")) else ""
        if not result_flag:
            flags.append("INVALID_VALUE")
        unit = ""
    else:
        try:
            value = float(raw)
        except ValueError:
            flags.append("INVALID_VALUE")
        if concept == "hba1c" and value is not None:
            if unit.lower() in ("mmol/mol", "mmol/mol hb"):
                value, unit = round(0.0915 * value + 2.15, 1), "%"  # IFCC -> NGSP master equation
                flags.append("UNIT_CONVERTED")
            elif unit not in ("%", ""):
                flags.append("UNIT_UNKNOWN")
            unit = "%"
            if not _plausible("hba1c", value):
                flags.append("IMPLAUSIBLE")
        elif concept in ("egfr", "uacr") and not _plausible(concept, value):
            flags.append("IMPLAUSIBLE")
    return Evidence(_eid("obs", row["observation_id"]), row["patient_id"], concept, system, code, service_date, "observations",
                    row["observation_id"], SOURCE_CLASS["observations"], value, value2, unit, result_flag, "final", "", raw, flags)


def _procedure(row: dict, as_of: date) -> Evidence | None:
    mapped = PROCEDURE_MAP.get(row["procedure_code"])
    if not mapped:
        return None
    concept, system = mapped
    service_date, flags = _date_flags(row["service_date"], as_of)
    if (row["status"] or "").lower() not in ("completed", "final", "done"):
        flags.append("NOT_COMPLETED")
    flags += ["EXTERNAL_SOURCE"] if "external" in (row["description"] or "").lower() else []
    return Evidence(_eid("proc", row["procedure_id"]), row["patient_id"], concept, system, row["procedure_code"], service_date, "procedures",
                    row["procedure_id"], SOURCE_CLASS["procedures"], status=row["status"], flags=flags)


def _claim(row: dict, as_of: date) -> Evidence | None:
    mapped = CLAIM_MAP.get(row["procedure_code"])
    if not mapped:
        return None
    concept, system = mapped
    service_date, flags = _date_flags(row["service_date"], as_of)
    if (row["status"] or "").lower() == "denied":
        flags.append("CLAIM_DENIED")
    return Evidence(_eid("clm", row["claim_id"]), row["patient_id"], concept, system, row["procedure_code"], service_date, "claims",
                    row["claim_id"], SOURCE_CLASS["claims"], status=row["status"], flags=flags)


def _condition(row: dict, as_of: date) -> Evidence | None:
    code = (row["icd10_code"] or "").upper()
    concept = next((name for prefix, name in CONDITION_PREFIX_MAP.items() if code.startswith(prefix)), None)
    if not concept:
        return None
    service_date, flags = _date_flags(row["onset_date"], as_of)
    flags = [f for f in flags if f != "MISSING_DATE"]  # an active diagnosis without onset date still counts
    if (row["status"] or "").lower() not in ("active", "resolved", "chronic", ""):
        flags.append("NOT_COMPLETED")
    return Evidence(_eid("cond", row["condition_id"]), row["patient_id"], concept, "ICD-10-CM", code, service_date, "conditions",
                    row["condition_id"], SOURCE_CLASS["conditions"], status=row["status"], flags=flags)


def _encounter(row: dict, as_of: date) -> Evidence | None:
    concept = ENCOUNTER_MAP.get(row["encounter_type"])
    if not concept:
        return None
    service_date, flags = _date_flags(row["date"], as_of)
    if (row["disposition"] or "").lower() in ("cancelled", "no show", "no-show"):
        flags.append("NOT_COMPLETED")
    return Evidence(_eid("enc", row["encounter_id"]), row["patient_id"], concept, "ENC", row["encounter_type"], service_date, "encounters",
                    row["encounter_id"], SOURCE_CLASS["encounters"], status=row["disposition"], flags=flags)


def _document(row: dict, as_of: date) -> Evidence | None:
    concept = DOCUMENT_MAP.get(row["document_type"])
    if not concept:
        return None
    service_date, flags = _date_flags(row["document_date"], as_of)
    verification = "verified" if (row["verification_status"] or "").lower() == "verified" else "pending"
    return Evidence(_eid("doc", row["document_id"]), row["patient_id"], concept, "DOC", row["document_type"], service_date, "documents",
                    row["document_id"], SOURCE_CLASS["documents"], verification=verification, flags=flags)


def _workflow(item: dict, as_of: date) -> Evidence:
    service_date, flags = _date_flags(item["service_date"], as_of)
    return Evidence(_eid("wf", item["capture_id"]), item["patient_id"], item["concept"], item.get("code_system", "WORKFLOW"),
                    item.get("code", item["concept"]), service_date, "workflow", item["capture_id"], SOURCE_CLASS["workflow"],
                    item.get("value"), item.get("value2"), item.get("unit", ""), item.get("result_flag", ""), "final",
                    "verified" if item.get("verified_by") else "pending", str(item.get("value", "")), flags)


PARSERS = (("observations", _observation, "observation_id", "type"), ("procedures", _procedure, "procedure_id", "procedure_code"),
           ("claims", _claim, "claim_id", "procedure_code"), ("conditions", _condition, "condition_id", "icd10_code"),
           ("encounters", _encounter, "encounter_id", "encounter_type"), ("documents", _document, "document_id", "document_type"))


def _merge(records: list[Evidence]) -> tuple[list[Evidence], int]:
    """Collapse the same event reported by several sources into one golden record with full lineage."""
    by_key = defaultdict(list)
    passthrough = []
    for record in records:
        family = FAMILY.get(record.concept)
        if family and record.service_date:
            by_key[(record.patient_id, family, record.service_date)].append(record)
        else:
            passthrough.append(record)
    golden, merged = [], 0
    reports = defaultdict(list)
    for (patient_id, family, _), group in by_key.items():
        primary = [r for r in group if r.concept in GOLDEN_CONCEPT.get(family, ())]
        if not primary:  # report without a matching service on that date: linked later within the window
            for record in group:
                reports[(patient_id, family)].append(record)
            continue
        # One golden record per distinct result (two different same-day BP readings stay separate).
        primary.sort(key=lambda r: (GOLDEN_CONCEPT[family].index(r.concept), SOURCE_RANK[r.source]))
        heads = []
        for record in primary:
            twin = next((h for h in heads if (h.value, h.value2, h.result_flag) == (record.value, record.value2, record.result_flag)
                         or record.value is None and not record.result_flag), None)
            if twin is None:
                heads.append(record)
            else:
                twin.merged_from.append(record.evidence_id)
                twin.lineage.append({"source": record.source, "record_id": record.source_record_id, "concept": record.concept,
                                     "status": record.status, "flags": record.flags})
                merged += 1
        for record in group:
            if record.concept in REPORT_CONCEPTS:
                for head in heads:
                    head.lineage.append({"source": record.source, "record_id": record.source_record_id, "concept": record.concept,
                                         "verification": record.verification})
                    if record.verification and not head.verification:
                        head.verification = record.verification
                merged += 1
        golden.extend(heads)
    for (patient_id, family), items in reports.items():
        for report in items:
            target = next((g for g in golden if g.patient_id == patient_id and FAMILY.get(g.concept) == family and g.service_date
                           and report.service_date and 0 <= (date.fromisoformat(report.service_date) - date.fromisoformat(g.service_date)).days
                           <= REPORT_WINDOW_DAYS), None)
            if target:
                target.lineage.append({"source": report.source, "record_id": report.source_record_id, "concept": report.concept,
                                       "verification": report.verification})
                target.verification = target.verification or report.verification
                merged += 1
            else:
                golden.append(report)
    for record in golden:
        record.lineage.insert(0, {"source": record.source, "record_id": record.source_record_id, "concept": record.concept,
                                  "status": record.status, "flags": record.flags, "primary": True})
        if record.concept in ("mammogram", "colonoscopy") and record.verification == "":
            record.flags.append("REPORT_MISSING")
    return golden + passthrough, merged


def normalize(workflow_evidence: list[dict] | None = None, as_of: date | None = None) -> dict:
    """Run every source through map -> parse -> check -> merge and return the canonical set plus the run report."""
    as_of = as_of or sources.period()[1]
    report = {"sources": {}, "unmapped": Counter(), "flags": Counter(), "concepts": Counter()}
    records: list[Evidence] = []
    for name, parser, id_field, code_field in PARSERS:
        stats = {"read": 0, "mapped": 0, "unmapped": 0, "flagged": 0}
        for row in sources.table(name):
            stats["read"] += 1
            evidence = parser(row, as_of)
            if evidence is None:
                code = row.get(code_field, "")
                recognised = name == "conditions" and str(code).upper().startswith(CONDITION_RECOGNISED)
                stats["unmapped"] += 0 if recognised else 1
                stats["recognised_not_evidence"] = stats.get("recognised_not_evidence", 0) + (1 if recognised else 0)
                if not recognised:
                    report["unmapped"][f"{name}:{code}"] += 1
                continue
            stats["mapped"] += 1
            stats["flagged"] += bool(evidence.flags)
            for flag in evidence.flags:
                report["flags"][flag] += 1
            records.append(evidence)
        considered = stats["read"] - stats.get("recognised_not_evidence", 0)
        stats["mapping_rate"] = round(stats["mapped"] / considered * 100, 1) if considered else 100.0
        report["sources"][name] = stats
    if workflow_evidence:
        captured = [_workflow(item, as_of) for item in workflow_evidence if item.get("verified_by")]
        records += captured
        report["sources"]["workflow"] = {"read": len(workflow_evidence), "mapped": len(captured), "unmapped": 0,
                                         "flagged": sum(bool(r.flags) for r in captured), "mapping_rate": 100.0,
                                         "pending_verification": len(workflow_evidence) - len(captured)}
    golden, merged = _merge(records)
    for record in golden:
        report["concepts"][record.concept] += 1
    total_considered = sum(s["read"] - s.get("recognised_not_evidence", 0) for s in report["sources"].values())
    total_mapped = sum(s["mapped"] for s in report["sources"].values())
    by_patient = defaultdict(list)
    for record in golden:
        by_patient[record.patient_id].append(record)
    return {
        "evidence": golden, "by_patient": dict(by_patient), "as_of": as_of.isoformat(), "dataset_sha256": sources.dataset_sha256(),
        "summary": {"raw_records": len(records), "golden_records": len(golden), "merged": merged,
                    "mapping_rate": round(total_mapped / total_considered * 100, 1) if total_considered else 100.0,
                    "unusable": sum(not r.usable for r in golden)},
        "sources": report["sources"], "unmapped": dict(report["unmapped"].most_common()), "flags": dict(report["flags"].most_common()),
        "concepts": dict(report["concepts"].most_common()),
    }


def patient_demographics() -> dict:
    """Member facts used for eligibility, with the DOB-derived age and a flag when DOB is missing."""
    _, end = sources.period()
    out = {}
    for row in sources.table("patients"):
        dob = _parse_date(row["dob"])
        if dob:
            age, flag = end.year - dob.year - ((end.month, end.day) < (dob.month, dob.day)), None
        else:
            age, flag = (int(row["age"]) if (row.get("age") or "").isdigit() else None), "DOB_MISSING_AGE_FROM_ENROLLMENT"
        out[row["patient_id"]] = {"patient_id": row["patient_id"], "name": f"{row['first_name']} {row['last_name']}".strip(),
                                  "sex": row["sex"], "dob": dob.isoformat() if dob else None, "age": age, "age_flag": flag,
                                  "coverage_start": row["coverage_start"], "coverage_end": row["coverage_end"],
                                  "provider_id": row["attributed_provider_id"], "segment": row["population_segment"]}
    return out


__all__ = ["Evidence", "normalize", "patient_demographics"]
