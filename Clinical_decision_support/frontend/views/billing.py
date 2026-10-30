"""Billing & RCM: performed-service guardrail, draft bill and hand-off to claim generation."""

import json
import re
from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st

from frontend.claim_generation_page import build_ambient_bill, register_generated_bill
from frontend.clinical_text import best_excerpt
from frontend.coding_validation import summarize as summarize_checks
from frontend.ehr_chart import active_drug_allergies, age_from_dob, chart_rows, format_date, latest_first, primary_coverage
from frontend.encounter_pipeline import (
    _procedure_status_inputs,
    build_estimated_hospital_bill,
    classify_procedure_status,
    is_explicitly_performed,
)
from frontend.settings import BILLING_NOTICE
from frontend.ui.components import (
    badge,
    card_title,
    initials,
    page_header,
    page_title,
    render_banner,
    render_html_table,
    render_tiles,
)
from frontend.ui.data import record_chart, record_patient_id
from frontend.ui.shell import render_page
from frontend.ui.state import can, current_user, go_to, log_action, open_record, save_record
from frontend.views.coding import (
    VALIDATION_TONES,
    build_code_rows,
    coding_validation,
    encounter_code_results,
    record_documentation,
)


def billing_guardrail_rows(record):
    """Classify each CPT candidate as billable/blocked using performed-status evidence."""
    cpt_results = list(encounter_code_results(record).get("cpt") or [])
    documentation = record_documentation(record)
    decisions = record.get("code_decisions") or {}
    note_text = str(record.get("summary") or "").lower()
    rows = []
    for candidate in cpt_results:
        if not isinstance(candidate, dict):
            continue
        code = str(candidate.get("CPT/HCPCS Code") or "").strip()
        term = str(candidate.get("Extracted Procedure") or "")
        if not code:
            continue
        status_procedure, status_documentation = _procedure_status_inputs(term, documentation)
        procedure_status = (
            classify_procedure_status(status_procedure, status_documentation) if classify_procedure_status else "unknown"
        )
        performed = bool(is_explicitly_performed and is_explicitly_performed(status_procedure, status_documentation))
        decision = decisions.get(f"CPT:{code}", {}).get("status")
        rejected = decision == "Rejected"
        from_superbill = candidate.get("source") == "ocr_superbill"
        words = [word for word in re.findall(r"[a-z]+", term.lower()) if len(word) > 3]
        in_note = any(word in note_text for word in words)
        if rejected:
            guardrail, tone, reason = "EXCLUDED", "gray", "Rejected by coder"
        elif performed:
            guardrail, tone, reason = "BILLABLE", "green", "Performed and documented"
        elif from_superbill and decision == "Accepted":
            guardrail, tone, reason = "BILLABLE", "green", "Provider superbill (OCR), accepted by coder"
            procedure_status = "superbill"
        elif from_superbill:
            guardrail, tone, reason = "UNVERIFIED", "amber", "Superbill line awaiting coder acceptance"
            procedure_status = "superbill"
        elif procedure_status == "unknown":
            guardrail, tone, reason = "UNVERIFIED", "amber", "Performed status could not be verified"
        else:
            guardrail, tone, reason = "BLOCKED", "red", "Not documented as performed"
        held = guardrail in {"BLOCKED", "UNVERIFIED"}
        override = (record.get("billing_overrides") or {}).get(code) if held else None
        if override:
            guardrail, tone = "BILLABLE", "green"
            reason = f"Confirmed performed by {override.get('by') or 'coder'}"
        rows.append(
            {
                "candidate": candidate,
                "code": code,
                "service": candidate.get("Matched Procedure/Service") or term,
                "order": {
                    "performed": "Performed",
                    "ordered_not_performed": "Ordered",
                    "planned": "Planned",
                    "recommended": "Recommended",
                    "discussed": "Discussed",
                    "historical": "Historical",
                    "negated": "Not performed",
                    "superbill": "Superbill",
                }.get(procedure_status, "Unknown"),
                "evidence": best_excerpt(term, documentation, max_chars=90) or "No performed evidence found",
                "documentation": ("Signed note" if record.get("status") == "Approved" else "Draft note")
                if in_note
                else "Transcript only",
                "guardrail": guardrail,
                "tone": tone,
                "reason": reason,
                # Held by the guardrail but released by a coder; shown as an override choice.
                "overridden": bool(override),
                "overridable": held,
            }
        )
    return rows


def _set_billing_overrides(record, key):
    """Persist the coder's 'confirmed performed' choices for held lines on the encounter."""
    selected = set(st.session_state.get(key) or [])
    previous = record.get("billing_overrides") or {}
    stamp = {"by": current_user()["username"], "at": datetime.now().strftime("%d %b %Y %H:%M")}
    record["billing_overrides"] = {code: previous.get(code) or stamp for code in selected}
    added, removed = selected - set(previous), set(previous) - selected
    save_record(record)
    if added or removed:
        log_action(
            "billing.override",
            f"{record.get('id')}: confirmed {', '.join(sorted(added)) or '—'}; withdrawn {', '.join(sorted(removed)) or '—'}",
        )


def _patient_key(record):
    return record_patient_id(record) or f"local:{str(record.get('name', '')).strip().lower()}"


def _billing_status(record):
    rows = billing_guardrail_rows(record)
    if record.get("bill_generated_at"):
        return "Bill drafted", "green"
    if not rows:
        return "Not coded", "gray"
    blocked = sum(row["guardrail"] in {"BLOCKED", "UNVERIFIED"} for row in rows)
    if blocked:
        return f"{blocked} line{'s' if blocked > 1 else ''} held", "red"
    if record.get("status") != "Approved":
        return "Awaiting signature", "amber"
    return "Ready to bill", "amber"


def _render_patient_directory(records):
    patients = {}
    for record in records:
        patients.setdefault(_patient_key(record), []).append(record)
    with st.container(border=True):
        card_title("Patient Directory", f"{len(patients)} patients · local consultations linked to the EHR")
        search = (
            st.text_input("Search", placeholder="Search name / MRN / ID", key="billing_search", label_visibility="collapsed")
            .strip()
            .lower()
        )
        view = (
            st.segmented_control(
                "Filter", ["All", "OPD", "IPD", "Unsynced"], default="All", key="billing_filter", label_visibility="collapsed"
            )
            or "All"
        )
        shown = 0
        for key, patient_records in patients.items():
            latest = patient_records[0]
            chart = record_chart(latest)
            coverage = primary_coverage(chart) if chart else {}
            unsynced = any(not r.get("ehr_encounter_id") for r in patient_records)
            types = {"IPD" if str(r.get("ehr_encounter_type", "")).lower().startswith("inp") else "OPD" for r in patient_records}
            haystack = f"{latest.get('name', '')} {key} {latest.get('mrn', '')} {' '.join(r.get('id', '') for r in patient_records)}".lower()
            if search and search not in haystack:
                continue
            if (view in {"OPD", "IPD"} and view not in types) or (view == "Unsynced" and not unsynced):
                continue
            shown += 1
            is_selected = st.session_state.billing_patient_key == key
            with st.container(key=f"dir_{'on' if is_selected else 'off'}_{re.sub(r'[^A-Za-z0-9]', '_', key)}", border=True):
                info_col, action_col = st.columns([4, 1.4], vertical_alignment="center")
                meta = " · ".join(
                    part for part in (record_patient_id(latest) or "Local only", coverage.get("Payer_Name", "")) if part
                )
                info_col.markdown(
                    f'<div class="nc-patient"><div class="nc-initials">{escape(initials(latest.get("name")))}</div>'
                    f"<div><b>{escape(latest.get('name', ''))}</b><small>{escape(meta)}</small></div></div>"
                    + (f'<div style="margin-top:.3rem">{badge("Unsynced", "amber")}</div>' if unsynced else ""),
                    unsafe_allow_html=True,
                )
                action_col.button(
                    "✓" if is_selected else "Open",
                    key=f"billing_open_{key}",
                    type="tertiary",
                    on_click=lambda k=key, r=latest: st.session_state.update(
                        billing_patient_key=k, billing_record_id=r.get("id")
                    ),
                )
        if not shown:
            st.caption("No patients match this filter.")
    return patients


def _render_patient_banner(record, chart):
    patient = (chart or {}).get("patient") or {}
    coverage = primary_coverage(chart) if chart else {}
    allergies = ", ".join(str(row.get("Substance")) for row in active_drug_allergies(chart)) if chart else ""
    synced = bool(record.get("ehr_encounter_id"))
    age = record.get("age") or age_from_dob(patient.get("DOB"))
    badges = []
    if coverage:
        badges.append(badge(f"{coverage.get('Payer_Name', 'Payer')} · {coverage.get('Plan_Type', '')}".strip(" ·"), "blue"))
        badges.append(
            badge(
                f"Eligibility {str(coverage.get('Status', 'unknown')).upper()}",
                "green" if str(coverage.get("Status", "")).lower() == "active" else "amber",
            )
        )
    else:
        badges.append(badge("No coverage on file", "gray"))
    if allergies:
        badges.append(badge(f"Allergy: {allergies}", "red"))
    badges.append(badge("EHR synced" if synced else "Unsynced · local only", "green" if synced else "amber"))
    st.markdown(
        '<div class="nc-banner-card">'
        f'<div class="nc-patient"><div class="nc-initials lg">{escape(initials(record.get("name")))}</div>'
        f'<div><b style="font-size:.95rem">{escape(record.get("name", ""))}</b><small>{escape(record_patient_id(record) or record.get("id", ""))} · {escape(str(age))}y · {escape(record.get("gender", ""))}</small></div></div>'
        f'<div class="nc-badge-row">{"".join(badges)}</div></div>',
        unsafe_allow_html=True,
    )


def _render_encounter_list(patient_records, chart, selected_id):
    ehr_encounters = latest_first(chart_rows(chart, "Encounters"), "Date") if chart else []
    local_encounter_ids = {r.get("ehr_encounter_id") for r in patient_records}
    history = [row for row in ehr_encounters if row.get("Encounter_ID") not in local_encounter_ids][:4]
    claims = {row.get("Encounter_ID"): row for row in chart_rows(chart, "Billing_Claims")} if chart else {}
    with st.container(border=True):
        card_title(f"Encounters ({len(patient_records) + len(history)})", "Select an encounter to review its charges")
        widths = [1.5, 1.4, 1.2, 1.6, 1.2, 1.4, 1.3]
        header = st.columns(widths)
        for col, label in zip(header, ("Encounter", "Type", "Date", "Provider", "Note", "Billing status", "Action"), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        for record in patient_records:
            is_selected = record.get("id") == selected_id
            billing_text, billing_tone = _billing_status(record)
            with st.container(key=f"encrow_{'sel' if is_selected else 'off'}_{record.get('id')}"):
                cols = st.columns(widths, vertical_alignment="center")
                cols[0].markdown(
                    f'<div class="nc-cell"><b>{escape(record.get("ehr_encounter_id") or record.get("id"))}</b></div>',
                    unsafe_allow_html=True,
                )
                cols[1].markdown(
                    f'<div class="nc-cell">{escape(record.get("ehr_encounter_type") or "Office visit")}</div>',
                    unsafe_allow_html=True,
                )
                cols[2].markdown(
                    f'<div class="nc-cell muted">{escape(format_date(record.get("date")))}</div>', unsafe_allow_html=True
                )
                cols[3].markdown(f'<div class="nc-cell muted">{escape(record.get("doctor", ""))}</div>', unsafe_allow_html=True)
                cols[4].markdown(
                    badge("Signed", "green") if record.get("status") == "Approved" else badge("SOAP draft", "amber"),
                    unsafe_allow_html=True,
                )
                cols[5].markdown(badge(billing_text, billing_tone), unsafe_allow_html=True)
                cols[6].button(
                    "Reviewing" if is_selected else "Review charges",
                    key=f"billing_review_{record.get('id')}",
                    type="secondary",
                    disabled=is_selected,
                    on_click=lambda r=record: st.session_state.update(billing_record_id=r.get("id")),
                )
        for row in history:
            claim = claims.get(row.get("Encounter_ID"), {})
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].markdown(
                f'<div class="nc-cell"><b>{escape(str(row.get("Encounter_ID", "")))}</b></div>', unsafe_allow_html=True
            )
            cols[1].markdown(f'<div class="nc-cell">{escape(str(row.get("Type", "")))}</div>', unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell muted">{escape(format_date(row.get("Date")))}</div>', unsafe_allow_html=True)
            cols[3].markdown(f'<div class="nc-cell muted">{escape(str(row.get("Provider", "")))}</div>', unsafe_allow_html=True)
            cols[4].markdown(
                badge(row.get("Status") or "—", "green" if str(row.get("Status")).lower() == "completed" else "gray"),
                unsafe_allow_html=True,
            )
            claim_status = claim.get("Claim_Status")
            cols[5].markdown(
                badge(
                    f"Claim {claim_status}" if claim_status else "No claim",
                    "green" if str(claim_status).lower() == "paid" else "gray",
                ),
                unsafe_allow_html=True,
            )
            cols[6].markdown('<span class="nc-meta">EHR history</span>', unsafe_allow_html=True)


def show_billing():
    render_page("billing")
    records = st.session_state.records
    if not records:
        page_title("BILLING & RCM", "Charge Review & Billing Guardrail", "Only services performed AND documented reach the bill")
        st.info("No consultations yet. Billing starts from a coded consultation.")
        return
    record = next((r for r in records if r.get("id") == st.session_state.billing_record_id), None)
    if record is None:
        current = st.session_state.current_record
        record = next((r for r in records if current and r.get("id") == current.get("id")), records[0])
        st.session_state.billing_record_id = record.get("id")
    if st.session_state.billing_patient_key != _patient_key(record):
        st.session_state.billing_patient_key = _patient_key(record)

    guardrail = billing_guardrail_rows(record)
    billable = [row for row in guardrail if row["guardrail"] == "BILLABLE"]
    held = [row for row in guardrail if row["guardrail"] in {"BLOCKED", "UNVERIFIED"}]
    encounter_type = "IPD" if str(record.get("ehr_encounter_type", "")).lower().startswith("inp") else "OPD"
    attest_key = f"billing_attest_{record.get('id')}"
    bill_rows, bill_total, unpriced = (
        build_estimated_hospital_bill([row["candidate"] for row in billable], encounter_type) if billable else ([], 0.0, [])
    )
    # Chargemaster price for every coded line, billable or held, so held services still show what they cost.
    coded = [row for row in guardrail if row["guardrail"] != "EXCLUDED"]
    prices = {
        line["CPT/HCPCS Code"]: line
        for line in (build_estimated_hospital_bill([row["candidate"] for row in coded], encounter_type)[0] if coded else [])
    }
    held_value = sum(
        float(str(prices.get(row["code"], {}).get("Gross Charge", "")).replace("$", "").replace(",", "") or 0)
        for row in held
        if prices.get(row["code"], {}).get("Pricing Status") == "Priced"
    )
    code_rows = build_code_rows(record, encounter_code_results(record)) if guardrail else []
    post_summary = summarize_checks(coding_validation(record, code_rows)[1]) if code_rows else None
    coding_blocked = bool(post_summary and post_summary["status"] == "fail")

    actions = page_header(
        "BILLING & RCM",
        "Charge Review & Billing Guardrail",
        "Only services performed AND documented reach the bill",
        action_weight=4.2,
    )
    export = {
        "encounter_id": record.get("ehr_encounter_id") or record.get("id"),
        "patient": record.get("name"),
        "encounter_type": encounter_type,
        "lines": bill_rows,
        "gross_total": bill_total,
        "held_lines": [{"code": row["code"], "reason": row["reason"]} for row in held],
        "notice": BILLING_NOTICE,
    }
    actions.download_button(
        "Download draft (JSON)",
        data=json.dumps(export, indent=2),
        file_name=f"bill_{export['encounter_id']}.json",
        mime="application/json",
        icon=":material/download:",
        disabled=not bill_rows,
        key="billing_download",
    )

    def send_to_rcm():
        selected_codes = [row["code"] for row in billable]
        estimate = {
            "record_id": record.get("id"),
            "encounter_type": encounter_type,
            "code_results_signature": None,
            "selected_cpt_codes": selected_codes,
            "rows": bill_rows,
            "total": bill_total,
            "unpriced_codes": unpriced,
        }
        st.session_state.estimated_hospital_bill = estimate
        # Saved with the encounter so Claim Generation finds it in any later session.
        record["bill_estimate"] = estimate
        register_generated_bill(build_ambient_bill(record, estimate, encounter_code_results(record)), source=encounter_type)
        record["bill_generated_at"] = datetime.now().strftime("%d %b %Y %H:%M")
        record["bill_released_by"] = current_user()["username"]
        save_record(record)
        log_action("claim.send", f"{record.get('id')}: {', '.join(selected_codes)} · ${bill_total:,.2f}")
        go_to("claim_generation")

    actions.button(
        "Send to RCM (claim draft) ➤",
        key="accent_billing_send",
        on_click=send_to_rcm,
        disabled=not bill_rows
        or not st.session_state.get(attest_key, False)
        or coding_blocked
        or not can("RCM Workspace", "Approve"),
        help=(
            "Resolve blocking post-coding validation issues first."
            if coding_blocked
            else None
            if can("RCM Workspace", "Approve")
            else "Your role cannot release claims (RCM Workspace · Approve)."
        ),
    )

    directory_col, main_col = st.columns([2.5, 7.5], gap="medium")
    with directory_col:
        patients = _render_patient_directory(records)
    patient_records = patients.get(_patient_key(record), [record])
    if record not in patient_records:
        record = patient_records[0]
    with main_col:
        chart = record_chart(record)
        _render_patient_banner(record, chart)
        _render_encounter_list(patient_records, chart, record.get("id"))
        if not guardrail:
            render_banner(
                "This encounter has not been coded yet",
                "Run the Coding & HCC workspace to extract CPT/HCPCS candidates.",
                tone="warn",
                icon="&#9888;",
            )
            st.button(
                "Open Coding & HCC →", key="billing_open_coding", type="primary", on_click=open_record, args=(record, "codes")
            )
            return
        if post_summary:
            label, tone = VALIDATION_TONES[post_summary["status"]]
            banner_tone = {"pass": "ok", "warn": "warn", "fail": "danger"}[post_summary["status"]]
            detail = (
                "Send to RCM is disabled until the blocking issues are resolved in Coding & HCC."
                if coding_blocked
                else f"Validation score {post_summary['score']}/100 · review details in Coding & HCC."
            )
            render_banner(
                f"Post-coding validation: {post_summary['label']}",
                detail,
                tone=banner_tone,
                icon="&#10003;" if post_summary["status"] == "pass" else "&#9888;",
                trailing=badge(label, tone),
            )
            if coding_blocked:
                st.button(
                    "Resolve in Coding & HCC →",
                    key="billing_fix_coding",
                    type="secondary",
                    on_click=open_record,
                    args=(record, "codes"),
                )
        if held:
            codes = ", ".join(row["code"] for row in held)
            render_banner(
                f"{len(held)} line{'s' if len(held) > 1 else ''} held — {codes} excluded until performed evidence is documented",
                "Held lines re-enter the bill once the note documents the service as performed.",
                tone="danger",
                icon="&#9888;",
                trailing=badge("BLOCKED", "red"),
            )
        with st.container(border=True):
            card_title(
                "Performed-Service Guardrail",
                f"{record.get('ehr_encounter_id') or record.get('id')} · order, evidence and documentation reconciliation",
                trailing=badge(f"{len(billable)} billable · {len(held)} held", "amber" if held else "green"),
            )
            render_html_table(
                ["CPT", "Service", "Order", "Performed evidence", "Documentation", "Charge", "Guardrail"],
                [
                    [
                        f"<code>{escape(row['code'])}</code>",
                        escape(prices.get(row["code"], {}).get("Service Description") or row["service"]),
                        escape(row["order"]),
                        escape(row["evidence"]),
                        escape(row["documentation"]),
                        escape(str(prices.get(row["code"], {}).get("Gross Charge") or "—")),
                        badge(row["guardrail"], row["tone"]),
                    ]
                    for row in guardrail
                ],
                ["blocked" if row["guardrail"] == "BLOCKED" else "" for row in guardrail],
            )
            overridable = [row for row in guardrail if row["overridable"]]
            if overridable:
                override_key = f"billing_override_{record.get('id')}"
                if override_key not in st.session_state:
                    st.session_state[override_key] = [row["code"] for row in overridable if row["overridden"]]
                labels = {
                    row["code"]: f"{row['code']} · {prices.get(row['code'], {}).get('Service Description') or row['service']}"
                    f" · {prices.get(row['code'], {}).get('Gross Charge') or 'Unpriced'}"
                    for row in overridable
                }
                st.multiselect(
                    "Coder override: confirm held services that were actually performed",
                    options=list(labels),
                    format_func=labels.get,
                    key=override_key,
                    on_change=_set_billing_overrides,
                    args=(record, override_key),
                    disabled=not can("RCM Workspace", "Edit"),
                    placeholder="Select services to release to the bill",
                    help="Use only when the service was performed but the note does not say so. "
                    "Your username and time are saved with the encounter and in the audit log.",
                )
            st.checkbox(
                "I reviewed these CPT/HCPCS codes and confirm the services were performed and documented for this encounter.",
                key=attest_key,
                disabled=not can("RCM Workspace", "Edit"),
            )
        with st.container(border=True):
            coverage = primary_coverage(chart) if chart else {}
            bill_id = f"BILL-{re.sub(r'[^A-Za-z0-9]', '', str(record.get('ehr_encounter_id') or record.get('id')))[-6:]}"
            card_title(
                f"Bill {bill_id}",
                " · ".join(
                    part
                    for part in (
                        "Professional claim",
                        coverage.get("Payer_Name", ""),
                        coverage.get("Plan_Type", ""),
                        "Outpatient" if encounter_type == "OPD" else "Inpatient",
                    )
                    if part
                ),
                trailing=badge(
                    "DRAFTED" if record.get("bill_generated_at") else "DRAFT",
                    "green" if record.get("bill_generated_at") else "amber",
                ),
            )
            dx_codes = [
                str(item.get("ICD-10 Code"))
                for item in (record.get("code_results") or {}).get("icd10", [])
                if isinstance(item, dict) and item.get("ICD-10 Code")
            ]
            render_tiles(
                [
                    ("Gross", f"${bill_total:,.2f}"),
                    ("Billable lines", len(bill_rows)),
                    ("Held lines", len(held)),
                    ("Held value", f"${held_value:,.2f}"),
                    ("Unpriced", len(unpriced)),
                    ("Diagnosis codes", ", ".join(dx_codes[:3]) or "—"),
                ]
            )
            if bill_rows:
                st.dataframe(pd.DataFrame(bill_rows), hide_index=True, width="stretch")
            if held:
                st.markdown('<div class="nc-label" style="margin-top:.6rem">Held — not in gross</div>', unsafe_allow_html=True)
                st.dataframe(
                    pd.DataFrame(
                        [{**prices.get(row["code"], {"CPT/HCPCS Code": row["code"]}), "Held reason": row["reason"]} for row in held]
                    ),
                    hide_index=True,
                    width="stretch",
                )
            if unpriced:
                render_banner(
                    "Unpriced codes: " + ", ".join(unpriced),
                    "No chargemaster rate was found; they are excluded from the gross.",
                    tone="warn",
                    icon="&#9888;",
                )
            st.caption(BILLING_NOTICE)
