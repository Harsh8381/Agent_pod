"""Evidence & quality — cross-source evidence normalizer: mapping, flags, merges and lineage per source."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from app.hedis import normalizer, store, versions
from frontend.ui.components import card_title, render_html_table, render_kpi_card
from frontend.ui.shell import render_page
from frontend.views.hedis.common import bars, code, flag_disabled_notice, header, phi_allowed

FLAG_HELP = {
    "MISSING_DATE": "No usable service date — never counted",
    "FUTURE_DATE": "Service date after the as-of date — never counted",
    "IMPLAUSIBLE": "Value outside the physiological range — never counted",
    "INVALID_VALUE": "Value could not be parsed — never counted",
    "CLAIM_DENIED": "Denied claim — not evidence of service",
    "NOT_COMPLETED": "Ordered, cancelled or incomplete — never counted",
    "REPORT_MISSING": "Service without its report — counts only where the measure allows",
    "EXTERNAL_SOURCE": "Outside facility record — needs verification",
    "UNIT_CONVERTED": "Converted to the canonical unit",
    "OUTSIDE_HISTORY": "Older than the ten-year history window",
}


def show_hedis_evidence():
    render_page("hedis_evidence")
    if flag_disabled_notice():
        return
    header("Evidence normalizer", "Six source feeds and workflow captures mapped to one canonical evidence set — with every merge and flag explained")
    result = normalizer.normalize(versions._workflow_evidence(store.load()))
    summary = result["summary"]
    cols = st.columns(4)
    for col, args in zip(cols, (
        ("Source records", f"{summary['raw_records']:,}", "lav", f"{len(result['sources'])} feeds"),
        ("Mapping rate", f"{summary['mapping_rate']}%", "mint" if summary["mapping_rate"] >= 95 else "rose", f"{len(result['unmapped'])} unmapped code(s)"),
        ("Golden records", f"{summary['golden_records']:,}", "sky", f"{summary['merged']:,} cross-source merges"),
        ("Not countable", str(summary["unusable"]), "butter", "Flagged records excluded from measures"),
    ), strict=True):
        with col:
            render_kpi_card(*args)
    tabs = st.tabs(["Sources", "Flags", "Concepts", "Member lineage", "Mapping rules"])
    with tabs[0]:
        render_html_table(["Source", "Records", "Mapped", "Recognised, not evidence", "Unmapped", "Flagged", "Mapping rate"],
                          [[code(name), f"{s['read']:,}", f"{s['mapped']:,}", str(s.get("recognised_not_evidence", 0)), str(s["unmapped"]),
                            str(s["flagged"]), f"{s['mapping_rate']}%"] for name, s in result["sources"].items()])
        if result["unmapped"]:
            st.warning("Unmapped codes: " + ", ".join(f"{k} ({v})" for k, v in result["unmapped"].items()))
        st.caption("Reason-for-encounter Z codes (e.g. Z00.00, Z12.11) are recognised but never treated as proof that a screening happened.")
    with tabs[1]:
        if result["flags"]:
            render_html_table(["Flag", "Records", "What it means"],
                              [[code(flag), str(count), escape(FLAG_HELP.get(flag, ""))] for flag, count in result["flags"].items()])
        else:
            st.success("No record was flagged.")
    with tabs[2]:
        top = max(result["concepts"].values() or [1])
        bars([(concept.replace("_", " "), "canonical concept", count / top * 100, f"{count:,}", "violet") for concept, count in result["concepts"].items()])
    with tabs[3]:
        _lineage(result)
    with tabs[4]:
        _rules()


def _lineage(result: dict) -> None:
    members = normalizer.patient_demographics()
    ids = sorted(result["by_patient"])
    label = (lambda pid: f"{pid} · {members[pid]['name']}") if phi_allowed() else (lambda pid: pid)
    patient = st.selectbox("Member", ids, format_func=label, key="hev_patient")
    records = sorted(result["by_patient"][patient], key=lambda r: (r.service_date or "", r.concept), reverse=True)
    member = members[patient]
    st.caption(f"Age {member['age']} at period end · {member['sex']} · {member['segment']}"
               + (f" · {member['age_flag'].replace('_', ' ').lower()}" if member["age_flag"] else ""))
    st.dataframe(pd.DataFrame([{
        "Concept": r.concept, "Date": r.service_date or "—", "Code": f"{r.code_system} {r.code}",
        "Value": f"{r.value:g}/{r.value2:g}" if r.value2 is not None and r.value is not None else (f"{r.value:g}" if r.value is not None else r.result_flag),
        "Unit": r.unit, "Source": r.source, "Class": r.source_class.replace("_", " ").title(), "Verification": r.verification or "—",
        "Flags": ", ".join(r.flags), "Lineage": " + ".join(f"{l['source']}:{l['record_id']}" for l in r.lineage),
    } for r in records]), hide_index=True, width="stretch", height=min(520, 38 + 35 * max(len(records), 1)))


def _rules() -> None:
    c1, c2 = st.columns(2, gap="medium")
    with c1, st.container(border=True):
        card_title("Code mapping", "Source code → canonical concept and standard code")
        rows = [[escape("observations"), escape(k), escape(f"{v[0]} · {v[1]} {v[2]}")] for k, v in normalizer.OBSERVATION_MAP.items()]
        rows += [[escape("procedures"), escape(k), escape(f"{v[0]} · {v[1]}")] for k, v in normalizer.PROCEDURE_MAP.items()]
        rows += [[escape("conditions"), escape(k + "*"), escape(v)] for k, v in normalizer.CONDITION_PREFIX_MAP.items()]
        rows += [[escape("encounters"), escape(k), escape(v)] for k, v in normalizer.ENCOUNTER_MAP.items()]
        rows += [[escape("documents"), escape(k), escape(v)] for k, v in normalizer.DOCUMENT_MAP.items()]
        render_html_table(["Source", "Source code", "Canonical"], rows)
    with c2, st.container(border=True):
        card_title("Merge and precedence", "How the same event from several feeds becomes one golden record")
        st.markdown(
            "- Records of one clinical event (same member, same concept family, same date) merge into one golden record.\n"
            "- The record that carries the result wins: lab observation → workflow capture → procedure → claim → encounter → document.\n"
            "- Reports attach to their service up to "
            f"{normalizer.REPORT_WINDOW_DAYS} days later and pass on their verification status.\n"
            "- Two different same-day results stay separate, so the measure's same-day rule can decide.\n"
            "- Flagged records stay visible with their reason; they are never silently dropped and never counted.\n"
            "- Values are put in canonical form: `132/84` → systolic and diastolic; HbA1c mmol/mol → % (NGSP); FIT text → positive / negative."
        )
