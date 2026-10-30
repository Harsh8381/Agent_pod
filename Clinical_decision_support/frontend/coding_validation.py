"""Pre-coding and post-coding validation for the Medical Coding Workspace.

Pre-coding validation asks "is this encounter's documentation ready to be coded?".
Post-coding validation asks "is the coder's code set clean enough to bill?".

Every check returns a dict with ``name``, ``status`` ("pass" | "warn" | "fail") and
``detail``. A "fail" blocks the next workflow step; a "warn" needs coder review but
does not block. All functions are pure (no Streamlit) so they can be unit-tested.
"""
from __future__ import annotations

import re
from typing import Any, Callable

PASS, WARN, FAIL = "pass", "warn", "fail"
REQUIRED_SOAP_SECTIONS = ("SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN")
DECIDED_STATUSES = {"Accepted", "Rejected", "Query sent"}
EM_CODES = {"99202", "99203", "99204", "99205", "99211", "99212", "99213", "99214", "99215"}
# Claim-scrub issue types that block billing; the rest are surfaced as warnings.
BLOCKING_SCRUB_TYPES = {"invalid_cpt", "imaging_mismatch", "unsupported_cpt"}


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _section_text(note: str, section: str) -> str | None:
    """Return the text under a SOAP heading, or None when the heading is absent."""
    pattern = rf"(?ims)^\s*#*\s*\**{section}\**\s*:?\s*(.*?)(?=^\s*#*\s*\**(?:{'|'.join(REQUIRED_SOAP_SECTIONS)})\**\s*:|\Z)"
    match = re.search(pattern, note or "")
    return match.group(1).strip() if match else None


def missing_soap_sections(note: str) -> list[str]:
    return [section.title() for section in REQUIRED_SOAP_SECTIONS if _section_text(note, section) is None]


def pre_coding_checks(record: dict[str, Any], chart: dict[str, Any] | None, insights: list[str]) -> list[dict[str, str]]:
    """Documentation readiness checks run before the encounter is coded."""
    note = str(record.get("summary") or record.get("patient_summary") or "")
    checks = []

    if record.get("status") == "Approved":
        checks.append(_check("Clinical note signed", PASS, "Coding the signed, locked SOAP note"))
    else:
        checks.append(_check("Clinical note signed", WARN, "Note is still a draft — final coding should follow provider signature"))

    missing = missing_soap_sections(note)
    if not note.strip():
        checks.append(_check("SOAP sections complete", FAIL, "No SOAP note is documented for this encounter"))
    elif missing:
        checks.append(_check("SOAP sections complete", FAIL, "Missing: " + ", ".join(missing)))
    else:
        checks.append(_check("SOAP sections complete", PASS, "Subjective, Objective, Assessment and Plan present"))

    assessment = _section_text(note, "ASSESSMENT") or ""
    if not assessment or re.search(r"\bnot documented\b|\bnone\b|\bn/?a\b", assessment, re.IGNORECASE) and len(assessment) < 40:
        checks.append(_check("Assessment documented", WARN, "No clinical assessment — diagnoses cannot be confirmed; query the provider"))
    else:
        checks.append(_check("Assessment documented", PASS, "Assessment supports diagnosis coding"))

    missing_ids = [
        label for label, value in (
            ("encounter ID", record.get("ehr_encounter_id") or record.get("id")),
            ("date of service", record.get("date")),
            ("rendering provider", record.get("doctor") if str(record.get("doctor", "")).strip().lower() not in {"", "doctor"} else ""),
        ) if not str(value or "").strip()
    ]
    if missing_ids:
        checks.append(_check("Encounter identifiers", FAIL, "Missing " + ", ".join(missing_ids)))
    else:
        checks.append(_check("Encounter identifiers", PASS, "Encounter ID, date of service and provider present"))

    if not str(record.get("transcript") or "").strip():
        checks.append(_check("Source documentation", FAIL, "No transcript is attached to support the codes"))
    else:
        checks.append(_check("Source documentation", PASS, "Transcript attached for code provenance"))

    if insights:
        checks.append(_check("Codable content", PASS, f"{len(insights)} clinical concept(s) detected"))
    else:
        checks.append(_check("Codable content", FAIL, "No diagnoses, symptoms or services detected in the documentation"))

    if record.get("ehr_patient_id") or record.get("patient_id"):
        checks.append(_check("Patient linked to EHR", PASS, str(record.get("ehr_patient_id") or record.get("patient_id"))))
    else:
        checks.append(_check("Patient linked to EHR", WARN, "Local consultation only — demographics and coverage cannot be verified"))

    coverages = ((chart or {}).get("records") or {}).get("Insurance") or []
    primary = next((row for row in coverages if str(row.get("Priority", "")).lower() == "primary"), coverages[0] if coverages else None)
    if not (chart or {}).get("patient"):
        checks.append(_check("Coverage on file", WARN, "EHR chart unavailable — coverage could not be verified"))
    elif not primary:
        checks.append(_check("Coverage on file", WARN, "No insurance coverage found for the patient"))
    elif str(primary.get("Status", "")).lower() != "active":
        checks.append(_check("Coverage on file", WARN, f'{primary.get("Payer_Name", "Payer")} coverage is {primary.get("Status") or "not active"}'))
    else:
        checks.append(_check("Coverage on file", PASS, f'{primary.get("Payer_Name", "Payer")} · active'))
    return checks


def _scrub_checks(scrub: dict[str, Any] | None) -> list[dict[str, str]]:
    if scrub is None:
        return [_check("Claim scrub", WARN, "Claim scrubber unavailable — CPT reference and pricing edits were not run")]
    checks = []
    for issue in scrub.get("errors", []):
        status = FAIL if issue.get("type") in BLOCKING_SCRUB_TYPES else WARN
        checks.append(_check(f'Claim scrub · {issue.get("type", "issue").replace("_", " ")}', status, issue.get("message", "")))
    for issue in scrub.get("warnings", []):
        checks.append(_check(f'Claim scrub · {issue.get("type", "warning").replace("_", " ")}', WARN, issue.get("message", "")))
    if not checks:
        checks.append(_check("Claim scrub", PASS, "CPT reference, pricing, imaging and documentation-support edits passed"))
    return checks


def post_coding_checks(
    rows: list[dict[str, Any]],
    documentation: str = "",
    meat_signals: Callable[[str], dict[str, bool]] | None = None,
    scrub: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Code-set integrity checks run after coder decisions, before billing.

    ``rows`` are coding-workspace rows with keys: key, type, code, description, status,
    hcc, excerpt and (for CPT rows) procedure_status.
    """
    active = [row for row in rows if row.get("status") != "Rejected"]
    icd = [row for row in active if row.get("type") == "ICD-10-CM"]
    cpt = [row for row in active if str(row.get("type", "")).startswith("CPT")]
    checks = []

    accepted_dx = [row for row in icd if row.get("status") == "Accepted"]
    if accepted_dx:
        checks.append(_check("Primary diagnosis", PASS, f'{accepted_dx[0]["code"]} accepted' + (f" (+{len(accepted_dx) - 1} more)" if len(accepted_dx) > 1 else "")))
    elif icd:
        checks.append(_check("Primary diagnosis", FAIL, "No ICD-10-CM code has been accepted yet"))
    else:
        checks.append(_check("Primary diagnosis", FAIL, "No ICD-10-CM diagnosis remains on the encounter"))

    if cpt and not icd:
        checks.append(_check("Procedure–diagnosis linkage", FAIL, "Procedures are coded without any supporting diagnosis (medical necessity)"))
    elif cpt:
        checks.append(_check("Procedure–diagnosis linkage", PASS, f"{len(cpt)} service(s) supported by {len(icd)} diagnosis code(s)"))

    not_performed = [
        row["code"] for row in cpt
        if row.get("status") == "Accepted" and row.get("code") not in EM_CODES
        # A provider-marked superbill line, or a coder's billing override, attests the service was rendered.
        and row.get("procedure_status") not in {"performed", "superbill", "coder_confirmed", None}
    ]
    if not_performed:
        checks.append(_check("Accepted services performed", FAIL, "Accepted but not documented as performed: " + ", ".join(not_performed)))
    elif cpt:
        checks.append(_check("Accepted services performed", PASS, "Every accepted service is documented as performed"))

    pending_queries = [row["code"] for row in active if row.get("status") == "Query sent"]
    if pending_queries:
        checks.append(_check("Open physician queries", WARN, "Awaiting provider response: " + ", ".join(pending_queries)))
    else:
        checks.append(_check("Open physician queries", PASS, "No unresolved queries"))

    undecided = [row["code"] for row in active if row.get("status") not in DECIDED_STATUSES]
    if undecided:
        checks.append(_check("Coder decisions", WARN, f"{len(undecided)} code(s) not yet accepted or rejected: " + ", ".join(undecided)))
    else:
        checks.append(_check("Coder decisions", PASS, "Every code has a coder decision"))

    symptoms = [row["code"] for row in icd if str(row.get("code", "")).upper().startswith("R")]
    definitive = [row["code"] for row in icd if not str(row.get("code", "")).upper().startswith("R")]
    if symptoms and definitive:
        checks.append(_check(
            "Symptom codes with definitive diagnosis", WARN,
            f"{', '.join(symptoms)} coded alongside {', '.join(definitive)} — drop symptoms integral to the confirmed diagnosis",
        ))

    unspecified = [row["code"] for row in icd if "unspecified" in str(row.get("description", "")).lower()]
    if unspecified:
        checks.append(_check("Diagnosis specificity", WARN, "Unspecified code(s): " + ", ".join(unspecified) + " — query if documentation supports more detail"))
    elif icd:
        checks.append(_check("Diagnosis specificity", PASS, "No unspecified diagnosis codes"))

    codes = [str(row.get("code", "")).upper() for row in active]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        checks.append(_check("Duplicate codes", WARN, "Coded more than once: " + ", ".join(duplicates)))

    hcc_rows = [row for row in icd if row.get("hcc")]
    if hcc_rows and meat_signals:
        weak = []
        for row in hcc_rows:
            signals = meat_signals(f'{row.get("excerpt", "")} {documentation}')
            if sum(signals.values()) < 2:
                weak.append(f'{row["code"]} ({row["hcc"]})')
        if weak:
            checks.append(_check("HCC MEAT support", WARN, "Fewer than two MEAT elements documented for " + ", ".join(weak)))
        else:
            checks.append(_check("HCC MEAT support", PASS, f"{len(hcc_rows)} HCC diagnosis(es) supported by MEAT documentation"))

    em_rows = [row for row in cpt if row.get("code") in EM_CODES]
    if len(em_rows) > 1:
        checks.append(_check("E/M level", FAIL, "More than one E/M code on the encounter: " + ", ".join(row["code"] for row in em_rows)))
    elif em_rows:
        checks.append(_check("E/M level", PASS if em_rows[0].get("status") == "Accepted" else WARN,
                             f'{em_rows[0]["code"]} ' + ("accepted" if em_rows[0].get("status") == "Accepted" else "needs MDM validation against the signed note")))
    elif re.search(r"\boffice visit\b|\bfollow[- ]up\b|\bconsult", documentation or "", re.IGNORECASE):
        checks.append(_check("E/M level", WARN, "Documentation describes a visit but no E/M service is coded"))

    checks.extend(_scrub_checks(scrub))
    return checks


def summarize(checks: list[dict[str, str]]) -> dict[str, Any]:
    fails = sum(check["status"] == FAIL for check in checks)
    warns = sum(check["status"] == WARN for check in checks)
    score = max(0, 100 - 25 * fails - 5 * warns)
    if fails:
        label, status = f"Blocked · {fails} issue{'s' if fails > 1 else ''} to resolve", FAIL
    elif warns:
        label, status = f"Ready with {warns} warning{'s' if warns > 1 else ''}", WARN
    else:
        label, status = "Clean · all checks passed", PASS
    return {"status": status, "label": label, "fails": fails, "warnings": warns, "score": score, "total": len(checks)}
