"""Supplemental submission: validated records, batch lifecycle, payer files, acknowledgements and corrections."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from app.hedis import supplemental
from app.hedis.config import PAYERS, PRACTITIONERS, SOURCE_CLASS_LABELS
from frontend.ui.components import card_title, render_html_table, render_kpi_card, render_tiles
from frontend.ui.shell import render_page
from frontend.views.hedis.common import actor, allowed, code, flag_disabled_notice, fmt_date, guard, header, run, status_badge

MODULE = "Supplemental Submission"
STEPS = ("DRAFT", "SIGNED", "SUBMITTED", "ACKNOWLEDGED")


def show_hedis_supplemental():
    render_page("hedis_supplemental")
    if flag_disabled_notice():
        return
    header("Supplemental submission", "Data the payer cannot see in claims — validated, signed by a second person, transmitted and reconciled")
    view = supplemental.view()
    _kpis(view)
    payer_cols = st.columns(len(PAYERS), gap="medium")
    for col, (key, info) in zip(payer_cols, view["payers"].items(), strict=True):
        with col, st.container(border=True):
            card_title(info["name"], f"{info['product']} · file {info['format']}")
            render_tiles([("Ready", info["ready"]), ("In flight", info["in_draft"] + info["pending_ack"]), ("Accepted", info["accepted"]),
                          ("Needs correction", info["needs_correction"])])
            if st.button(f"Build batch ({info['ready']})", key=f"hsup_build_{key}", type="primary", disabled=not info["ready"] or not allowed(MODULE, "Edit"),
                         width="stretch") and guard(MODULE, "Edit", "build a supplemental batch"):
                batch = run(supplemental.build_batch, key, actor(), success="Draft batch built — it needs a second person to sign")
                if batch:
                    st.session_state.hsup_batch = batch["batch_id"]
                    st.rerun()
    tabs = st.tabs(["Batches", f"Records ({len(view['records'])})", "Corrections"])
    with tabs[0]:
        _batches(view)
    with tabs[1]:
        _records(view)
    with tabs[2]:
        _corrections(view)


def _kpis(view: dict) -> None:
    records = view["records"]
    blocked = sum(bool(r["errors"]) for r in records)
    warned = sum(bool(r["warnings"]) and r["status"] != "ACCEPTED" for r in records)
    cols = st.columns(4)
    for col, args in zip(cols, (
        ("Supplemental records", str(len(records)), "lav", "Implied by compliant registry rows"),
        ("Accepted by payers", f"{view['accepted_rate'] or 0}%", "mint", f"{sum(r['status'] == 'ACCEPTED' for r in records)} record(s) credited"),
        ("Blocked by validation", str(blocked), "rose" if blocked else "mint", "Fail the payer file spec"),
        ("Overdue acknowledgements", str(len(view["overdue_acks"])), "rose" if view["overdue_acks"] else "sky",
         f"{warned} record(s) with a roster warning"),
    ), strict=True):
        with col:
            render_kpi_card(*args)


def _batches(view: dict) -> None:
    batches = view["batches"]
    if not batches:
        st.info("No batches yet. Build one per payer above, or let the Supplemental preparer (L1 agent) assemble the drafts.")
        return
    render_html_table(["Batch", "Payer", "Status", "Records", "Built by", "Signed by", "Submitted", "Acknowledgement"],
                      [[code(b["batch_id"]), escape(PAYERS[b["payer"]].name), status_badge(b["status"]), str(len(b["records"])),
                        escape(b["built_by"]), escape(b["signed_by"] or "—"), escape(fmt_date(b["submitted_at"])),
                        escape(f"{b['ack']['accepted']} accepted · {b['ack']['rejected']} rejected" if b["ack"] else "—")] for b in batches])
    ids = [b["batch_id"] for b in batches]
    if st.session_state.get("hsup_batch") not in ids:
        st.session_state.hsup_batch = ids[0]
    chosen = st.selectbox("Open batch", ids, key="hsup_batch")
    _batch_detail(next(b for b in batches if b["batch_id"] == chosen))


def _batch_detail(batch: dict) -> None:
    key = batch["batch_id"]
    status = batch["status"]
    with st.container(border=True):
        card_title(f"{key} · {PAYERS[batch['payer']].name}", f"{batch['format']} · {len(batch['records'])} record(s) · "
                   f"{batch['resubmissions']} resubmission(s)", trailing=status_badge(status))
        progress = {"DRAFT": 0, "SIGNED": 1, "SUBMITTED": 2}.get(status, len(STEPS) if status in ("ACKNOWLEDGED", "PARTIAL", "REJECTED") else 0)
        st.markdown('<div class="nc-stepper">' + "".join(
            f'<div class="nc-step {"done" if i < progress else "current" if i == progress else ""}">'
            f'<i>{"&#10003;" if i < progress else i + 1}</i>{name.title()}</div>' for i, name in enumerate(STEPS)) + "</div>", unsafe_allow_html=True)
        cols = st.columns(4)
        if status == "DRAFT":
            if cols[0].button("Sign batch", key=f"hsup_sign_{key}", type="primary", disabled=not allowed(MODULE, "Approve"),
                              help="Authorized signatory; must not be the person (or agent) who built it."):
                if guard(MODULE, "Approve", "sign a supplemental batch") and run(supplemental.sign_batch, key, actor(), success="Batch signed"):
                    st.rerun()
            if cols[1].button("Discard draft", key=f"hsup_discard_{key}", disabled=not allowed(MODULE, "Edit")):
                if guard(MODULE, "Edit", "discard a draft batch") and run(supplemental.discard_batch, key, actor(), "Discarded from the console",
                                                                         success="Draft discarded"):
                    st.rerun()
        elif status == "SIGNED":
            if cols[0].button("Transmit to payer", key=f"hsup_submit_{key}", type="primary", disabled=not allowed(MODULE, "Approve")):
                if guard(MODULE, "Approve", "transmit a supplemental batch") and run(supplemental.submit_batch, key, actor(), success="File transmitted"):
                    st.rerun()
        elif status == "SUBMITTED":
            if cols[0].button("Load payer acknowledgement", key=f"hsup_ack_{key}", type="primary", disabled=not allowed(MODULE, "Edit"),
                              help="Demo: generates the payer's response file (roster rule enforced) and ingests it."):
                if guard(MODULE, "Edit", "ingest an acknowledgement"):
                    text = run(supplemental.simulate_ack, key)
                    if text and run(supplemental.ingest_ack, key, text, actor(), success="Acknowledgement ingested"):
                        st.rerun()
            uploaded = cols[1].file_uploader("Or upload the payer's ack file", key=f"hsup_ackfile_{key}", label_visibility="collapsed")
            if uploaded is not None and cols[1].button("Ingest uploaded ack", key=f"hsup_ackup_{key}") and guard(MODULE, "Edit", "ingest an acknowledgement"):
                if run(supplemental.ingest_ack, key, uploaded.getvalue().decode("utf-8", "replace"), actor(), success="Acknowledgement ingested"):
                    st.rerun()
        text, control = supplemental.render_file(batch)
        cols[3].download_button("Payer file", data=text, file_name=f"{key}.{'txt' if batch['format'] == 'PIPE-v2' else 'csv'}",
                                key=f"hsup_dl_{key}", icon=":material/download:")
        render_tiles([("Control records", control["records"]), ("Detail SHA-256", control["detail_sha256"][:16]),
                      ("Signature", (batch["signature"] or "—")[:16]), ("Written to", (batch["file"] or "—").split("\\")[-1].split("/")[-1])])
        st.dataframe(pd.DataFrame([{"Record": r["record_key"], "Member": r["member_id"], "Measure": r["measure_id"], "Service date": r["service_date"],
                                    "Code": f"{r['code_system']} {r['code']}", "Value": r["value"], "Value 2": r["value2"], "NPI": r["rendering_npi"],
                                    "Source": SOURCE_CLASS_LABELS[r["source_class"]], "PSV": r["psv"], "Resubmission of": r.get("resubmission_of") or ""}
                                   for r in batch["records"]]), hide_index=True, width="stretch")


def _records(view: dict) -> None:
    st.dataframe(pd.DataFrame([{
        "Record": r["record_key"], "Payer": r["payer"], "Member": r["member_id"], "Measure": r["measure_id"], "Status": r["status"],
        "Service date": r["service_date"], "Code": f"{r['code_system']} {r['code']}", "NPI": r["rendering_npi"],
        "Source": SOURCE_CLASS_LABELS[r["source_class"]], "PSV": r["psv"],
        "Errors": "; ".join(f"{e['code']}: {e['detail']}" for e in r["errors"]),
        "Warnings": "; ".join(f"{w['code']}: {w['detail']}" for w in r["warnings"]),
        "Payer reason": f"{r['reason_code']} {r['reason']}" if r["reason_code"] else "",
    } for r in view["records"]]), hide_index=True, width="stretch", height=min(560, 38 + 35 * max(len(view["records"]), 1)))
    st.caption("Each record's key is a hash of payer, member, measure, code, service date and value: the same evidence can never be in flight twice.")


def _corrections(view: dict) -> None:
    pending = [r for r in view["records"] if r["status"] == "NEEDS_CORRECTION"]
    if not pending:
        st.success("No payer rejection is waiting for a correction.")
        return
    st.caption("Payer-rejected records. The Rejection resolver (L2 agent) proposes fixes in the AI agent console; you can also correct here.")
    for record in pending:
        with st.container(border=True):
            card_title(f"{record['member_id']} · {record['measure_id']}", f"{record['reason_code']}: {record['reason']}", trailing=status_badge(record["status"]))
            group = PRACTITIONERS.get(record["provider"], {}).get("group_npi", "")
            with st.form(f"hsup_fix_{record['record_key']}"):
                c1, c2 = st.columns([1, 2])
                value = c1.text_input("Rendering NPI", value=group, key=f"hsup_npi_{record['record_key']}")
                reason = c2.text_input("Reason", value=f"{record['reason_code']}: bill under the practice group NPI on the payer roster",
                                       key=f"hsup_reason_{record['record_key']}")
                if st.form_submit_button("Apply correction", disabled=not allowed(MODULE, "Edit")) and guard(MODULE, "Edit", "correct a record"):
                    if run(supplemental.apply_correction, record["record_key"], "rendering_npi", value.strip(), actor(), reason,
                           success="Corrected — ready to resubmit"):
                        st.rerun()
