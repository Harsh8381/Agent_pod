"""Medical Coding Workspace: code suggestions, evidence, coder decisions and coding validation."""

import re
from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st

from frontend import ocr_service as ocr
from frontend.clinical_text import best_excerpt
from frontend.coding_validation import post_coding_checks, pre_coding_checks
from frontend.coding_validation import summarize as summarize_checks
from frontend.ehr_chart import format_date
from frontend.encounter_pipeline import (
    _procedure_status_inputs,
    classify_procedure_status,
    get_backend_code_suggestions,
    get_code_results_signature,
    get_record_insights,
    normalize_billing_code,
)
from frontend.settings import CODE_MATCHER_VERSION
from frontend.ui.components import (
    badge,
    card_title,
    page_header,
    page_title,
    record_subtitle,
    render_banner,
    render_checklist,
    render_code_results,
    render_grouped_insights,
    render_tiles,
)
from frontend.ui.data import record_chart
from frontend.ui.shell import render_page
from frontend.ui.state import can, can_open, current_user, log_action, open_billing, open_record, save_db, save_record
from frontend.views.ocr_widgets import render_superbill_import


def persist_consultation_cpt_results(record, cpt_results):
    history = record.get("cpt_results_history", [])
    history = list(history) if isinstance(history, list) else []
    known_candidates = {
        (normalize_billing_code(item.get("CPT/HCPCS Code")), str(item.get("Extracted Procedure", "")).casefold())
        for item in history
        if isinstance(item, dict)
    }
    for candidate in cpt_results or []:
        identity = (
            normalize_billing_code(candidate.get("CPT/HCPCS Code")),
            str(candidate.get("Extracted Procedure", "")).casefold(),
        )
        if identity not in known_candidates:
            history.append(dict(candidate))
            known_candidates.add(identity)
    record["cpt_results_history"] = history
    record["code_results"] = {
        **(record.get("code_results") if isinstance(record.get("code_results"), dict) else {}),
        "cpt": list(cpt_results or []),
    }
    save_db(st.session_state.records)


# Small, explicitly labelled demo subset of CMS-HCC V28 mappings.
HCC_V28_DEMO = {
    "E10.9": "HCC 38",
    "E11.9": "HCC 38",
    "E66.01": "HCC 48",
    "I48.91": "HCC 238",
    "I50.9": "HCC 226",
    "J44.9": "HCC 280",
    "N18.4": "HCC 327",
    "N18.5": "HCC 326",
}

EM_MDM_LEVELS = {
    "99202": "Straightforward",
    "99203": "Low",
    "99204": "Moderate",
    "99205": "High",
    "99212": "Straightforward",
    "99213": "Low",
    "99214": "Moderate",
    "99215": "High",
}

CODE_STATUS_TONES = {
    "Accepted": "green",
    "Rejected": "red",
    "Query sent": "amber",
    "Suggested": "amber",
    "Symptom only": "violet",
    "Ordered": "violet",
    "Planned": "violet",
    "Discussed": "violet",
    "Performed": "blue",
    "E/M check": "amber",
    "Unverified": "gray",
    "Superbill": "blue",
}

MEAT_TERMS = {
    "M": ("monitor", "follow", "stable", "improv", "worse", "symptom", "progress", "history", "since"),
    "E": ("test", "result", "lab", "exam", "a1c", "x-ray", "level", "review", "vital", "reading"),
    "A": ("assess", "diagnos", "consistent", "likely", "impression", "controlled", "uncontrolled", "exacerbation"),
    "T": ("medication", "prescri", "continue", "start", "mg", "refer", "therapy", "treat", "inhaler", "metformin"),
}


def match_score(term, description):
    if not term or not description:
        return None
    try:
        from rapidfuzz import fuzz

        return int(round(fuzz.token_set_ratio(str(term).lower(), str(description).lower())))
    except ImportError:
        from difflib import SequenceMatcher

        return int(round(SequenceMatcher(None, str(term).lower(), str(description).lower()).ratio() * 100))


def record_documentation(record):
    """Transcript, SOAP note and any OCR'd supporting documents attached to the encounter."""
    documents = "\n".join(str(doc.get("text") or "") for doc in record.get("documents") or [])
    return f"{record.get('transcript', '')}\n{record.get('summary', '')}\n{documents}".strip()


def encounter_code_results(record, base=None):
    """Matcher suggestions plus codes imported from a paper superbill (OCR)."""
    base = base if base is not None else (record.get("code_results") or {})
    return ocr.merge_code_results(base, record.get("imported_codes"))


def build_code_rows(record, code_results):
    documentation = record_documentation(record)
    decisions = record.get("code_decisions") or {}
    rows = []
    for item in code_results.get("icd10", []) or []:
        code = str(item.get("ICD-10 Code") or "").strip()
        if not code:
            continue
        term = item.get("Extracted Condition", "")
        auto_status = "Symptom only" if code.upper().startswith("R") else "Suggested"
        key = f"ICD:{code}"
        rows.append(
            {
                "key": key,
                "type": "ICD-10-CM",
                "code": code,
                "term": term,
                "description": item.get("Matched Disease/Injury", ""),
                "hcc": HCC_V28_DEMO.get(code.upper(), ""),
                "match": match_score(term, item.get("Matched Disease/Injury", "")),
                "status": decisions.get(key, {}).get("status") or auto_status,
                "excerpt": best_excerpt(term, documentation),
            }
        )
    for item in code_results.get("cpt", []) or []:
        code = str(item.get("CPT/HCPCS Code") or "").strip()
        if not code:
            continue
        term = item.get("Extracted Procedure", "")
        status_procedure, status_documentation = _procedure_status_inputs(term, documentation)
        procedure_status = (
            classify_procedure_status(status_procedure, status_documentation) if classify_procedure_status else "unknown"
        )
        from_superbill = item.get("source") == "ocr_superbill"
        if from_superbill and procedure_status != "performed":
            procedure_status = "superbill"
        elif procedure_status != "performed" and code in (record.get("billing_overrides") or {}):
            procedure_status = "coder_confirmed"
        if code in EM_MDM_LEVELS:
            auto_status = "E/M check"
        elif procedure_status == "superbill":
            auto_status = "Superbill"
        elif procedure_status == "coder_confirmed":
            auto_status = "Coder confirmed"
        else:
            auto_status = {
                "performed": "Performed",
                "ordered_not_performed": "Ordered",
                "planned": "Planned",
                "recommended": "Planned",
                "discussed": "Discussed",
            }.get(procedure_status, "Unverified")
        key = f"CPT:{code}"
        rows.append(
            {
                "key": key,
                "type": "CPT · E/M" if code in EM_MDM_LEVELS else "CPT",
                "code": code,
                "term": term,
                "description": item.get("Matched Procedure/Service", ""),
                "hcc": "",
                "match": match_score(term, item.get("Matched Procedure/Service", "")),
                "status": decisions.get(key, {}).get("status") or auto_status,
                "procedure_status": procedure_status,
                "excerpt": best_excerpt(term, documentation),
            }
        )
    return rows


def set_code_decision(record, keys, status, note=""):
    if not can("Codes", "Edit"):
        st.toast("Your role cannot change coding decisions (Codes · Edit).")
        return
    decisions = record.setdefault("code_decisions", {})
    audit = record.setdefault("coding_audit", [])
    stamp = datetime.now().strftime("%d %b %Y %H:%M")
    by = current_user()["username"]
    for key in keys:
        decisions[key] = {"status": status, "at": stamp, "note": note, "by": by}
        audit.append({"at": stamp, "code": key, "action": status, "note": note, "by": by})
    save_record(record)
    log_action("coding.decision", f"{record.get('id')}: {status} {', '.join(keys)}")


def meat_signals(text):
    lowered = str(text or "").lower()
    return {letter: any(term in lowered for term in terms) for letter, terms in MEAT_TERMS.items()}


def ensure_code_results(record, insights):
    """Run (or reuse) code matching for this record, mirroring the cached signature logic."""
    current_signature = get_code_results_signature(insights, record.get("transcript", ""))
    cached_results_are_stale = (
        st.session_state.code_record_id != record.get("id")
        or st.session_state.code_results_signature != current_signature
        or st.session_state.code_matcher_version != CODE_MATCHER_VERSION
    )
    if cached_results_are_stale:
        with st.spinner("Matching ICD-10 and CPT/HCPCS codes..."):
            st.session_state.code_results = get_backend_code_suggestions(insights, record.get("transcript", ""))
            persist_consultation_cpt_results(record, st.session_state.code_results.get("cpt", []))
            record["code_results"]["icd10"] = list(st.session_state.code_results.get("icd10", []))
            save_record(record)
        st.session_state.code_record_id = record.get("id")
        st.session_state.code_results_signature = current_signature
        st.session_state.code_matcher_version = CODE_MATCHER_VERSION
    return current_signature


def _render_code_table(record, rows):
    widths = [0.45, 2.7, 1.0, 0.85, 0.85, 1.35, 1.2, 0.7]
    header = st.columns(widths, vertical_alignment="center")
    for col, label in zip(header, ("", "Documentation", "Type", "Code", "HCC", "Match", "Status", ""), strict=True):
        col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
    st.markdown('<div class="nc-rule"></div>', unsafe_allow_html=True)
    selected_keys = []
    for row in rows:
        is_active = st.session_state.active_code == row["key"]
        with st.container(key=f"coderow_{'sel' if is_active else 'off'}_{re.sub(r'[^A-Za-z0-9]', '_', row['key'])}"):
            cols = st.columns(widths, vertical_alignment="center")
            if cols[0].checkbox("select", key=f"code_pick_{record.get('id')}_{row['key']}", label_visibility="collapsed"):
                selected_keys.append(row["key"])
            cols[1].markdown(
                f'<div class="nc-cell">{escape(row["description"] or row["term"])}<div class="nc-meta">{escape(row["term"])}</div></div>',
                unsafe_allow_html=True,
            )
            cols[2].markdown(f'<div class="nc-cell muted">{escape(row["type"])}</div>', unsafe_allow_html=True)
            cols[3].markdown(f'<div class="nc-cell"><b>{escape(row["code"])}</b></div>', unsafe_allow_html=True)
            cols[4].markdown(
                badge(row["hcc"], "blue") if row["hcc"] else '<span class="nc-cell muted">—</span>', unsafe_allow_html=True
            )
            if row["match"] is None:
                cols[5].markdown('<span class="nc-cell muted">—</span>', unsafe_allow_html=True)
            else:
                cols[5].markdown(
                    f'<div class="nc-meter" style="margin:0"><span><em style="width:{row["match"]}%"></em></span>{row["match"]}%</div>',
                    unsafe_allow_html=True,
                )
            cols[6].markdown(badge(row["status"], CODE_STATUS_TONES.get(row["status"], "gray")), unsafe_allow_html=True)
            cols[7].button(
                "View",
                key=f"code_view_{row['key']}",
                type="tertiary",
                on_click=lambda key=row["key"]: st.session_state.update(active_code=key),
            )
    return selected_keys


def _render_evidence_panel(record, row, is_signed):
    with st.container(border=True):
        match_badge = (
            badge(f"{row['match']}% match", "green" if (row["match"] or 0) >= 85 else "amber") if row["match"] is not None else ""
        )
        card_title("Evidence & Provenance", f"{row['code']} · {row['type']}", trailing=match_badge)
        excerpt = row["excerpt"] or "No supporting sentence was found in the transcript or SOAP note."
        st.markdown(f'<div class="nc-excerpt"><small>Note excerpt</small><q>{escape(excerpt)}</q></div>', unsafe_allow_html=True)
        if row["type"] == "ICD-10-CM":
            signals = meat_signals(f"{row['excerpt']} {record.get('summary', '')}" if row["excerpt"] else "")
            labels = {"M": "Monitor", "E": "Evaluate", "A": "Assess", "T": "Treat"}
            meat = " · ".join(f"{letter}: {labels[letter]} {'✓' if found else '—'}" for letter, found in signals.items())
            st.markdown(
                f'<div class="nc-label" style="margin-top:.7rem">Why this code · MEAT (keyword scan)</div><div class="nc-cell">{escape(meat)}</div>',
                unsafe_allow_html=True,
            )
            if "unspecified" in str(row["description"]).lower():
                st.markdown(
                    '<div class="nc-cdi" style="margin-top:.6rem"><div class="nc-label">CDI opportunity</div>'
                    f"<b>{escape(row['code'])} is an unspecified code</b>"
                    "The note may support a more specific code. Do not infer specificity — query the provider.</div>",
                    unsafe_allow_html=True,
                )
                st.button(
                    "Send non-leading physician query",
                    key=f"code_query_{row['key']}",
                    type="secondary",
                    icon=":material/chat_bubble:",
                    disabled=row["status"] == "Query sent" or not can("Codes", "Edit"),
                    on_click=set_code_decision,
                    args=(record, [row["key"]], "Query sent", "Specificity query"),
                )
        else:
            status_text = {
                "performed": "Documented as performed during this encounter",
                "ordered_not_performed": "Ordered — no result or performed evidence yet",
                "planned": "Planned / recommended — not yet performed",
                "recommended": "Recommended — not yet performed",
                "discussed": "Discussed only",
                "superbill": "Marked on the provider's paper superbill (imported by OCR) — accept to bill",
            }.get(row.get("procedure_status"), "Performed status could not be verified")
            st.markdown(
                f'<div class="nc-label" style="margin-top:.7rem">Performed-service status</div><div class="nc-cell">{escape(status_text)}</div>',
                unsafe_allow_html=True,
            )
            if row["code"] in EM_MDM_LEVELS:
                st.markdown(
                    f'<div class="nc-label" style="margin-top:.6rem">E/M validation</div><div class="nc-cell">Level {escape(row["code"])} implies {EM_MDM_LEVELS[row["code"]]} MDM — confirm against the signed note.</div>',
                    unsafe_allow_html=True,
                )
        st.markdown(
            f'<div class="nc-meta" style="margin-top:.6rem">Source: {"Signed" if is_signed else "Draft"} SOAP note + transcript · '
            f"Matched “{escape(row['term'])}” → {escape(row['description'])}<br>Code matcher v{CODE_MATCHER_VERSION} · ICD-10-CM / CPT catalogs</div>",
            unsafe_allow_html=True,
        )
        can_code = can("Codes", "Edit")
        with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
            st.button(
                "Reject",
                key=f"code_reject_{row['key']}",
                type="secondary",
                disabled=not can_code,
                on_click=set_code_decision,
                args=(record, [row["key"]], "Rejected"),
            )
            st.button(
                "Accept",
                key=f"accent_code_accept_{row['key']}",
                disabled=not can_code,
                on_click=set_code_decision,
                args=(record, [row["key"]], "Accepted"),
            )


VALIDATION_TONES = {"pass": ("Pass", "green"), "warn": ("Warning", "amber"), "fail": ("Fail", "red")}


def run_claim_scrub(record, rows):
    """Run the project's claim scrubber over the non-rejected CPT codes; None if unavailable."""
    services = [
        {"CPT/HCPCS Code": row["code"], "Extracted Procedure": row["term"], "quantity": 1}
        for row in rows
        if str(row.get("type", "")).startswith("CPT") and row.get("status") != "Rejected"
    ]
    if not services:
        return {"errors": [], "warnings": []}
    encounter_type = "IPD" if str(record.get("ehr_encounter_type", "")).lower().startswith("inp") else "OPD"
    try:
        try:
            from frontend.billing_scrubber import scrub_claim
        except ModuleNotFoundError:
            from billing_scrubber import scrub_claim
        return scrub_claim(record_documentation(record), services, encounter_type=encounter_type)
    except Exception:
        return None


def coding_validation(record, rows):
    """Return (pre_checks, post_checks) for an encounter's current coding state."""
    pre = pre_coding_checks(record, record_chart(record), get_record_insights(record))
    post = post_coding_checks(rows, record_documentation(record), meat_signals=meat_signals, scrub=run_claim_scrub(record, rows))
    return pre, post


def render_validation_card(title, subtitle, checks, key):
    summary = summarize_checks(checks)
    label, tone = VALIDATION_TONES[summary["status"]]
    with st.container(border=True):
        card_title(title, subtitle, trailing=badge(summary["label"], tone))
        render_tiles(
            [
                ("Validation score", f"{summary['score']}/100"),
                ("Passed", summary["total"] - summary["fails"] - summary["warnings"]),
                ("Warnings", summary["warnings"]),
                ("Blocking", summary["fails"]),
            ]
        )
        ordered = sorted(checks, key=lambda check: {"fail": 0, "warn": 1, "pass": 2}[check["status"]])
        # Open the checklist only when something blocks; warnings stay one click away.
        with st.expander(f"{summary['total']} checks", expanded=summary["status"] == "fail"):
            render_checklist(
                [
                    (check["name"], VALIDATION_TONES[check["status"]][0], VALIDATION_TONES[check["status"]][1], check["detail"])
                    for check in ordered
                ],
                "No checks were run.",
            )
    return summary


def show_codes():
    render_page("codes")
    records = st.session_state.records
    if not records:
        page_title("CODING & HCC", "Medical Coding Workspace")
        st.info("No patient encounters are available. Create a consultation before generating codes.")
        return

    current = st.session_state.current_record
    record = next((r for r in records if current and r.get("id") == current.get("id")), records[0])
    actions = page_header(
        "CODING & HCC", "Medical Coding Workspace", f"{record_subtitle(record)} · synthetic demo record", action_weight=4.2
    )
    actions.button("← Return to Clinical Review", key="codes_back_review", type="secondary", on_click=open_record, args=(record,))
    actions.button(
        "Proceed to Billing →",
        key="accent_codes_billing",
        disabled=not can_open("billing"),
        help=None if can_open("billing") else "Billing requires RCM Workspace access.",
        on_click=open_billing,
        args=(record,),
    )

    with st.container(border=True):
        enc_col, badge_col = st.columns([5.5, 4.5], vertical_alignment="bottom")
        record_lookup = {
            f"{r.get('ehr_encounter_id') or r.get('id')} · {format_date(r.get('date'), '')} · {r.get('name', 'Unknown')} · {r.get('ehr_encounter_type') or 'Office visit'}": r
            for r in records
        }
        labels = list(record_lookup)
        default_label = next(label for label, value in record_lookup.items() if value.get("id") == record.get("id"))
        selected_label = enc_col.selectbox("Encounter", labels, index=labels.index(default_label), key="codes_encounter")
        if record_lookup[selected_label].get("id") != record.get("id"):
            st.session_state.current_record = record_lookup[selected_label]
            st.session_state.active_code = None
            st.rerun()
        is_signed = record.get("status") == "Approved"
        badge_col.markdown(
            '<div class="nc-badge-row" style="justify-content:flex-end;padding-bottom:.35rem">'
            + badge("Note SIGNED" if is_signed else "Note DRAFT", "green" if is_signed else "amber")
            + badge("ICD-10-CM · CPT/HCPCS", "blue")
            + badge("CMS-HCC V28 (demo map)", "violet")
            + "</div>",
            unsafe_allow_html=True,
        )

    insights = get_record_insights(record)
    st.session_state.detected_insights = insights
    if st.session_state.current_record is None or st.session_state.current_record.get("id") != record.get("id"):
        st.session_state.current_record = record
    ensure_code_results(record, insights)
    code_results = encounter_code_results(record, st.session_state.code_results)
    code_error = st.session_state.get("code_generation_error", "")
    if code_error:
        st.warning(code_error)
    if not is_signed:
        render_banner(
            "Coding a draft note",
            "Codes can be reviewed now, but final coding should follow the signed SOAP note.",
            tone="warn",
            icon="&#9888;",
        )
    can_code = can("Codes", "Edit")
    if not can_code:
        render_banner(
            "Read-only coding view",
            "Your role can review codes but not change them (Codes · Edit).",
            tone="warn",
            icon="&#128274;",
        )
    render_superbill_import(record, can_code)

    rows = build_code_rows(record, code_results)
    if rows and st.session_state.active_code not in {row["key"] for row in rows}:
        st.session_state.active_code = rows[0]["key"]
    pre_checks, post_checks = coding_validation(record, rows)
    render_validation_card("Pre-coding validation", "Documentation readiness — is this encounter codable?", pre_checks, "pre")
    table_col, evidence_col = st.columns([6.6, 3.4], gap="medium")
    with table_col:
        with st.container(border=True):
            card_title(
                "Encounter Codes",
                "Matcher suggestions, extracted services and E/M validation",
                trailing=badge(f"{len(rows)} candidates", "violet"),
            )
            if rows:
                selected_keys = _render_code_table(record, rows)
                with st.container(horizontal=True, gap="small"):
                    st.button(
                        "Accept selected",
                        key="codes_bulk_accept",
                        type="primary",
                        disabled=not selected_keys or not can_code,
                        on_click=set_code_decision,
                        args=(record, selected_keys, "Accepted"),
                    )
                    st.button(
                        "Reject selected",
                        key="codes_bulk_reject",
                        type="secondary",
                        disabled=not selected_keys or not can_code,
                        on_click=set_code_decision,
                        args=(record, selected_keys, "Rejected"),
                    )
                    if st.button(
                        "↻ Re-run matcher", key="codes_regenerate", type="tertiary", disabled=not insights or not can_code
                    ):
                        st.session_state.code_record_id = None
                        st.rerun()
            else:
                st.markdown(
                    '<div class="code-empty">No diagnosis or procedure codes are available for this encounter.</div>',
                    unsafe_allow_html=True,
                )
    with evidence_col:
        active_row = next((row for row in rows if row["key"] == st.session_state.active_code), None)
        if active_row:
            _render_evidence_panel(record, active_row, is_signed)
        else:
            with st.container(border=True):
                card_title("Evidence & Provenance")
                st.caption("Select a code to see its supporting documentation.")

    statuses = [row["status"] for row in rows]
    hccs = sorted({row["hcc"] for row in rows if row["hcc"] and row["status"] not in {"Rejected"}})
    em_code = next((row["code"] for row in rows if row["code"] in EM_MDM_LEVELS and row["status"] != "Rejected"), None)
    with st.container(border=True):
        card_title("Coding Summary", "Encounter-level coding and risk capture")
        render_tiles(
            [
                ("Accepted", statuses.count("Accepted")),
                ("Pending query", statuses.count("Query sent")),
                ("Rejected", statuses.count("Rejected")),
                ("HCCs captured", f"{len(hccs)} · {', '.join(hccs)}" if hccs else "0"),
                ("E/M level", em_code or "—"),
                ("MDM", EM_MDM_LEVELS.get(em_code, "—") if em_code else "—"),
            ]
        )
        audit = record.get("coding_audit") or []
        last = audit[-1] if audit else None
        st.markdown(
            f'<div class="nc-meta" style="margin-top:.5rem">{"Last action: " + escape(last["action"]) + " " + escape(last["code"]) + " · " + escape(last["at"]) if last else "No coder actions yet"}'
            " · Every status transition is retained in the encounter audit log.</div>",
            unsafe_allow_html=True,
        )
    post_summary = render_validation_card(
        "Post-coding validation", "Code-set integrity and claim scrub — is this ready to bill?", post_checks, "post"
    )
    if post_summary["status"] == "fail":
        st.caption("Billing will hold this encounter until the blocking post-coding issues are resolved.")
    with st.expander("Detected clinical context and raw matcher output"):
        if insights:
            render_grouped_insights(insights)
        icd_tab, cpt_tab, audit_tab = st.tabs(["ICD-10 matcher output", "CPT/HCPCS matcher output", "Coding audit log"])
        with icd_tab:
            render_code_results(code_results.get("icd10", []), "No ICD-10 suggestions are available.")
        with cpt_tab:
            render_code_results(code_results.get("cpt", []), "No CPT/HCPCS suggestions are available.")
        with audit_tab:
            if audit:
                st.dataframe(pd.DataFrame(audit), hide_index=True, width="stretch")
            else:
                st.caption("No coder actions recorded yet.")
    st.caption(
        "Coding suggestions require professional validation before billing, claim submission, or addition to the legal health record."
    )
