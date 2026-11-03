"""Evidence & quality — supplied versus calculated QA comparison with tolerances and second-person sign-off."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from app.hedis import calculator, qa
from frontend.ui.components import badge, card_title, render_html_table, render_kpi_card
from frontend.ui.shell import render_page
from frontend.views.hedis.common import actor, allowed, bars, code, flag_disabled_notice, fmt_date, guard, header, run, status_badge

MODULE = "Evidence & Quality"


def show_hedis_qa():
    render_page("hedis_qa")
    if flag_disabled_notice():
        return
    actions = header("Supplied vs calculated QA", "Every supplied result recalculated from source evidence under the active measure version — "
                     "differences explained, measures held to tolerance")
    if actions.button("Record QA run", key="hqa_run", type="primary", icon=":material/fact_check:", disabled=not allowed(MODULE, "Edit")):
        if guard(MODULE, "Edit", "record a QA run") and run(qa.record_run, actor(), success="QA run recorded — it needs a second-person sign-off"):
            st.rerun()
    result = qa.compare()
    summary = result["summary"]
    cols = st.columns(4)
    for col, args in zip(cols, (
        ("State agreement", f"{summary['agreement']}%", "mint" if (summary["agreement"] or 0) >= qa.TOLERANCE["agreement_min"] else "rose",
         f"{summary['agree']:,} of {summary['compared']:,} member-measures"),
        ("Measures in tolerance", f"{summary['measures_passing']} / {summary['measures']}", "mint" if summary["measures_passing"] == summary["measures"] else "butter",
         f"≥ {qa.TOLERANCE['agreement_min']:g}% agreement, ≤ {qa.TOLERANCE['rate_delta_max']:g} pt rate gap"),
        ("Supplied, unsupported", str(summary["categories"].get("SUPPLIED_UNSUPPORTED", 0)), "rose", "Compliant per supplier, not per evidence"),
        ("Evidence, not supplied", str(summary["categories"].get("SUPPLIED_UNDERCOUNT", 0)), "butter", "Compliant per evidence, open per supplier"),
    ), strict=True):
        with col:
            render_kpi_card(*args)
    st.caption(f"Active measure version {result['version_id']} · dataset {result['dataset_sha256'][:12]} · "
               f"{result['normalization']['golden_records']:,} normalized evidence records")
    tabs = st.tabs(["By measure", f"Differences ({len(result['items'])})", "Sign-off"])
    with tabs[0]:
        _measures(result)
    with tabs[1]:
        _differences(result)
    with tabs[2]:
        _signoff()


def _measures(result: dict) -> None:
    rows = []
    for measure_id, entry in result["measures"].items():
        rows.append([code(measure_id), f"{entry['agreement']}%", f"{entry['supplied']['rate']}%", f"{entry['calculated']['rate']}%",
                     f"{entry['rate_delta']:+g} pts" if entry["rate_delta"] is not None else "—",
                     " ".join(badge(f"{qa.CATEGORIES[k][0]} {v}", qa.CATEGORIES[k][1]) for k, v in entry["categories"].items() if k != "AGREE") or "—",
                     badge("Within tolerance", "green") if entry["pass"] else badge("Outside tolerance", "red")])
    render_html_table(["Measure", "Agreement", "Supplied rate", "Calculated rate", "Difference", "Differences", "QA"], rows,
                      ["" if e["pass"] else "blocked" for e in result["measures"].values()])
    measure = st.selectbox("Confusion matrix", list(result["measures"]), key="hqa_matrix")
    entry = result["measures"][measure]
    with st.container(border=True):
        card_title(f"{measure}: supplied state × calculated state", "Diagonal cells agree; everything else is listed under Differences")
        matrix = pd.DataFrame(entry["matrix"])
        matrix["supplied"] = matrix["supplied"].map(calculator.STATE_LABELS)
        matrix["calculated"] = matrix["calculated"].map(calculator.STATE_LABELS)
        st.dataframe(matrix.pivot_table(index="supplied", columns="calculated", values="count", fill_value=0), width="stretch")
        bars([("Supplied", f"{entry['supplied']['numerator']}/{entry['supplied']['denominator']}", entry["supplied"]["rate"],
               f"{entry['supplied']['rate']}%", "amber"),
              ("Calculated from evidence", f"{entry['calculated']['numerator']}/{entry['calculated']['denominator']}", entry["calculated"]["rate"],
               f"{entry['calculated']['rate']}%", "violet")])


def _differences(result: dict) -> None:
    items = result["items"]
    c1, c2 = st.columns(2)
    measure = c1.selectbox("Measure", ["All", *result["measures"]], key="hqa_measure")
    category = c2.selectbox("Category", ["All", *[k for k in qa.CATEGORIES if k != "AGREE"]], key="hqa_category",
                            format_func=lambda k: k if k == "All" else qa.CATEGORIES[k][0])
    shown = [i for i in items if (measure == "All" or i["measure_id"] == measure) and (category == "All" or i["category"] == category)]
    st.dataframe(pd.DataFrame([{
        "Member": i["patient_id"], "Measure": i["measure_id"], "Category": i.get("category_label", i["category"]),
        "Supplied": i.get("supplied_label"), "Calculated": i.get("calculated_label"), "Supplied reason": i.get("supplied_reason"),
        "Calculated from evidence": i.get("calculated_reason"),
        "Evidence": "; ".join(f"{e['concept']} {e['date'] or '—'} [{', '.join(e['lineage'])}]" for e in i.get("evidence", [])),
    } for i in shown]), hide_index=True, width="stretch", height=min(560, 38 + 35 * max(len(shown), 1)))
    st.caption(f"{len(shown)} difference(s). Each calculated result names the normalized evidence and its source rows.")


def _signoff() -> None:
    runs = qa.history()
    if not runs:
        st.info("No QA run recorded yet. Record one above; a different person signs it off here.")
        return
    render_html_table(["Run", "When", "By", "Version", "Agreement", "Within tolerance", "Sign-off"],
                      [[code(r["run_id"]), escape(fmt_date(r["at"])), escape(r["by"]), code(r["version_id"]), f"{r['summary']['agreement']}%",
                        f"{r['summary']['measures_passing']}/{r['summary']['measures']}",
                        status_badge(r["signoff"]["decision"]) + escape(f" {r['signoff']['by']}") if r["signoff"] else badge("Awaiting", "amber")]
                       for r in runs])
    pending = [r for r in runs if not r["signoff"]]
    if pending:
        run_ = pending[0]
        failing = [m for m, e in run_["measures"].items() if not e["pass"]]
        with st.form("hqa_signoff"):
            st.markdown(f"**Sign off {run_['run_id']}**" + (f" — {', '.join(failing)} outside tolerance (can only be rejected to the supplier)" if failing else ""))
            decision = st.radio("Decision", ["REJECTED", "ACCEPTED"] if failing else ["ACCEPTED", "REJECTED"], horizontal=True)
            note = st.text_area("Basis", placeholder="e.g. KED supplied Met results lack eGFR/uACR evidence; returned to supplier with the difference file")
            if st.form_submit_button("Record sign-off", disabled=not allowed(MODULE, "Approve")) and guard(MODULE, "Approve", "sign off QA"):
                if run(qa.sign_off, run_["run_id"], actor(), decision, note, success="Sign-off recorded"):
                    st.rerun()
