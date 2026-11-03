"""Payer reconciliation: compare the payer's member-level file with the registry, route differences, manage disputes."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from app.hedis import reconciliation
from app.hedis.config import PAYERS
from frontend.ui.components import badge, card_title, render_html_table, render_kpi_card
from frontend.ui.shell import render_page
from frontend.views.hedis.common import actor, allowed, bars, code, flag_disabled_notice, fmt_date, guard, header, run, status_badge

MODULE = "Payer Reconciliation"


def show_hedis_reconciliation():
    render_page("hedis_reconciliation")
    if flag_disabled_notice():
        return
    header("Payer reconciliation", "Every member and measure compared with the payer's file — root cause, owner, action and rate impact")
    view = reconciliation.view()
    payer = st.segmented_control("Payer", list(PAYERS), default="ATH", key="hrec_payer", format_func=lambda k: PAYERS[k].name) or "ATH"
    _load(payer)
    run_ = view["latest"].get(payer)
    if not run_:
        st.info(f"No reconciliation for {PAYERS[payer].name} yet. Load the payer's member-level file above.")
    else:
        _summary(run_)
        tabs = st.tabs([f"Open items ({run_['summary']['open_items']})", "Measures", f"Disputes ({len(view['disputes'])})",
                        f"Tasks ({len(view['tasks'])})", "History"])
        with tabs[0]:
            _items(run_)
        with tabs[1]:
            _measures(run_)
        with tabs[2]:
            _disputes(view)
        with tabs[3]:
            _tasks(view)
        with tabs[4]:
            _history(view)


def _load(payer: str) -> None:
    with st.container(border=True):
        card_title("Load the payer file", f"{PAYERS[payer].name} member-level measure file (CSV: member_id, measure_id, payer_status, "
                   "numerator_source, last_service_date)")
        c1, c2, c3 = st.columns([1.2, 2, 1], vertical_alignment="bottom")
        if c1.button("Reconcile synthetic payer file", key="hrec_run_synth", type="primary", disabled=not allowed(MODULE, "Edit"),
                     help="Models what the payer can see: claims plus accepted supplemental records.") and guard(MODULE, "Edit", "run reconciliation"):
            text = run(reconciliation.generate_payer_file, payer)
            if text and run(reconciliation.reconcile, payer, text, actor(), "synthetic payer file", success="Reconciliation complete"):
                st.rerun()
        uploaded = c2.file_uploader("Upload payer CSV", type=["csv", "txt"], key=f"hrec_upload_{payer}", label_visibility="collapsed")
        if uploaded is not None and c2.button("Reconcile uploaded file", key="hrec_run_upload", disabled=not allowed(MODULE, "Edit")):
            if guard(MODULE, "Edit", "run reconciliation") and run(reconciliation.reconcile, payer, uploaded.getvalue().decode("utf-8", "replace"),
                                                                   actor(), uploaded.name, success="Reconciliation complete"):
                st.rerun()
        if allowed(MODULE, "Edit"):
            c3.download_button("Sample payer file", data=reconciliation.generate_payer_file(payer), file_name=f"{payer}_member_measures.csv",
                               key="hrec_sample", icon=":material/download:")


def _summary(run_: dict) -> None:
    summary = run_["summary"]
    counts = summary["counts"]
    recoverable = sum(m["recoverable"] for m in summary["measures"].values())
    cols = st.columns(4)
    for col, args in zip(cols, (
        ("Agreement", f"{summary['agreement']}%", "mint" if (summary["agreement"] or 0) >= 90 else "butter", f"{summary['compared']} member-measures compared"),
        ("Compliant only internally", str(counts.get("INTERNAL_ONLY_COMPLIANT", 0)), "rose", f"{recoverable} numerator(s) recoverable at the payer"),
        ("Compliant only at payer", str(counts.get("PAYER_ONLY_COMPLIANT", 0)), "butter", "Payer holds evidence we lack"),
        ("Eligibility / attribution", str(sum(counts.get(k, 0) for k in ("DENOMINATOR_MISMATCH", "NOT_ON_PAYER_FILE", "NOT_IN_REGISTRY"))),
         "lav", f"{run_['run_id']} · file {run_['file_sha256'][:10]}"),
    ), strict=True):
        with col:
            render_kpi_card(*args)


def _items(run_: dict) -> None:
    items = [i for i in run_["items"] if i["category"] != "AGREE"]
    if not items:
        st.success("Registry and payer file agree on every member and measure.")
        return
    render_html_table(["Member", "Measure", "Difference", "Root cause", "Ours", "Payer", "Owner", "Action"],
                      [[code(i["member_id"]), code(i["measure_id"]), badge(i["category_label"], i["tone"]), escape(i["root_cause_label"]),
                        escape((i["internal_status"] or "—").replace("_", " ").title()), escape((i["payer_status"] or "no row").replace("_", " ").title()),
                        escape(i["owner_role"]), escape(i["action"])] for i in items],
                      ["blocked" if i["priority"] == "high" else "" for i in items])
    st.caption("The Reconciliation analyst (L2 agent) drafts a task or dispute for each row; approve them in the AI agent console, or act here.")
    labels = {i["item_id"]: f"{i['member_id']} · {i['measure_id']} — {i['root_cause_label']}" for i in items}
    chosen = st.selectbox("Act on", list(labels), format_func=labels.get, key=f"hrec_item_{run_['run_id']}")
    item = next(i for i in items if i["item_id"] == chosen)
    c1, c2 = st.columns(2)
    if c1.button(f"Route to {item['owner_role']}", key=f"hrec_task_{run_['run_id']}_{chosen}", disabled=not allowed(MODULE, "Edit")):
        if guard(MODULE, "Edit", "route a reconciliation item") and run(reconciliation.create_task, run_["run_id"], chosen, actor(), success="Task routed"):
            st.rerun()
    if item["disputable"]:
        with c2.form(f"hrec_dispute_{run_['run_id']}_{chosen}"):
            note = st.text_area("Basis for the dispute", key=f"hrec_note_{chosen}",
                                placeholder="e.g. Accepted in SUP-0003 on 14 Oct; payer file still shows non-compliant")
            if st.form_submit_button("Open dispute", disabled=not allowed(MODULE, "Approve")) and guard(MODULE, "Approve", "open a payer dispute"):
                if run(reconciliation.open_dispute, run_["run_id"], chosen, actor(), note, success="Dispute opened"):
                    st.rerun()
    else:
        c2.caption("This root cause is fixed by an internal action, not a dispute.")


def _measures(run_: dict) -> None:
    rows = []
    for measure_id, entry in run_["summary"]["measures"].items():
        rows.append((f"{measure_id} · ours", f"{entry['internal']['numerator']}/{entry['internal']['denominator']}", entry["internal"]["rate"],
                     f"{entry['internal']['rate']}%" if entry["internal"]["rate"] is not None else "—", "violet"))
        rows.append((f"{measure_id} · payer today", f"{entry['payer']['numerator']}/{entry['payer']['denominator']}", entry["payer"]["rate"],
                     f"{entry['payer']['rate']}%" if entry["payer"]["rate"] is not None else "—", "amber"))
        rows.append((f"{measure_id} · payer if resolved", f"+{entry['recoverable']} recoverable", entry["payer_rate_if_resolved"],
                     f"{entry['payer_rate_if_resolved']}%" if entry["payer_rate_if_resolved"] is not None else "—", "green"))
    with st.container(border=True):
        card_title("Rate impact", "Our rate, the rate the payer reports today, and the payer rate once internal-only numerators are credited")
        bars(rows)


def _disputes(view: dict) -> None:
    if not view["disputes"]:
        st.info("No disputes. Disputes are opened for root causes a payer must fix (accepted record not applied, attribution, eligibility).")
        return
    render_html_table(["Dispute", "Payer", "Member", "Measure", "Root cause", "Status", "Opened", "Basis"],
                      [[code(d["dispute_id"]), escape(d["payer"]), code(d["member_id"]), code(d["measure_id"]),
                        escape(d["root_cause"].replace("_", " ").title()), status_badge(d["status"]),
                        escape(f"{d['opened_by']} · {fmt_date(d['opened_at'])}"), escape(d["note"])] for d in view["disputes"]])
    open_ = [d for d in view["disputes"] if d["status"] == "OPEN"]
    if open_:
        with st.form("hrec_resolve"):
            c1, c2, c3 = st.columns([1.2, 1, 2])
            dispute_id = c1.selectbox("Dispute", [d["dispute_id"] for d in open_])
            outcome = c2.selectbox("Outcome", ["UPHELD", "DENIED", "WITHDRAWN"])
            note = c3.text_input("Payer response")
            if st.form_submit_button("Record outcome", disabled=not allowed(MODULE, "Approve")) and guard(MODULE, "Approve", "resolve a dispute"):
                if run(reconciliation.resolve_dispute, dispute_id, actor(), outcome, note, success="Dispute updated"):
                    st.rerun()


def _tasks(view: dict) -> None:
    if not view["tasks"]:
        st.info("No routed tasks yet.")
        return
    st.dataframe(pd.DataFrame([{"Task": t["task_id"], "Payer": t["payer"], "Member": t["member_id"], "Measure": t["measure_id"],
                                "Owner": t["owner_role"], "Action": t["action"], "Status": t["status"], "Created by": t["created_by"]}
                               for t in view["tasks"]]), hide_index=True, width="stretch")


def _history(view: dict) -> None:
    render_html_table(["Run", "Payer", "When", "By", "Source", "Rows", "Agreement", "Open items", "File SHA-256"],
                      [[code(r["run_id"]), escape(r["payer"]), escape(r["at"].replace("T", " ")), escape(r["by"]), escape(r["source"]),
                        str(r["payer_rows"]), f"{r['summary']['agreement']}%", str(r["summary"]["open_items"]), code(r["file_sha256"][:12])]
                       for r in view["runs"]])
