"""HEDIS registry: member x measure ledger, snapshots, period lock and integrity issues."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from app.hedis import registry, store
from app.hedis.config import PAYERS, SOURCE_CLASS_LABELS
from app.vbc_poc.catalog import MEASURE_STATES
from frontend.ui.components import badge, card_title, render_html_table, render_kpi_card, render_tiles
from frontend.ui.shell import render_page
from frontend.views.hedis.common import actor, allowed, bars, code, flag_disabled_notice, fmt_date, guard, header, phi_allowed, run

MODULE = "HEDIS Registry"


def show_hedis_registry():
    render_page("hedis_registry")
    if flag_disabled_notice():
        return
    actions = header("HEDIS registry", "One authoritative row per member and measure — numerator source, evidence, snapshot and period lock")
    view = registry.view(phi=phi_allowed())
    locked = view["locked"]
    if actions.button("Publish snapshot", key="hreg_publish", type="primary", icon=":material/photo_camera:",
                      disabled=bool(locked) or not allowed(MODULE, "Edit")) and guard(MODULE, "Edit", "publish a registry snapshot"):
        if run(registry.publish_snapshot, actor(), success="Registry snapshot published"):
            st.rerun()
    if not locked:
        if actions.button("Lock period", key="hreg_lock", icon=":material/lock:", disabled=not allowed(MODULE, "Approve"),
                          help="Four eyes: the snapshot's publisher cannot lock the period.") and guard(MODULE, "Approve", "lock the period"):
            if run(registry.lock_period, actor(), success="Period locked"):
                st.rerun()
    _kpis(view)
    tabs = st.tabs(["Measures", "Members", f"Integrity ({len(view['issues'])})", f"Snapshots ({len(view['snapshots'])})", "Period"])
    with tabs[0]:
        _measures(view)
    with tabs[1]:
        _members(view)
    with tabs[2]:
        _issues(view)
    with tabs[3]:
        _snapshots(view)
    with tabs[4]:
        _period(view)


def _kpis(view: dict) -> None:
    totals = view["totals"]
    supplemental = sum(1 for row in view["rows"] if row["numerator_source"] in ("STANDARD_SUPPLEMENTAL", "NON_STANDARD_SUPPLEMENTAL"))
    blocking = sum(issue["severity"] == "block" for issue in view["issues"])
    latest = view["latest_snapshot"]
    lock_text = "Locked" if view["locked"] else ("Drifted" if view["drift"] else ("Matches snapshot" if latest else "No snapshot"))
    cols = st.columns(5)
    for col, args in zip(cols, (
        ("Members", str(totals["members"]), "lav", f"{len(view['rows'])} member-measure rows"),
        ("Compliance", f"{totals['rate']}%", "mint", f"{totals['numerator']} / {totals['denominator']} in denominators"),
        ("Supplemental-dependent", str(supplemental), "sky", "Numerators the payer cannot see in claims"),
        ("Integrity", "Clean" if not view["issues"] else f"{blocking} blocking", "rose" if blocking else "butter" if view["issues"] else "mint",
         f"{len(view['issues'])} issue(s) on this build"),
        ("Registry state", lock_text, "rose" if view["drift"] and not view["locked"] else "lav",
         f"Hash {view['content_hash'][:12]}"),
    ), strict=True):
        with col:
            render_kpi_card(*args)


def _measures(view: dict) -> None:
    with st.container(border=True):
        card_title("Measure rates", "Numerator / denominator from the registry, with the evidence class behind every compliant row")
        bars([(f"{measure_id} · {entry['measure']}", f"{entry['numerator']}/{entry['denominator']} · {entry['excluded']} excluded",
               entry["rate"], f"{entry['rate']}%" if entry["rate"] is not None else "—", "green" if (entry["rate"] or 0) >= 60 else "amber")
              for measure_id, entry in view["totals"]["measures"].items()])
    rows = []
    for measure_id, entry in view["totals"]["measures"].items():
        rows.append([code(measure_id), escape(entry["measure"]), str(entry["denominator"]), str(entry["numerator"]), str(entry["excluded"]),
                     f"{entry['rate']}%" if entry["rate"] is not None else "—",
                     " ".join(badge(f"{SOURCE_CLASS_LABELS[k]} {v}", "green" if k == "ADMINISTRATIVE" else "blue") for k, v in entry["by_source"].items()) or "—",
                     " · ".join(f"{PAYERS[p].key} {v['rate']}%" for p, v in entry["by_payer"].items() if v["rate"] is not None)])
    render_html_table(["Measure", "Name", "Denominator", "Numerator", "Excluded", "Rate", "Numerator source", "By payer"], rows)


def _members(view: dict) -> None:
    rows = view["rows"]
    c1, c2, c3, c4 = st.columns(4)
    measure = c1.selectbox("Measure", ["All", *sorted({r["measure_id"] for r in rows})], key="hreg_measure")
    payer = c2.selectbox("Payer", ["All", *PAYERS], key="hreg_payer", format_func=lambda k: k if k == "All" else PAYERS[k].name)
    state = c3.selectbox("State", ["All", *sorted({r["state"] for r in rows})], key="hreg_state",
                         format_func=lambda s: s if s == "All" else MEASURE_STATES[s].label)
    only = c4.selectbox("Show", ["Everyone", "In denominator", "Compliant", "Open (not compliant)"], key="hreg_only")
    shown = [r for r in rows if (measure == "All" or r["measure_id"] == measure) and (payer == "All" or r["payer"] == payer)
             and (state == "All" or r["state"] == state)
             and (only == "Everyone" or (only == "In denominator" and r["in_denominator"]) or (only == "Compliant" and r["numerator_met"])
                  or (only == "Open (not compliant)" and r["in_denominator"] and not r["numerator_met"]))]
    if not phi_allowed():
        st.caption("Names and dates of birth are masked for your role (minimum necessary).")
    st.dataframe(pd.DataFrame([{
        "Member": r["member_id"], "Name": r["name"], "Payer": r["payer"], "Measure": r["measure_id"], "State": r["state_label"],
        "Denominator": r["in_denominator"], "Compliant": r["numerator_met"],
        "Numerator source": SOURCE_CLASS_LABELS.get(r["numerator_source"], "—") if r["numerator_source"] else "—",
        "Compliant date": r["compliant_date"] or "", "Evidence": ", ".join(r["evidence_ids"]), "Provider NPI": r["provider_npi"],
        "Result": f"{r['result_id']} v{r['result_version']}",
    } for r in shown]), hide_index=True, width="stretch", height=min(560, 38 + 35 * max(len(shown), 1)))
    st.caption(f"{len(shown)} of {len(rows)} rows. Rows are rebuilt from the latest versioned measure results; they are never edited by hand.")


def _issues(view: dict) -> None:
    if not view["issues"]:
        st.success("No integrity issues on this build.")
        return
    render_html_table(["Check", "Severity", "Registry key", "Detail"],
                      [[code(i["code"]), badge("Blocks lock" if i["severity"] == "block" else "Warning", "red" if i["severity"] == "block" else "amber"),
                        code(i["key"]), escape(i["detail"])] for i in view["issues"]],
                      ["blocked" if i["severity"] == "block" else "" for i in view["issues"]])
    st.caption("Blocking issues stop the period lock. The registry quality sentinel (L1 agent) opens a task for each one.")


def _snapshots(view: dict) -> None:
    if not view["snapshots"]:
        st.info("No snapshot yet. Publishing freezes the registry with a content hash and records what changed since the last one.")
        return
    render_html_table(["Snapshot", "Published", "By", "Rows", "Compliant", "Hash", "Change vs previous"],
                      [[code(s["snapshot_id"]), escape(fmt_date(s["at"])), escape(s["published_by"]), str(s["row_count"]),
                        f"{s['totals']['numerator']}/{s['totals']['denominator']}", code(s["content_hash"][:12]),
                        escape(f"+{len(s['diff']['gained_compliance'])} gained · -{len(s['diff']['lost_compliance'])} lost · "
                               f"{len(s['diff']['state_changed'])} state change(s)")] for s in view["snapshots"]])
    latest = view["snapshots"][0]
    if latest["diff"]["state_changed"]:
        with st.expander(f"What changed in {latest['snapshot_id']}"):
            st.dataframe(pd.DataFrame(latest["diff"]["state_changed"]), hide_index=True, width="stretch")


def _period(view: dict) -> None:
    locked = view["locked"]
    render_tiles([("Period", view["period"]["id"]), ("Start", view["period"]["start"]), ("End", view["period"]["end"]),
                  ("Rule", view["rule_version"]), ("Engine", view["engine_version"])])
    if locked:
        st.success(f"Locked by {locked['by']} on {fmt_date(locked['at'])} at {locked['snapshot_id']} (hash {locked['content_hash'][:12]}).")
        with st.form("hreg_reopen"):
            reason = st.text_area("Reason for reopening", placeholder="e.g. Late lab feed for March received; corrected rates required")
            if st.form_submit_button("Reopen period", disabled=not allowed(MODULE, "Approve")) and guard(MODULE, "Approve", "reopen the period"):
                if run(registry.reopen_period, actor(), reason, success="Period reopened"):
                    st.rerun()
    else:
        st.info("Locking needs a published snapshot that still matches the registry, no blocking issues, and a different person from the publisher.")
    state = store.load()
    events = [e for e in state["audit"] if e["action"].startswith("registry.")][-15:]
    if events:
        render_html_table(["When", "Who", "Action", "Detail"], [[escape(e["at"].replace("T", " ")), escape(e["actor"]), code(e["action"]),
                                                                  escape(e["details"])] for e in reversed(events)])
