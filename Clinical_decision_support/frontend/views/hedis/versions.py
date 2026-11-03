"""Governance — measure version control center: draft, impact analysis, four-eyes approval, activation, rollback."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from app.hedis import versions
from frontend.ui.components import badge, card_title, render_html_table, render_kpi_card
from frontend.ui.shell import render_page
from frontend.views.hedis.common import actor, allowed, bars, code, flag_disabled_notice, fmt_date, guard, header, run, status_badge

MODULE = "Measure Versioning"
STATUS_TONES = {"DRAFT": "amber", "IN_REVIEW": "violet", "APPROVED": "blue", "ACTIVE": "green", "SUPERSEDED": "gray", "REJECTED": "red"}


def show_hedis_versions():
    render_page("hedis_versions")
    if flag_disabled_notice():
        return
    header("Version control center", "Measure logic as versioned specifications — impact-analysed on the real population and approved by a "
           "second person before it goes live")
    view = versions.view()
    active = next(v for v in view["versions"] if v["version_id"] == view["active"])
    in_review = [v for v in view["versions"] if v["status"] in ("DRAFT", "IN_REVIEW", "APPROVED")]
    cols = st.columns(4)
    for col, args in zip(cols, (
        ("Active version", view["active"], "mint", f"effective {fmt_date(active['effective_date'])}"),
        ("In the pipeline", str(len(in_review)), "butter" if in_review else "lav", "draft · review · approved"),
        ("Versions on record", str(len(view["versions"])), "sky", "every version kept"),
        ("Engine rules", view["engine_rules"]["current"], "lav", f"{len(view['engine_rules']['log'])} quality-loop releases"),
    ), strict=True):
        with col:
            render_kpi_card(*args)
    tabs = st.tabs(["Versions", "Propose a change", "Compare", "Active specification", "Change log"])
    with tabs[0]:
        _versions(view)
    with tabs[1]:
        _propose(view)
    with tabs[2]:
        _compare(view)
    with tabs[3]:
        _spec(active)
    with tabs[4]:
        _log(view)


def _versions(view: dict) -> None:
    render_html_table(["Version", "Status", "Based on", "Changes", "Author", "Approved by", "Effective"],
                      [[code(v["version_id"]), badge(v["status"].replace("_", " ").title(), STATUS_TONES[v["status"]]), escape(v["based_on"] or "—"),
                        escape("; ".join(f"{c['path']}: {c['from']} → {c['to']}" for c in v["changes"]) or "Baseline"), escape(v["author"]),
                        escape(v["approved_by"] or "—"), escape(fmt_date(v["effective_date"]))] for v in view["versions"]])
    workable = [v for v in view["versions"] if v["status"] in ("DRAFT", "IN_REVIEW", "APPROVED", "SUPERSEDED")]
    if not workable:
        return
    chosen = st.selectbox("Work on", [v["version_id"] for v in workable], key="hver_pick",
                          format_func=lambda vid: f"{vid} · {next(v['status'] for v in workable if v['version_id'] == vid)}")
    version = next(v for v in workable if v["version_id"] == chosen)
    with st.container(border=True):
        card_title(f"{version['version_id']} · {version['status'].replace('_', ' ').title()}", version["rationale"],
                   trailing=status_badge(version["status"]))
        c1, c2, c3, c4 = st.columns(4)
        status = version["status"]
        if status == "DRAFT" and c1.button("Submit for review", key="hver_submit", disabled=not allowed(MODULE, "Edit")):
            if guard(MODULE, "Edit", "submit a version") and run(versions.submit, chosen, actor(), success="Submitted for review"):
                st.rerun()
        if status in ("DRAFT", "IN_REVIEW") and c2.button("Run impact analysis", key="hver_impact", disabled=not allowed(MODULE, "Edit")):
            if guard(MODULE, "Edit", "run an impact analysis") and run(versions.analyze_impact, chosen, actor(), success="Impact analysed"):
                st.rerun()
        if status == "IN_REVIEW" and c3.button("Approve", key="hver_approve", type="primary", disabled=not allowed(MODULE, "Approve"),
                                               help="Four eyes: the author cannot approve."):
            if guard(MODULE, "Approve", "approve a version") and run(versions.approve, chosen, actor(), success="Approved"):
                st.rerun()
        if status == "APPROVED" and c3.button("Activate", key="hver_activate", type="primary", disabled=not allowed(MODULE, "Approve")):
            if guard(MODULE, "Approve", "activate a version") and run(versions.activate, chosen, actor(), success=f"{chosen} is now active"):
                st.rerun()
        if status in ("DRAFT", "IN_REVIEW", "APPROVED"):
            with c4.popover("Reject"):
                note = st.text_input("Reason", key="hver_reject_note")
                if st.button("Reject version", key="hver_reject", disabled=not allowed(MODULE, "Approve")):
                    if guard(MODULE, "Approve", "reject a version") and run(versions.reject, chosen, actor(), note, success="Rejected"):
                        st.rerun()
        if status == "SUPERSEDED":
            with c1.popover("Roll back to this version"):
                reason = st.text_input("Reason", key="hver_rollback_note")
                if st.button("Roll back", key="hver_rollback", disabled=not allowed(MODULE, "Approve")):
                    if guard(MODULE, "Approve", "roll back") and run(versions.rollback, chosen, actor(), reason, success=f"Rolled back to {chosen}"):
                        st.rerun()
        impact = version.get("impact")
        if impact:
            st.markdown(f'<div class="nc-label" style="margin-top:.6rem">Impact vs {escape(impact["compared_with"])} · '
                        f'{impact["members_changed"]} member-measure result(s) change</div>', unsafe_allow_html=True)
            bars([(m, f"{v['before']['rate']}% → {v['after']['rate']}% · {v['members_changed']} changed", v["after"]["rate"],
                   f"{v['rate_delta']:+g} pts" if v["rate_delta"] is not None else "—",
                   "green" if (v["rate_delta"] or 0) > 0 else "red" if (v["rate_delta"] or 0) < 0 else "gray")
                  for m, v in impact["measures"].items()])
            if impact["changes"]:
                st.dataframe(pd.DataFrame(impact["changes"]), hide_index=True, width="stretch", height=min(320, 38 + 35 * len(impact["changes"])))
        elif status in ("DRAFT", "IN_REVIEW"):
            st.caption("Approval needs an impact analysis against the active version.")


def _propose(view: dict) -> None:
    active = next(v for v in view["versions"] if v["version_id"] == view["active"])
    measures = active["spec"]["measures"]
    with st.form("hver_draft"):
        c1, c2 = st.columns(2)
        measure = c1.selectbox("Measure", list(measures), format_func=lambda m: f"{m} · {measures[m]['name']}")
        governed = [(section, name) for section in ("population", "numerator") for name in measures[measure][section]
                    if name in versions.PARAMETER_RULES]
        parameter = c2.selectbox("Parameter", [f"{s}.{n}" for s, n in governed])
        section, name = parameter.split(".")
        current = measures[measure][section][name]
        value = st.text_input(f"New value (current: {current}; allowed: {view['parameter_rules'][name][1:] or 'true/false'})", value=str(current))
        rationale = st.text_area("Rationale", placeholder="e.g. Align the glycemic target with the payer contract for MY2026")
        if st.form_submit_button("Create draft", type="primary", disabled=not allowed(MODULE, "Edit")) and guard(MODULE, "Edit", "draft a version"):
            draft = run(versions.create_draft, view["active"], {f"{measure}.{parameter}": value}, actor(), rationale,
                        success="Draft created — run the impact analysis and submit for review")
            if draft:
                st.session_state.hver_pick = draft["version_id"]
                st.rerun()


def _compare(view: dict) -> None:
    ids = [v["version_id"] for v in view["versions"]]
    if len(ids) < 2:
        st.info("Only the baseline exists so far.")
        return
    c1, c2 = st.columns(2)
    a = c1.selectbox("From", ids, index=len(ids) - 1, key="hver_a")
    b = c2.selectbox("To", ids, index=0, key="hver_b")
    differences = run(versions.diff, a, b) or []
    if differences:
        render_html_table(["Parameter", a, b], [[code(d["path"]), escape(str(d[a])), escape(str(d[b]))] for d in differences])
    else:
        st.caption("The two versions are identical.")


def _spec(active: dict) -> None:
    st.caption(f"{active['version_id']} · SHA-256 {active['spec_sha256'][:16]} · approved by {active['approved_by']}")
    rows = []
    for measure_id, measure in active["spec"]["measures"].items():
        for section in ("population", "numerator"):
            for name, value in measure[section].items():
                rows.append({"Measure": measure_id, "Section": section, "Parameter": name, "Value": str(value),
                             "Governed": name in versions.PARAMETER_RULES})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=520)


def _log(view: dict) -> None:
    if view["events"]:
        render_html_table(["When", "Who", "Action", "Version", "Detail"],
                          [[escape(e["at"].replace("T", " ")[:16]), escape(e["actor"]), code(e["action"]), code(e["object_id"]), escape(e["details"])]
                           for e in reversed(view["events"])])
    st.markdown('<div class="nc-label" style="margin-top:.8rem">Quality-loop engine rule history (code releases)</div>', unsafe_allow_html=True)
    render_html_table(["Rule version", "Released", "Change"], [[code(v), escape(d), escape(t)] for v, d, t in view["engine_rules"]["log"]])
