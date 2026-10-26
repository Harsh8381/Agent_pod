"""Quality loop POC — audit trail, governance artifacts, golden tests and the 10-minute demo script."""

from __future__ import annotations

import json
from html import escape

import streamlit as st

from app.vbc_poc import catalog
from app.vbc_poc import repository as repo
from app.vbc_poc import service as vbc
from app.vbc_poc.dataset import DEMO_PATIENT, EVENTS, PATIENTS
from app.vbc_poc.golden import run_golden
from frontend.ui.components import badge, card_title, render_html_table, render_kpi_card
from frontend.ui.shell import render_page
from frontend.ui.state import go_to
from frontend.views.vbc_poc.common import actor, flag_disabled_notice, header, open_gap, open_patient, state_badge


def _demo_status() -> list[tuple]:
    state = vbc.ensure_initialized()
    gaps = [gap for gap in state["gaps"].values() if gap["patient_id"] == DEMO_PATIENT]
    gsd = next((gap for gap in gaps if gap["measure_id"] == "GSD-P" and gap["line"] == "SERVICE"), None)
    bpd = next((gap for gap in gaps if gap["measure_id"] == "BPD-P" and gap["line"] == "OUTCOME"), None)
    target = gsd["gap_id"] if gsd else None
    attached = bool(gsd and gsd["evidence_ids"])
    closed = bool(gsd and gsd["status"] == "CLOSED")
    return [
        (1, "Open population dashboard", "Shows 20 synthetic patients, baseline performance and the priority queue.", True,
         ("Open dashboard", lambda: go_to("vbcpoc_dashboard"))),
        (2, "Select the high-risk diabetic patient", "James Whitfield opens in Patient 360 with a 12-month timeline.", True,
         ("Open James Whitfield", lambda: open_patient(DEMO_PATIENT))),
        (3, "Open the quality panel", "HbA1c is missing (open care gap); BP 152/94 is uncontrolled; evidence is displayed.", True, None),
        (4, "Inspect gap rationale", "Missing test and uncontrolled outcome are separate gaps with separate owners.", True,
         ("Open the HbA1c gap", (lambda: open_gap(target)) if target else None)),
        (5, "Assign actions", "Review the finding, then assign an owner; the HbA1c order and BP follow-up get due dates.",
         bool(gsd and gsd["owner"]), None),
        (6, "Generate outreach draft", "Plain-language reminder created and held for human approval.", bool(gsd and gsd["outreach_ids"]), None),
        (7, "Add completion evidence", "Attach the new HbA1c (and a follow-up BP on the BP gap); both stay pending until verified.", attached, None),
        (8, "Verify and re-evaluate", "Reviewer verifies; the measure re-runs and the gap closes automatically.", closed, None),
        (9, "Return to the dashboard", "Compliance and closure rate update; the before/after panel names the evidence.", closed and bool(bpd),
         ("Open dashboard", lambda: go_to("vbcpoc_dashboard"))),
        (10, "Open the audit trail", "Previous/new state, evidence IDs and rule version for every step — below.", True, None),
    ]


def show_governance():
    render_page("vbcpoc_governance")
    if flag_disabled_notice():
        return
    header("Audit & demo", "Demo script, audit trail, measure inventory, rule versions, data dictionary, limitations and golden tests")
    tabs = st.tabs(["Demo script", "Audit trail", "Measures & rules", "Data & governance", "Golden tests"])
    with tabs[0]:
        _demo()
    with tabs[1]:
        _audit()
    with tabs[2]:
        _measures()
    with tabs[3]:
        _data()
    with tabs[4]:
        _golden()


def _demo() -> None:
    st.markdown('<div class="vbc-callout story"><div><b>Leadership storyline</b>The Encounter Pod does not stop after reimbursement. '
                'It uses evidence from every encounter to measure quality over time, identify care opportunities, coordinate action and prove '
                'whether outcomes improved.</div></div>', unsafe_allow_html=True)
    st.caption(catalog.POSITIONING)
    for number, title, expected, done, action in _demo_status():
        cols = st.columns([0.35, 3.2, 1, 1.3], vertical_alignment="center")
        cols[0].markdown(f'<div class="poc-step-no">{number}</div>', unsafe_allow_html=True)
        cols[1].markdown(f'<div class="nc-cell"><b>{escape(title)}</b><div class="nc-meta">{escape(expected)}</div></div>', unsafe_allow_html=True)
        cols[2].markdown(badge("Done" if done else "To do", "green" if done else "gray"), unsafe_allow_html=True)
        if action and action[1]:
            cols[3].button(action[0], key=f"poc_demo_{number}", type="tertiary", on_click=action[1])


def _audit() -> None:
    log = vbc.audit_log()
    events = log["events"]
    cols = st.columns(4)
    for col, (label, value, tone, context) in zip(cols, (
        ("Audit events", str(len(events)), "lav", "Append-only"),
        ("Hash chain", "Intact" if log["chain_intact"] else f"Broken at #{log['broken_at']}", "mint" if log["chain_intact"] else "rose",
         "Each event covers the previous hash"),
        ("Evaluation runs", str(len(log["runs"])), "sky", f"Rule {log['rule_version']}"),
        ("Permission denials", str(sum(1 for event in events if event["action"] == "rbac.denied")), "rose", "Blocked and logged"),
    ), strict=True):
        with col:
            render_kpi_card(label, value, tone, context)
    f1, f2, f3 = st.columns([1.4, 1.4, 2])
    actions = sorted({event["action"] for event in events})
    chosen = f1.multiselect("Action", actions, key="poc_audit_actions")
    object_type = f2.selectbox("Object", ("All",) + tuple(sorted({event["object_type"] for event in events})), key="poc_audit_obj")
    search = f3.text_input("Search", key="poc_audit_search", placeholder="Patient, gap, evidence or actor")
    rows = [event for event in events if (not chosen or event["action"] in chosen) and (object_type == "All" or event["object_type"] == object_type)
            and (not search or search.lower() in json.dumps(event).lower())]
    render_html_table(["#", "When", "Actor", "Action", "Object", "Previous → new", "Trigger", "Evidence", "Details", "Hash"], [
        [str(event["seq"]), escape(event["at"].replace("T", " ")), escape(event["actor"]), escape(event["action"]),
         escape(f"{event['object_type']} {event['object_id']}"), escape(f"{event['previous_state'] or '—'} → {event['new_state'] or '—'}"),
         escape(event["trigger"]), escape(", ".join(event["evidence_ids"][:4])), escape(event["details"][:110]), f"<code>{event['hash'][:10]}</code>"]
        for event in reversed(rows[-80:])
    ])
    st.caption(f"Showing {min(len(rows), 80)} of {len(rows)} matching events, newest first.")
    with st.container(horizontal=True, gap="small"):
        if st.button("Verify hash chain", key="poc_verify_chain", type="secondary"):
            ok, where = repo.verify_chain(repo.load())
            (st.success if ok else st.error)("Chain intact — no event altered or removed." if ok else f"Chain broken at event #{where}.")
        st.download_button("Export audit log (JSON)", json.dumps(log, indent=1, default=str), file_name="vbc_poc_audit.json", mime="application/json")
    st.caption(f"Viewed by {actor()}.")


def _measures() -> None:
    card_title("Measure inventory", "HEDIS-inspired prototype logic — public descriptions only, not licensed NCQA specifications")
    render_html_table(["Measure", "Question", "Population", "Evidence", "Target / window", "Exclusions", "Owner"], [
        [f"<b>{m.measure_id}</b><div class='nc-meta'>{escape(m.name)}</div>", escape(m.purpose),
         f"Age {m.age[0]}–{m.age[1]}" + (", female" if m.sex else "") + (f", {m.requires_condition}" if m.requires_condition else ""),
         escape(", ".join(catalog.CONCEPT_LABELS.get(c, c) for c in m.evidence_concepts)),
         escape(m.target) + (f"<div class='nc-meta'>{escape(m.outcome_rule)}</div>" if m.outcome_rule else ""),
         escape(", ".join(catalog.CONCEPT_LABELS.get(c, c) for c in m.exclusions)), "Quality analytics lead"]
        for m in catalog.MEASURES.values()
    ])
    left, right = st.columns(2, gap="medium")
    with left:
        card_title("Rule version and change log", f"Active: {catalog.RULE_VERSION} · every result stores the version that produced it")
        render_html_table(["Version", "Effective", "Change"], [[f"<b>{v}</b>", d, escape(text)] for v, d, text in catalog.RULE_CHANGE_LOG])
    with right:
        card_title("Measure result states", "Calculation state is separate from workflow state and disposition")
        render_html_table(["State", "Meaning", "System action"], [
            [state_badge(code), escape(info.meaning), escape(info.system_action)] for code, info in catalog.MEASURE_STATES.items()
        ])


def _data() -> None:
    st.markdown(f'<div class="vbc-callout guardrail"><div><b>Demo-data declaration</b>All {len(PATIENTS)} patients and {len(EVENTS)} '
                f'events are synthetic and fictional, generated for this proof of concept. {escape(catalog.DISCLAIMER)}</div></div>',
                unsafe_allow_html=True)
    left, right = st.columns(2, gap="medium")
    with left:
        card_title("Data dictionary and provenance", "Every clinical fact keeps source system, record ID, event date and ingestion date")
        render_html_table(["Entity", "Key fields"], [[f"<b>{escape(name)}</b>", escape(fields)] for name, fields in catalog.DATA_DICTIONARY])
        card_title("Source hierarchy", "Lower rank wins when duplicates need a primary record")
        render_html_table(["Rank", "Source"], [[str(rank), escape(label)] for key, (label, rank) in sorted(catalog.SOURCES.items(), key=lambda i: i[1][1])])
    with right:
        card_title("Known-limitations register", "What this POC does not prove, and the mitigation")
        render_html_table(["Limitation", "Impact", "Mitigation"], [[f"<b>{escape(a)}</b>", escape(b), escape(c)] for a, b, c in catalog.LIMITATIONS])
        card_title("Role-based access", "Enforced on every action; denials are logged")
        render_html_table(["Capability", "Roles"], [[escape(a), escape(b)] for a, b in catalog.ROLE_MATRIX])
        card_title("Safety controls", "")
        st.markdown("".join(f'<div class="nc-meta">• {escape(text)}</div>' for text in (
            "Synthetic data labelled on every page.", "Outreach and care-plan status changes need human approval.",
            "Risk scores are workload priority with visible factors, never a validated prediction.",
            "Free text never closes a gap; unverified extraction cannot be accepted.",
            "Incomplete data shows as evidence review or data-quality issue — never forced pass/fail.",
            "Gaps close only after accepted evidence and re-evaluation; orders and outreach never close them.")), unsafe_allow_html=True)


def _golden() -> None:
    rows = run_golden()
    passed = sum(row["passed"] for row in rows)
    card_title(f"Golden patient tests · {passed}/{len(rows)} passing",
               "Expected result for every patient-measure pair on the untouched synthetic history (also run by pytest)")
    render_html_table(["Patient", "Measure", "Expected", "Actual", "Result", "Scenario"], [
        [row["patient_id"], row["measure_id"], state_badge(row["expected"]), state_badge(row["actual"]),
         badge("Pass" if row["passed"] else "Fail", "green" if row["passed"] else "red"), escape(row["scenario"])]
        for row in rows if row["expected"] != "NOT_ELIGIBLE" or not row["passed"]
    ])
    st.caption("Not-eligible pairs are hidden when passing. Workflow, data-quality and audit tests run in app/tests/test_vbc_poc.py.")
