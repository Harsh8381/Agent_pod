"""Value and trust: S14 Survey insights, S15 Utilization & value, S16 Governance & audit, and the operating playbook."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from frontend.admin_service import ROLES, VBC_APPROVERS, VBC_ROLE_MATRIX, VBC_SCREENS
from frontend.ui.components import (
    badge,
    card_title,
    page_header,
    render_banner,
    render_html_table,
    render_kpi_card,
    render_tiles,
)
from frontend.ui.shell import render_page
from frontend.ui.state import go_to
from frontend.ui.theme import CHART_COLORS, CHART_NEUTRAL, PALETTE
from frontend.vbc import store as vbc_store
from frontend.vbc.catalog import (
    CLAIMS_RUNOUT,
    EVIDENCE_CHECKS,
    GAP_CLOSED,
    GAP_STATUSES,
    GUARDRAILS,
    MEASURES,
    MP,
    REASON_CODES,
    SOURCES,
    VALUE_SET_NOTICE,
    VALUE_SET_SOURCE,
    VALUE_SETS,
)
from frontend.vbc.engine import compute_rate
from frontend.vbc.feeds import SURVEY_DATA, TDABC_DIABETES_CYCLE
from frontend.vbc.playbook import (
    DECISIONS,
    EXCEPTION_FAMILIES,
    FIVE_JOBS,
    HAPPY_PATHS,
    STORY,
)
from frontend.views.vbc.common import (
    SCREEN_PAGES,
    SCREEN_TITLES,
    STATE_PATH,
    actor,
    callout,
    can_vbc,
    fmt,
    get_snapshot,
    guard,
    header,
    how_it_works,
    mutate,
    status_legend,
)
from frontend.views.vbc.gaps import SURVEY_TERMS
from frontend.views.vbc.prove import lineage_chain


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# S14 Survey insights
# ---------------------------------------------------------------------------


def show_surveys():
    render_page("vbc_surveys")
    snapshot = get_snapshot()
    header("S14", snapshot)
    render_banner("Survey integrity", SURVEY_DATA["integrity"], tone="warn", icon="&#9888;")
    threshold = SURVEY_DATA["reliability_threshold"]
    cahps_col, hos_col = st.columns([1.6, 1], gap="medium")
    with cahps_col, st.container(border=True):
        card_title("CAHPS · aggregate vendor results", "Scores out of 100 with respondent counts and movement since the last wave")
        cols = st.columns(len(SURVEY_DATA["cahps"]))
        for col, item in zip(cols, SURVEY_DATA["cahps"], strict=True):
            with col:
                tone = "rose" if item["change"] < 0 else "mint" if item["change"] > 0 else "sky"
                render_kpi_card(item["measure"], str(item["score"]), tone, f"n = {item['n']} · {item['direction']}")
    with hos_col, st.container(border=True):
        card_title("HOS · Health Outcomes Survey", f"Below n = {threshold} results are trend only — no targets are set from them")
        for item in SURVEY_DATA["hos"]:
            reliable = item["n"] >= threshold
            st.markdown(f'<div class="vbc-check"><span class="{"ok" if reliable else "hold"}">{"✓" if reliable else "~"}</span>'
                        f'<span>{escape(item["measure"])} · <b>{item["score"]}</b></span>'
                        f'<small>n = {item["n"]} · {"reliable" if reliable else "trend only"}</small></div>', unsafe_allow_html=True)

    routes = snapshot.state.get("survey_routes", {})
    theme_col, tools_col = st.columns([1.6, 1], gap="medium")
    with theme_col, st.container(border=True):
        card_title("Themes from de-identified comments", "Grouped, linked to the measure they affect and routed to an operational owner")
        widths = [3, 0.8, 1.6, 2, 1.4]
        for col, label in zip(st.columns(widths), ("Theme", "Mentions", "Affects", "Owner", ""), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        for item in SURVEY_DATA["themes"]:
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].markdown(f'<div class="nc-cell"><b>{escape(item["theme"])}</b></div>', unsafe_allow_html=True)
            cols[1].markdown(f'<div class="nc-cell">{item["mentions"]}</div>', unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell muted">{escape(item["linked"])}</div>', unsafe_allow_html=True)
            cols[3].markdown(f'<div class="nc-cell">{escape(item["owner"])}</div>', unsafe_allow_html=True)
            routed = routes.get(item["theme"])
            if routed:
                cols[4].markdown(badge(f"Routed {routed['at'][5:10]}", "green"), unsafe_allow_html=True)
            elif cols[4].button("Route", key=f"s14_route_{item['theme'][:24]}", type="tertiary", disabled=not can_vbc("S14", "Edit")):
                if guard("S14", "Edit", "route a survey theme"):
                    mutate("SRV", "Survey theme routed to owner", f"{item['theme']} → {item['owner']}",
                           lambda state, item=item: state["survey_routes"].update({item["theme"]: {"owner": item["owner"], "by": actor(), "at": _now_iso()}}),
                           toast=f"Routed to {item['owner']}")
                    st.rerun()
    with tools_col:
        with st.container(border=True):
            card_title("Check a draft message", "The communication gateway blocks anything that references a coming survey")
            draft = st.text_area("Draft", key="s14_draft", height=90, label_visibility="collapsed",
                                 placeholder="Paste an outreach draft to check it against the survey-integrity policy")
            if st.button("Run policy check", key="s14_check", type="secondary", disabled=not draft.strip()):
                hits = [term.strip() for term in SURVEY_TERMS if term in f" {draft.lower()} "]
                if hits:
                    mutate("SRV", "Draft referenced a survey: blocked", f"Terms: {', '.join(hits)}", outcome="Blocked")
                    st.error(f"Blocked — the draft references a survey ({', '.join(hits)}). Policy event logged.")
                else:
                    st.success("No survey reference found. The draft can go to template approval.")
        with st.container(border=True):
            card_title("Requests the agent refuses", "Logged when attempted")
            st.markdown('<div class="nc-meta">Listing low-scoring respondents, coaching people before a survey, or pre-screening who receives one.</div>',
                        unsafe_allow_html=True)
            if st.button("Request list of low-scoring respondents", key="s14_refuse", type="tertiary"):
                mutate("SRV", "Request to identify survey respondents refused", "Individual targeting is not permitted", outcome="Blocked")
                st.error("Refused. Survey results are aggregate only; individuals are contacted only with consent and never because of a survey.")
    with st.container(border=True):
        card_title("Care conversations that may help, at visits", "Offered to consenting patients only — never linked to who answered a survey")
        seniors = [person for person in snapshot.people if (person.age or 0) >= 65]
        fsa = compute_rate("COA-FSA", snapshot.evaluation)
        render_tiles((
            ("Medicare Advantage members 65+", str(len(seniors))),
            ("Consented to contact", str(sum(person.consent_phone for person in seniors))),
            ("Functional status recorded (COA-FSA)", f"{fsa.value:.1f}%" if fsa.value is not None else "—"),
            ("Fall-risk conversation", "Offered at the point-of-care card"),
        ))
    how_it_works("S14")


# ---------------------------------------------------------------------------
# S15 Utilization & value
# ---------------------------------------------------------------------------

BASE_PMPM = {"Medicare Advantage": 1182.0, "Commercial": 498.0}
TARGET_PMPM = {"Medicare Advantage": 1150.0, "Commercial": 505.0}
QUARTER_FACTORS = (("Q1 2026", 1.00, "Final"), ("Q2 2026", 0.97, "Final"), ("Q3 2026", 1.02, "Final"), ("Q4 2026", 1.01, "Estimated"))
PRICE_PER_CYCLE = 612.0  # illustrative chargemaster list price for the same cycle
REIMBURSEMENT_PER_CYCLE = 388.0  # illustrative contracted payment


def _ed_per_thousand(people) -> float:
    visits = sum(1 for person in people for encounter in person.encounters if str(encounter.get("Type")) == "Emergency")
    return visits / len(people) * 1000 if people else 0.0


def show_value():
    render_page("vbc_value")
    snapshot = get_snapshot()
    header("S15", snapshot)
    render_banner(f"Claims run-out at {CLAIMS_RUNOUT:.0%} for recent months",
                  "TCOC and PCR for the open quarter are ESTIMATED, with widened ranges; they are finalised as claims complete.", tone="warn", icon="&#9888;")
    product = st.radio("Product line", tuple(BASE_PMPM), horizontal=True, key="s15_product")
    people = [person for person in snapshot.people if person.product_line == product]
    ids = {person.person_id for person in people}
    frame = pd.DataFrame([
        {"Quarter": quarter, "PMPM": round(BASE_PMPM[product] * factor, 0), "Basis": basis,
         "Low": round(BASE_PMPM[product] * factor * (0.94 if basis == "Estimated" else 1), 0),
         "High": round(BASE_PMPM[product] * factor * (1.06 if basis == "Estimated" else 1), 0)}
        for quarter, factor, basis in QUARTER_FACTORS
    ])
    chart_col, cost_col = st.columns([1.5, 1], gap="medium")
    with chart_col, st.container(border=True):
        card_title(f"Total cost of care · PMPM · {product}", f"{len(people)} members · contract target ${TARGET_PMPM[product]:,.0f} (dashed)")
        axis = {"labelColor": PALETTE["muted"], "titleColor": PALETTE["muted"], "gridColor": PALETTE["border"], "domain": False}
        x = alt.X("Quarter:N", title=None, sort=None,
                  axis=alt.Axis(labelColor=PALETTE["muted"], domainColor=PALETTE["border"], tickColor=PALETTE["border"], labelAngle=0))
        y_scale = alt.Scale(zero=False, domain=[BASE_PMPM[product] * 0.88, BASE_PMPM[product] * 1.10])
        line_layer = alt.Chart(frame).mark_line(strokeWidth=2, color=CHART_COLORS[3]).encode(
            x=x, y=alt.Y("PMPM:Q", title="PMPM ($)", scale=y_scale, axis=alt.Axis(format="$,.0f", **axis)))
        range_layer = alt.Chart(frame[frame["Basis"] == "Estimated"]).mark_rule(strokeWidth=2, color=CHART_NEUTRAL).encode(
            x=x, y=alt.Y("Low:Q", scale=y_scale), y2="High:Q")
        point_layer = alt.Chart(frame).mark_point(filled=True, size=110, opacity=1, stroke=PALETTE["surface"], strokeWidth=2).encode(
            x=x, y=alt.Y("PMPM:Q", scale=y_scale),
            color=alt.Color("Basis:N", scale=alt.Scale(domain=["Final", "Estimated"], range=[CHART_COLORS[3], CHART_NEUTRAL]),
                            legend=alt.Legend(title=None, orient="top", labelColor=PALETTE["text"])),
            tooltip=["Quarter:N", "Basis:N", alt.Tooltip("PMPM:Q", format="$,.0f"), alt.Tooltip("Low:Q", format="$,.0f"), alt.Tooltip("High:Q", format="$,.0f")],
        )
        target_layer = alt.Chart(pd.DataFrame({"Target": [TARGET_PMPM[product]]})).mark_rule(strokeDash=[6, 4], strokeWidth=2, color=CHART_COLORS[1]).encode(
            y=alt.Y("Target:Q", scale=y_scale))
        chart = (line_layer + range_layer + target_layer + point_layer).properties(height=240).configure_view(strokeWidth=0).configure(
            font="Inter, 'Segoe UI', system-ui, sans-serif")
        st.altair_chart(chart, width="stretch")
        st.caption("Contract-defined TCOC with risk adjustment and truncation applied. The open quarter is an estimate; the grey bar is its range. "
                   "The dashed line is the contract target. Demo values.")
    with cost_col, st.container(border=True):
        card_title("Cost of one diabetes care cycle (TDABC)", "Process-map minutes × capacity cost rate — cost, not charges")
        total = sum(minutes * rate for _, minutes, rate in TDABC_DIABETES_CYCLE)
        render_html_table(["Activity", "Time / units", "Rate", "Cost"], [
            [escape(activity), f"{minutes:g}", f"${rate:,.2f}", f"${minutes * rate:,.2f}"] for activity, minutes, rate in TDABC_DIABETES_CYCLE
        ] + [["<b>Cost per cycle</b>", "", "", f"<b>${total:,.2f}</b>"]])
        render_tiles((("Cost (TDABC)", f"${total:,.0f}"), ("Price (list, illustrative)", f"${PRICE_PER_CYCLE:,.0f}"),
                      ("Reimbursement (contract, illustrative)", f"${REIMBURSEMENT_PER_CYCLE:,.0f}")))
        physician = next(rate for activity, _, rate in TDABC_DIABETES_CYCLE if activity.startswith("Physician"))
        assistant = next(rate for activity, _, rate in TDABC_DIABETES_CYCLE if activity.startswith("Medical assistant"))
        saving = 8 * (physician - assistant)
        callout("story", "Redesign opportunity",
                f"Moving 8 minutes of standing-order and education work from the physician to the medical assistant saves ${saving:,.2f} per cycle "
                f"({saving / total:.0%}) with no change to the HEDIS steps.")

    with st.container(border=True):
        card_title("Quality next to cost", f"Same cohort, same period ({MP.code}) — cost reductions are never shown without outcomes")
        pathways = (
            ("Diabetes", lambda person: person.has_condition(VALUE_SETS["Diabetes"]), ("GSD", "KED", "EED")),
            ("Hypertension", lambda person: person.has_condition(VALUE_SETS["Essential Hypertension"]), ("CBP",)),
            ("Preventive screening", lambda person: 45 <= (person.age or 0) <= 75, ("BCS-E", "COL-E")),
            ("After the hospital", lambda person: any(stay.person_id == person.person_id for stay in snapshot.evaluation.pcr_stays), ("TRC", "FMC")),
        )
        rows = []
        for name, test, codes in pathways:
            cohort = [person for person in people if test(person)]
            cohort_ids = {person.person_id for person in cohort}
            quality = " · ".join(f"{code} {rate.value:.0f}%" if (rate := compute_rate(code, snapshot.evaluation, cohort_ids)).value is not None else f"{code} —"
                                 for code in codes)
            pcr = compute_rate("PCR", snapshot.evaluation, cohort_ids)
            rows.append([f"<b>{escape(name)}</b>", str(len(cohort)), escape(quality),
                         f"{pcr.value:.2f} <span class='nc-meta'>est.</span>" if pcr.value is not None else "—",
                         f"{_ed_per_thousand(cohort):,.0f}",
                         f"${BASE_PMPM[product] * (1.18 if name == 'Diabetes' else 1.06 if name == 'Hypertension' else 1.0):,.0f} <span class='nc-meta'>est.</span>"])
        render_html_table(["Pathway", "Members", "HEDIS (internal)", "PCR O/E", "ED visits / 1,000", "TCOC PMPM"], rows)
    rules_col, note_col = st.columns(2, gap="medium")
    with rules_col:
        callout("guardrail", "Rule", "A savings view without HEDIS and readmission results is blocked. Cost, price and reimbursement are always separate lines.")
    with note_col:
        callout("handoff", "Scope", f"{len(ids)} {product} members in this view. Utilization family (HFO) is display-only; PCR is risk-adjusted against expected.")
    how_it_works("S15")


# ---------------------------------------------------------------------------
# S16 Governance & audit
# ---------------------------------------------------------------------------

FAMILY_TONES = {"RBAC": "red", "SRV": "red", "AUD": "amber", "VER": "violet", "EVD": "blue", "EXC": "gray", "SUP": "green", "OUT": "blue",
                "WRK": "gray", "TRC": "blue", "ABS": "violet", "PAY": "amber"}


def _closures(snapshot) -> list:
    return [result for result in snapshot.evaluation.results.values() if result.status == GAP_CLOSED and result.closing]


def show_governance():
    render_page("vbc_governance")
    snapshot = get_snapshot()
    header("S16", snapshot)
    state = snapshot.state
    log = state.get("audit", [])
    intact, broken_at = vbc_store.verify_chain(state)
    quarantine = {fact_id: item for fact_id, item in state.get("quarantine", {}).items() if not item.get("restored")}
    closures = _closures(snapshot)
    traced = [result for result in closures if result.closing.fact.source_ref and result.closing.fact.fact_id not in quarantine]
    blocked = [event for event in log if event["outcome"] == "Blocked"]
    render_banner("Reportable rates blocked until licensed value sets are loaded", f"{VALUE_SET_SOURCE}. {VALUE_SET_NOTICE}", tone="warn", icon="&#9888;")
    for col, (label, value, tone, context) in zip(st.columns(4), (
        ("Audit events", str(len(log)), "lav", "Append-only, hash-chained"),
        ("Chain integrity", "Intact" if intact else f"Broken at #{broken_at}", "mint" if intact else "rose", "Every event covers the previous hash"),
        ("Closures fully traced", f"{len(traced)}/{len(closures)}", "sky", "Version, evidence, source and message ID"),
        ("Policy blocks", str(len(blocked)), "rose", "Permission, survey and outreach guardrails"),
    ), strict=True):
        with col:
            render_kpi_card(label, value, tone, context)

    trace_col, quarantine_col = st.columns([1.5, 1], gap="medium")
    with trace_col, st.container(border=True):
        card_title("Explain any closure", "Pick a closed gap to see the full lineage behind it")
        if closures:
            options = sorted(closures, key=lambda result: (result.measure, result.person_id))
            labels = {result.key: f"{result.measure} · {snapshot.person(result.person_id).name} · {result.closing.fact.description[:34]}" for result in options}
            chosen = st.selectbox("Closure", list(labels), format_func=labels.get, key="s16_closure")
            result = next(item for item in options if item.key == chosen)
            lineage_chain(snapshot, result.closing)
        else:
            st.caption("No closures yet.")
    with quarantine_col, st.container(border=True):
        card_title("Quarantine", "Accepted closures whose lineage is incomplete are pulled from reported results")
        if not quarantine:
            st.success("Nothing quarantined — every accepted closure traces end to end.")
        for fact_id, item in quarantine.items():
            person = snapshot.person(item.get("person_id", ""))
            st.markdown(f'<div class="nc-cell"><b>{escape(item.get("measure", ""))} · {escape(person.name if person else item.get("person_id", ""))}</b>'
                        f'<div class="nc-meta">{escape(item["reason"])} · since {item["at"][:10]} · <code>{escape(fact_id)}</code></div></div>', unsafe_allow_html=True)
            ref = st.text_input("Recovered source message ID", key=f"s16_ref_{fact_id}", placeholder="e.g. HL7 MSH-10 from the interface engine log")
            if st.button("Restore lineage", key=f"s16_restore_{fact_id}", type="primary", disabled=not ref.strip() or not can_vbc("S16", "Approve")):
                if guard("S16", "Approve", "restore quarantined lineage"):
                    def change(state_, fact_id=fact_id, ref=ref):
                        state_["quarantine"][fact_id].update({"restored": True, "restored_ref": ref.strip(), "restored_by": actor(), "restored_at": _now_iso()})
                    mutate("AUD", "Lineage restored", f"{fact_id}: source message {ref.strip()}", change, outcome="Restored", refs={"fact": fact_id},
                           toast="Lineage restored — closure counts again")
                    st.rerun()
        restored = [(fact_id, item) for fact_id, item in state.get("quarantine", {}).items() if item.get("restored")]
        if restored:
            st.caption("Restored: " + ", ".join(f"{fact_id} by {item.get('restored_by')}" for fact_id, item in restored))

    with st.container(border=True):
        title_col, verify_col = st.columns([3, 1], vertical_alignment="bottom")
        with title_col:
            card_title("Audit log", "Typed events — who did what, when, under which version, with which outcome")
        if verify_col.button("Verify hash chain", key="s16_verify", type="secondary"):
            ok, where = vbc_store.verify_chain(vbc_store.load_state(STATE_PATH))
            mutate("AUD", "Hash chain verified", f"{len(log)} events", outcome="Intact" if ok else f"Broken at #{where}")
            (st.success if ok else st.error)("Chain intact — no event has been altered or removed." if ok else f"Chain broken at event #{where}.")
        f1, f2, f3 = st.columns([2, 1, 2])
        families = f1.multiselect("Family", tuple(vbc_store.AUDIT_FAMILIES), format_func=lambda key: f"{key} · {vbc_store.AUDIT_FAMILIES[key]}", key="s16_families")
        outcome = f2.selectbox("Outcome", ("All", "Blocked", "Recorded", "Other"), key="s16_outcome")
        search = f3.text_input("Search", key="s16_search", placeholder="Person, measure, batch or actor")
        events = [
            event for event in log
            if (not families or event["family"] in families)
            and (outcome == "All" or event["outcome"] == outcome or (outcome == "Other" and event["outcome"] not in ("Blocked", "Recorded")))
            and (not search or search.lower() in json.dumps(event).lower())
        ]
        render_html_table(["#", "When", "Family", "Actor", "Action", "Detail", "Outcome", "Hash"], [
            [str(event["seq"]), escape(event["at"][:16].replace("T", " ")), badge(event["family"], FAMILY_TONES.get(event["family"], "gray")),
             escape(event["actor"]), escape(event["action"]), escape(event["detail"][:90]),
             badge(event["outcome"], "red" if event["outcome"] == "Blocked" else "gray"), f"<code>{event['hash'][:10]}</code>"]
            for event in events[::-1][:40]
        ])
        st.caption(f"Showing {min(len(events), 40)} of {len(events)} matching events, newest first.")

    export_col, systems_col = st.columns([1, 1.6], gap="medium")
    with export_col, st.container(border=True):
        card_title("Serve the auditor", "Lineage, versions, approvals and the full audit log in one file")
        package = {
            "exported_at": _now_iso(), "measurement_period": MP.label, "version": snapshot.version, "value_sets": VALUE_SET_SOURCE,
            "versions": state.get("versions", []), "change_requests": state.get("change_requests", {}), "snapshots": state.get("snapshots", {}),
            "closures": [{"person_id": result.person_id, "measure": result.measure, "fact_id": result.closing.fact.fact_id,
                          "source": result.closing.fact.source, "source_ref": result.closing.fact.source_ref,
                          "date_of_service": result.closing.fact.on.isoformat() if result.closing.fact.on else None} for result in closures],
            "quarantine": state.get("quarantine", {}), "audit": log, "chain_intact": intact,
        }
        st.download_button("Download auditor package (JSON)", json.dumps(package, indent=2, default=str), file_name=f"vbc_audit_{snapshot.as_of}.json",
                           mime="application/json", type="primary", disabled=not can_vbc("S16", "View"),
                           on_click=lambda: mutate("AUD", "Auditor package exported", f"{len(log)} events · {len(closures)} closures"))
        frozen = state.get("snapshots", {})
        render_tiles((("Versions published", str(len(state.get("versions", [])))), ("Frozen snapshots", str(len(frozen)))))
        can_freeze = snapshot.as_of > MP.end
        st.button("Freeze results at period close", key="s16_freeze", type="secondary", disabled=True if not can_freeze else not can_vbc("S16", "Approve"))
        if not can_freeze:
            st.caption(f"Available once {MP.code} closes on {fmt(MP.end)}; results are then frozen under the versions that produced them.")
    with systems_col, st.container(border=True):
        card_title("Information systems documentation", "Every feed the measures depend on, with its format and freshness")
        render_html_table(["Feed", "Kind", "Last message", "Expected", "Status"], [
            [escape(feed["feed"]), escape(feed["kind"]), fmt(feed["last"]), f"every {feed['expected_hours']} h",
             badge("Stale", "red") if feed in snapshot.stale_feeds() else badge("Healthy", "green")]
            for feed in snapshot.feeds
        ])
        st.markdown('<div class="nc-meta" style="margin-top:.5rem">Evidence sources: ' + " · ".join(
            f"{escape(source.label)} ({escape(source.format)})" for source in SOURCES.values()) + "</div>", unsafe_allow_html=True)
    how_it_works("S16")


# ---------------------------------------------------------------------------
# Operating playbook (the deck's reference material)
# ---------------------------------------------------------------------------


def show_playbook():
    render_page("vbc_playbook")
    page_header("Value-based care · Reference", "Operating playbook",
                "Screen-by-screen flow from the functional specification: jobs, statuses, checks, guardrails, scenarios and roles")
    st.markdown('<div class="nc-meta" style="margin-bottom:.6rem">Sixteen screens in five jobs. Every screen follows the same pattern: '
                "trigger → flow → happy path → exception paths → guardrail → hand-off.</div>", unsafe_allow_html=True)
    for col, (job, purpose, screens) in zip(st.columns(len(FIVE_JOBS)), FIVE_JOBS, strict=True):
        with col, st.container(border=True):
            card_title(job, purpose)
            for code in screens:
                st.button(f"{code} {SCREEN_TITLES[code]}", key=f"pb_go_{code}", type="tertiary", on_click=go_to, args=(SCREEN_PAGES[code],),
                          disabled=not can_vbc(code, "View"))

    status_col, check_col = st.columns(2, gap="medium")
    with status_col, st.container(border=True):
        card_title("The seven gap statuses", "Unknown is not open; preference is not exclusion")
        st.markdown(status_legend(), unsafe_allow_html=True)
        render_html_table(["Status", "Meaning", "Counts as", "Work goes to"], [
            [badge(status.code, status.tone), escape(status.meaning), escape(status.counts), escape(status.work_goes)] for status in GAP_STATUSES.values()
        ])
    with check_col, st.container(border=True):
        card_title("The eight evidence checks", "Every closure passes all eight; the first failure sets the reason code")
        render_html_table(["#", "Check", "Code", "If it fails", "Owner"], [
            [str(index), escape(label), f"<code>{code}</code>", escape(REASON_CODES[code][0]), escape(REASON_CODES[code][1])]
            for index, (_, label, code) in enumerate(EVIDENCE_CHECKS, start=1)
        ])

    guard_col, story_col = st.columns(2, gap="medium")
    with guard_col, st.container(border=True):
        card_title("Eight guardrails", "What the agent will never do")
        render_html_table(["Never", "Because"], [[f"<b>{escape(title)}</b>", escape(text)] for title, text in GUARDRAILS])
    with story_col, st.container(border=True):
        card_title("Margaret's year", "One person through the screens, end to end")
        ticks = "".join(f'<div class="vbc-tick"><i class="tone-violet"></i><b>{code}</b>{escape(title)}</div>' for code, title, _ in STORY)
        st.markdown(f'<div class="vbc-timeline">{ticks}</div>', unsafe_allow_html=True)
        render_html_table(["Screen", "What happens"], [[f"<b>{code}</b> {escape(title)}", escape(text)] for code, title, text in STORY])

    paths_col, exceptions_col = st.columns(2, gap="medium")
    with paths_col, st.container(border=True):
        card_title("Happy paths", "End-to-end scenarios the build must pass")
        render_html_table(["Code", "Scenario", "Screens"], [[f"<code>{code}</code>", escape(text), escape(screens)] for code, text, screens in HAPPY_PATHS])
    with exceptions_col, st.container(border=True):
        card_title("Exception paths", "U01–U34, grouped by family and the screen that handles them")
        render_html_table(["Family", "Codes", "Handled in"], [[escape(family), escape(codes), escape(screens)] for family, codes, screens in EXCEPTION_FAMILIES])

    with st.container(border=True):
        card_title("Roles and screens", "P = primary user · O = occasional · ✓ = may approve")
        roles = [role for role in ROLES if any(role in VBC_ROLE_MATRIX[screen] for screen in VBC_SCREENS)]
        rows = []
        for role in roles:
            cells = []
            for screen in VBC_SCREENS:
                usage = VBC_ROLE_MATRIX[screen].get(role, "")
                mark = "✓" if role in VBC_APPROVERS.get(screen, ()) else ""
                cells.append(badge(usage + mark, "violet" if usage == "P" else "gray") if usage else "")
            rows.append([f"<b>{escape(role)}</b>"] + cells)
        render_html_table(["Role"] + list(VBC_SCREENS), rows)
    with st.container(border=True):
        card_title("Decisions before build", "Owned by the programme; each touches the screens shown")
        render_html_table(["Decision", "Question", "Screens"], [[f"<b>{escape(name)}</b>", escape(question), escape(screens)] for name, question, screens in DECISIONS])
    rated = Counter(measure.data_method for measure in MEASURES.values())
    st.caption("Measures configured: " + " · ".join(f"{count} {method}" for method, count in rated.items()) + f" · value sets: {VALUE_SET_SOURCE}.")
    callout("guardrail", "Labelled honestly", "All rates in this workspace are internal calculations on demo data, not certified HEDIS results.")
