"""NCQA certification readiness: controls by audit standard, attestations, assessment history and the evidence pack."""

from __future__ import annotations

from html import escape

import streamlit as st

from app.hedis import ncqa
from frontend.ui.components import card_title, render_banner, render_html_table, render_kpi_card
from frontend.ui.shell import render_page
from frontend.views.hedis.common import actor, allowed, bars, code, flag_disabled_notice, fmt_date, guard, header, principal, run, status_badge

MODULE = "NCQA Readiness"


def show_hedis_ncqa():
    render_page("hedis_ncqa")
    if flag_disabled_notice():
        return
    actions = header("NCQA certification readiness", "Automated controls mapped to the HEDIS Compliance Audit standards, with attestations "
                     "and an evidence pack for the auditor")
    result = ncqa.evaluate()
    if actions.button("Record assessment", key="hncqa_assess", type="primary", icon=":material/fact_check:",
                      disabled=not allowed(MODULE, "Edit")) and guard(MODULE, "Edit", "record a readiness assessment"):
        if run(ncqa.record_assessment, actor(), success="Assessment recorded"):
            st.rerun()
    pack = ncqa.evidence_pack()
    actions.download_button("Evidence pack", data=pack["markdown"], file_name="ncqa_evidence_pack.md", key="hncqa_pack",
                            icon=":material/download:")
    counts = result["counts"]
    cols = st.columns(4)
    for col, args in zip(cols, (
        ("Readiness", f"{result['score']}%", "mint" if result["score"] >= 85 else "butter" if result["score"] >= 60 else "rose", result["verdict"]),
        ("Controls passing", str(counts.get("PASS", 0) + counts.get("ATTESTED", 0)), "mint", f"of {len(result['controls'])} controls"),
        ("Warnings", str(counts.get("WARN", 0)), "butter", "Work before the audit"),
        ("Failing / unattested", f"{counts.get('FAIL', 0)} / {counts.get('ATTEST', 0)}", "rose", "Blocks certification"),
    ), strict=True):
        with col:
            render_kpi_card(*args)
    render_banner("Self-assessment, not an audit opinion", "Certification needs licensed NCQA specifications, NCQA's measure test deck and an "
                  "NCQA-licensed audit organization. Controls that depend on them fail until they exist (see HD-1).", icon="&#9432;")
    tabs = st.tabs(["Controls", "By standard", "Attestations", "History"])
    with tabs[0]:
        _controls(result)
    with tabs[1]:
        _standards(result)
    with tabs[2]:
        _attestations(result)
    with tabs[3]:
        _history(result)


def _controls(result: dict) -> None:
    order = {"FAIL": 0, "ATTEST": 1, "WARN": 2, "ATTESTED": 3, "PASS": 4}
    controls = sorted(result["controls"], key=lambda c: (order[c["status"]], c["control_id"]))
    render_html_table(["Control", "Standard", "Status", "Evidence", "Remediation"],
                      [[f"{code(c['control_id'])}<br>{escape(c['title'])}", escape(c["standard"]), status_badge(c["status"]), escape(c["detail"]),
                        escape(c["remediation"] if c["status"] not in ("PASS", "ATTESTED") else "—")] for c in controls],
                      ["blocked" if c["status"] == "FAIL" else "" for c in controls])


def _standards(result: dict) -> None:
    with st.container(border=True):
        card_title("Readiness by audit standard", "PASS and attested controls count fully; warnings count half")
        bars([(standard, entry["title"], entry["readiness"], f"{entry['readiness']}%",
               "green" if entry["readiness"] >= 85 else "amber" if entry["readiness"] >= 50 else "red")
              for standard, entry in result["standards"].items()])


def _attestations(result: dict) -> None:
    attestable = [c for c in result["controls"] if c["attestation"]]
    role = principal().role
    for control in attestable:
        with st.container(border=True):
            card_title(f"{control['control_id']} · {control['title']}", control["requirement"], trailing=status_badge(control["status"]))
            st.caption(control["detail"])
            with st.form(f"hncqa_attest_{control['control_id']}"):
                note = st.text_area("Evidence behind the attestation", key=f"hncqa_note_{control['control_id']}",
                                    placeholder="e.g. Over-read of 30 abstracted charts completed 2026-10-20; 29/30 agreed; finding logged as CAPA-12")
                can_attest = allowed(MODULE, "Approve") and role in (control["attest_role"], "Super Admin")
                if st.form_submit_button(f"Attest as {role or 'user'}", disabled=not can_attest):
                    if guard(MODULE, "Approve", "attest a control") and run(ncqa.attest, control["control_id"], actor(), role, note,
                                                                             success="Attestation recorded"):
                        st.rerun()
            if role not in (control["attest_role"], "Super Admin"):
                st.caption(f"Attested by {control['attest_role']}.")


def _history(result: dict) -> None:
    if not result["assessments"]:
        st.info("No assessment recorded yet. Recording one keeps a dated score for the audit trail.")
        return
    render_html_table(["Assessment", "When", "By", "Score", "Statuses"],
                      [[code(a["assessment_id"]), escape(fmt_date(a["at"])), escape(a["by"]), f"{a['score']}%",
                        escape(", ".join(f"{k} {v}" for k, v in sorted(a["counts"].items())))] for a in result["assessments"]])
