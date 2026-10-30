"""Work the gaps: S03 Gap workbench, S04 Point-of-care card, S05 Person profile, S11 Outreach console."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from html import escape

import streamlit as st

from frontend.ui.components import badge, card_title, render_html_table, render_tiles
from frontend.ui.shell import render_page
from frontend.ui.state import can, go_to
from frontend.vbc import store as vbc_store
from frontend.vbc.catalog import (
    EXCLUSION_SUPPRESS_CONFIDENCE,
    GAP_OPEN,
    MEASURES,
    MP,
    OUTREACH_ATTEMPT_LIMIT,
    OUTREACH_RECONTACT_DAYS,
    PERSON_MEASURES,
    QUIET_HOURS,
    TEAM_WEEKLY_CAPACITY,
    WORKABLE_STATUSES,
)
from frontend.vbc.engine import CARE_MANAGERS
from frontend.vbc.evidence import Fact
from frontend.vbc.population import parse_date
from frontend.vbc.stars import ACTION_GROUPS, rank_gaps
from frontend.views.vbc.common import (
    actor,
    bars,
    callout,
    can_vbc,
    default_person,
    get_snapshot,
    guard,
    header,
    how_it_works,
    mutate,
    open_evidence,
    open_person,
    person_banner,
    pick_person,
    post_fact,
    status_badge,
)

ROUTE_PAGES = {"visit": "vbc_poc", "outreach": "vbc_outreach", "evidence": "vbc_person", "records": "vbc_person", "await": "vbc_evidence",
               "review": "vbc_evidence", "transition": "vbc_transitions"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _suppressed(snapshot) -> set:
    """(person, measure) pairs paused while a high-confidence exclusion candidate is reviewed."""
    decisions = snapshot.state.get("exclusion_decisions", {})
    return {
        (candidate.person_id, measure)
        for candidate in snapshot.candidates()
        if candidate.confidence >= EXCLUSION_SUPPRESS_CONFIDENCE
        and decisions.get(candidate.candidate_id, {}).get("decision") not in ("confirmed", "rejected")
        for measure in candidate.measures
    }


# ---------------------------------------------------------------------------
# S03 Gap workbench
# ---------------------------------------------------------------------------


def show_gaps():
    render_page("vbc_gaps")
    snapshot = get_snapshot()
    header("S03", snapshot)
    days_left = max((MP.end - snapshot.as_of).days, 0)
    f1, f2, f3, f4, f5 = st.columns([1.4, 2.2, 1.2, 1.4, 1.2], vertical_alignment="bottom")
    measure_filter = f1.selectbox("Measure", ("All",) + tuple(code for code in MEASURES if code != "PCR"), key="s03_measure")
    status_filter = f2.multiselect("Status", WORKABLE_STATUSES, default=list(WORKABLE_STATUSES), key="s03_status")
    due_filter = f3.selectbox("Due", ("Any", "≤ 7 days", "≤ 30 days", "≤ 90 days"), index=3, key="s03_due")
    owners = ("My team", "Me", "Unassigned") + CARE_MANAGERS
    owner_filter = f4.selectbox("Owner", owners, key="s03_owner")
    barrier_filter = f5.selectbox("Barrier", ("Any", "transport", "cost", "None"), key="s03_barrier")
    hide_closed = st.toggle("Hide gaps whose window has already closed", value=True, key="s03_hide_closed")

    me = actor()
    results = [
        result for result in snapshot.evaluation.results.values()
        if result.status in status_filter and (measure_filter == "All" or result.measure == measure_filter)
        and not (hide_closed and result.route == "none" and result.likelihood == 0 and result.measure in ("TRC", "FMC"))
    ]
    ranked = rank_gaps(results, snapshot.as_of)
    limit = {"Any": 10**6, "≤ 7 days": 7, "≤ 30 days": 30, "≤ 90 days": 90}[due_filter]
    assignments = snapshot.state.get("assignments", {})

    def keep(item) -> bool:
        person = snapshot.person(item.person_id)
        if item.days_left > limit:
            return False
        if barrier_filter == "None" and person.barriers or barrier_filter not in ("Any", "None") and barrier_filter not in person.barriers:
            return False
        owners_of = {assignments.get(gap.key, {}).get("owner") or gap.owner for gap in item.gaps}
        if owner_filter == "Me":
            return me in owners_of
        if owner_filter == "Unassigned":
            return not any(gap.key in assignments for gap in item.gaps)
        if owner_filter in CARE_MANAGERS:
            return owner_filter in owners_of
        return True

    ranked = [item for item in ranked if keep(item)]
    total_gaps = sum(len(item.gaps) for item in ranked)
    st.markdown(f'<div class="nc-meta" style="text-align:right">{total_gaps} gaps · {len(ranked)} people · {days_left} days left in {MP.code}</div>',
                unsafe_allow_html=True)

    with st.container(border=True):
        widths = [0.35, 2.0, 1.4, 1.6, 3.0, 0.9, 1.3, 2.2]
        for col, label in zip(st.columns(widths), ("", "Person", "Measures", "Status", "Why it is open", "Priority", "Owner", "Next action"), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        selected = []
        for item in ranked[:30]:
            person = snapshot.person(item.person_id)
            lead = item.lead
            statuses = Counter(gap.status for gap in item.gaps)
            status_html = " ".join(f"{status_badge(status)}{f' ×{count}' if count > 1 else ''}" for status, count in statuses.items())
            owner = assignments.get(lead.key, {}).get("owner") or lead.owner or "—"
            with st.container(key=f"coderow_{'sel' if st.session_state.get('s03_explain') == item.person_id else 'off'}_{item.person_id}"):
                cols = st.columns(widths, vertical_alignment="center")
                if cols[0].checkbox("select", key=f"s03_pick_{item.person_id}", label_visibility="collapsed"):
                    selected.append(item)
                cols[1].markdown(f'<div class="nc-cell"><b>{escape(person.name)}, {person.age}</b><div class="nc-meta">{escape(person.payer_name)} · {escape(person.site)}</div></div>',
                                 unsafe_allow_html=True)
                cols[2].markdown(f'<div class="nc-cell">{" · ".join(gap.measure for gap in item.gaps)}</div>', unsafe_allow_html=True)
                cols[3].markdown(status_html, unsafe_allow_html=True)
                cols[4].markdown(f'<div class="nc-cell muted">{escape(lead.reason)}</div>', unsafe_allow_html=True)
                cols[5].markdown(badge(item.priority_label, {"High": "red", "Med": "amber", "Low": "gray"}[item.urgency_band]), unsafe_allow_html=True)
                cols[6].markdown(f'<div class="nc-cell">{escape(owner)}</div>', unsafe_allow_html=True)
                page = ROUTE_PAGES.get(lead.route, "vbc_person")
                cols[7].button(lead.next_action or "Open profile", key=f"s03_next_{item.person_id}", type="tertiary",
                               on_click=open_person, args=(item.person_id, page),
                               disabled=not can_vbc({"vbc_poc": "S04", "vbc_outreach": "S11", "vbc_evidence": "S06", "vbc_transitions": "S10"}.get(page, "S05"), "View"))
        if len(ranked) > 30:
            st.caption(f"Showing the top 30 of {len(ranked)} people. Narrow the filters to see the rest — nothing is dropped.")
        if not ranked:
            st.info("No gaps match these filters.")

    explain_col, capacity_col = st.columns([1.3, 1], gap="medium")
    with explain_col:
        with st.container(border=True):
            if ranked:
                options = {item.person_id: f"#{item.rank} {snapshot.person(item.person_id).name}" for item in ranked[:30]}
                title_col, pick_col = st.columns([1.4, 1], vertical_alignment="bottom")
                pick = pick_col.selectbox("Explain", list(options), format_func=options.get, key="s03_explain", label_visibility="collapsed")
                item = next(entry for entry in ranked if entry.person_id == pick)
                with title_col:
                    card_title(f"Why {snapshot.person(pick).name.split()[0]} is #{item.rank}", "Components shown separately — Star weight only breaks ties")
                bars([
                    ("Clinical urgency", item.lead.urgency_note, item.urgency, 1, f"{item.urgency_band} · {item.urgency:.2f}", "red"),
                    ("Days left in window", "", max(0, 120 - item.days_left), 120, f"{item.days_left}", "amber"),
                    ("Closure likelihood", "", item.likelihood, 1, f"{item.likelihood:.2f}", "green"),
                    ("Gaps closed by one action", ACTION_GROUPS.get(item.lead.measure, ""), item.one_action, 4, str(item.one_action), "blue"),
                    ("Star weight (max)", "tie-breaker only", item.star_weight, 3, str(item.star_weight), "violet"),
                ])
            else:
                card_title("Priority explanation", "Select a person in the queue")
    with capacity_col:
        with st.container(border=True):
            week = snapshot.as_of.isocalendar()[:2]
            assigned_this_week = sum(
                1 for item in assignments.values()
                if (parse_date(item.get("at")) or date.min).isocalendar()[:2] == week
            )
            card_title("Team capacity this week", "Work is assigned within capacity; anything beyond is deferred with a reason")
            st.markdown(f'<div class="vbc-big">{assigned_this_week} <small>/ {TEAM_WEEKLY_CAPACITY} tasks assigned</small></div>', unsafe_allow_html=True)
            bars([("Capacity used", "", assigned_this_week, TEAM_WEEKLY_CAPACITY, f"{assigned_this_week / TEAM_WEEKLY_CAPACITY:.0%}", "blue")])
            owner = st.selectbox("Assign selected to", (me,) + CARE_MANAGERS, key="s03_assign_to")
            with st.container(horizontal=True, gap="small"):
                assign = st.button(f"Assign selected ({len(selected)})", key="s03_assign", type="primary",
                                   disabled=not selected or not can_vbc("S03", "Edit"))
                bulk = st.button("Bulk outreach", key="s03_bulk", type="secondary", disabled=not selected or not can_vbc("S11", "Edit"))
            st.caption("Bulk outreach is limited to non-clinical messages for open screening gaps.")
            if assign and guard("S03", "Edit", "assign gaps"):
                _assign(selected, owner, assigned_this_week)
                st.rerun()
            if bulk and guard("S11", "Edit", "queue bulk outreach"):
                _queue_outreach(snapshot, selected)
                st.rerun()
    how_it_works("S03")


def _assign(selected, owner, assigned_this_week):
    room = TEAM_WEEKLY_CAPACITY - assigned_this_week
    gaps = [gap for item in selected for gap in item.gaps]
    within, deferred = gaps[: max(room, 0)], gaps[max(room, 0):]

    def change(state):
        for gap in within:
            state["assignments"][gap.key] = {"owner": owner, "by": actor(), "at": _now_iso(),
                                             "due": (date.today() + timedelta(days=7)).isoformat()}
        for gap in deferred:
            state["deferred"][gap.key] = {"reason": "Beyond weekly team capacity", "at": _now_iso(), "by": actor()}

    mutate("WRK", "Gaps assigned", f"{len(within)} to {owner}" + (f"; {len(deferred)} deferred (capacity)" if deferred else ""), change,
           toast=f"{len(within)} gap(s) assigned" + (f", {len(deferred)} deferred with reason" if deferred else ""))


def _queue_outreach(snapshot, selected):
    eligible = [gap for item in selected for gap in item.gaps if gap.status == GAP_OPEN and gap.measure in ("COL-E", "BCS-E", "EED", "AIS-E")]

    def change(state):
        queued = {(entry["person_id"], entry["measure"]) for entry in state["outreach_queue"]}
        for gap in eligible:
            if (gap.person_id, gap.measure) not in queued:
                state["outreach_queue"].append({"person_id": gap.person_id, "measure": gap.measure, "at": _now_iso(), "by": actor()})

    mutate("OUT", "Queued for outreach from workbench", f"{len(eligible)} open screening gap(s)", change,
           toast=f"{len(eligible)} gap(s) queued in the Outreach console")


# ---------------------------------------------------------------------------
# S04 Point-of-care card
# ---------------------------------------------------------------------------

SCREENING_ORDERS = {"GSD": ("order", "Order HbA1c"), "KED": ("order", "Order uACR + eGFR"), "EED": ("referral", "Refer to eye care"),
                    "BCS-E": ("order", "Order mammogram"), "COL-E": ("order", "Order FIT kit"), "CBP": ("order", "Schedule BP recheck"),
                    "SPD": ("order", "Prescribe statin"), "COA-FSA": ("order", "Complete assessment"), "AIS-E": ("order", "Give flu vaccine")}


def _latest_vitals(person):
    vitals = [row for row in person.vitals if row.get("Systolic_mmHg") not in (None, "")]
    return vitals[-1] if vitals else None


def _personal_target(person) -> str:
    frail = (person.age or 0) >= 75
    return ("HbA1c < 8.0% — looser individual target set by the PCP for an older adult; shown beside the HEDIS level, never replaced by it"
            if frail else "HbA1c < 7.0% — individual target set by the PCP; shown beside the HEDIS level, never replaced by it")


def show_poc():
    render_page("vbc_poc")
    snapshot = get_snapshot()
    header("S04", snapshot)
    visits = sorted((p for p in snapshot.people if p.upcoming_visit), key=lambda p: (p.upcoming_visit["date"], p.upcoming_visit["time"]))
    pick_col, info_col = st.columns([2, 3], vertical_alignment="bottom")
    with pick_col:
        if visits and st.session_state.get("vbc_person") not in {p.person_id for p in snapshot.people}:
            with_gaps = [p for p in visits if any(r.status in WORKABLE_STATUSES and MEASURES[r.measure].kind == "person"
                                                  for r in snapshot.evaluation.for_person(p.person_id))]
            st.session_state.vbc_person = (with_gaps or visits)[0].person_id
        person = pick_person(snapshot, "s04_person", visits + [p for p in snapshot.people if not p.upcoming_visit], "Patient (today's visits first)")
    if person is None:
        st.info("No patients available.")
        return
    visit = person.upcoming_visit
    gaps = [result for result in snapshot.evaluation.for_person(person.person_id)
            if result.status in WORKABLE_STATUSES and MEASURES[result.measure].kind == "person"]
    ranked = sorted(gaps, key=lambda result: (-result.urgency, -MEASURES[result.measure].star_weight))
    with info_col:
        when = "No upcoming visit" if not visit else (
            f"Visit {'today' if visit['date'] == snapshot.as_of else visit['date'].strftime('%d %b')} {visit['time']} · {visit['provider']} · {visit['location']}")
        st.markdown(f'<div class="nc-badge-row" style="justify-content:flex-end">{badge(when, "blue")}'
                    f'{badge(f"{len(ranked)} open gap(s) · 1 alert max", "red" if ranked else "green")}</div>', unsafe_allow_html=True)

    context_col, gaps_col = st.columns([1, 1.6], gap="medium")
    with context_col:
        with st.container(border=True):
            card_title(f"{person.name} · {person.age} {person.sex[:1]}", "Visit context")
            problems = ", ".join(problem["name"] for problem in person.problems if problem["active"]) or "None recorded"
            vitals = _latest_vitals(person)
            a1c = sorted((row for row in person.labs if "a1c" in str(row.get("Test_Name", "")).lower()), key=lambda row: str(row.get("Result_Date")))
            render_tiles((
                ("Problems", problems),
                ("Latest vitals", f"BP {vitals['Systolic_mmHg']}/{vitals['Diastolic_mmHg']} · {str(vitals.get('Observed_Date'))[:10]}" if vitals else "—"),
                ("Last HbA1c", f"{a1c[-1]['Result']}% · {str(a1c[-1]['Result_Date'])[:10]}" if a1c else "—"),
                ("Allergies", ", ".join(str(row.get("Substance")) for row in person.allergies) or "NKDA"),
            ))
            st.markdown(f'<div class="nc-meta" style="margin-top:.6rem"><b>Clinician goal</b> · {escape(_personal_target(person))}</div>',
                        unsafe_allow_html=True)
        with st.container(border=True):
            card_title("Capture during this visit", "Structured vitals from the ambient scribe; CPT II proposed for the coder")
            with st.form(f"s04_bp_{person.person_id}", clear_on_submit=True):
                sys_col, dia_col = st.columns(2)
                systolic = sys_col.number_input("Systolic", 70, 260, 128, key=f"s04_sys_{person.person_id}")
                diastolic = dia_col.number_input("Diastolic", 40, 160, 80, key=f"s04_dia_{person.person_id}")
                record = st.form_submit_button("Record structured BP", disabled=not can_vbc("S04", "Edit"))
            if record and guard("S04", "Edit", "record visit vitals"):
                _record_bp(person, int(systolic), int(diastolic))
                st.rerun()

    with gaps_col:
        with st.container(border=True):
            card_title("HEDIS gaps for this visit", "Closure rule in plain language; the clinician decides — the agent never places orders")
            if not ranked:
                st.success("No open HEDIS gaps for this person.")
            if {"GSD", "KED"} <= {result.measure for result in ranked}:
                callout("story", "One lab draw closes two gaps", "HbA1c and uACR + eGFR can be drawn together today.")
            for index, result in enumerate(ranked):
                _gap_card(snapshot, person, result, alert=index == 0)
    _captured(snapshot, person)
    how_it_works("S04")


def _gap_card(snapshot, person, result, alert: bool):
    measure = MEASURES[result.measure]
    found = [item.detail for item in result.components if item.met]
    missing = [item.detail for item in result.components if not item.met]
    st.markdown(
        f'<div class="vbc-gap{" alert" if alert else ""}"><div class="head"><b>{measure.code} · weight {measure.star_weight or "display"}</b>'
        f'{status_badge(result.status)}</div><div class="rule">{escape(measure.closure_rule)}</div>'
        f'<div class="evidence">Evidence: {escape("; ".join(found) or "none in " + MP.code)}'
        + (f" · Missing: {escape('; '.join(missing))}" if missing else "") + "</div></div>",
        unsafe_allow_html=True,
    )
    if result.status != GAP_OPEN:
        st.caption(f"{result.status.replace('_', ' ').title()}: {result.next_action}")
        return
    kind, label = SCREENING_ORDERS.get(result.measure, ("order", measure.action_label))
    allowed = can_vbc("S04", "Approve")
    with st.container(horizontal=True, gap="small"):
        order = st.button(label, key=f"s04_order_{result.key}", type="primary", disabled=not allowed or "awaiting result" in result.next_action)
        with st.popover("Decline · reason", disabled=not allowed):
            reason = st.text_input("Clinical reason", key=f"s04_decline_text_{result.key}", placeholder="e.g. intensification risky after recent falls")
            decline = st.button("Record decline", key=f"s04_decline_{result.key}", disabled=not reason.strip())
        with st.popover("Preference", disabled=not allowed):
            pref = st.text_input("Patient preference", key=f"s04_pref_text_{result.key}", placeholder="e.g. declines colonoscopy; FIT offered")
            review = st.text_input("Review / re-offer date", key=f"s04_pref_review_{result.key}", value="Next period")
            preference = st.button("Record preference", key=f"s04_pref_{result.key}", disabled=not pref.strip())
    if order and guard("S04", "Approve", "place an order"):
        mutate("WRK", f"{label} — clinician order", f"{person.person_id} · {result.measure}",
               lambda state: vbc_store.add_action(state, actor(), person.person_id, result.measure, kind, label),
               toast=f"{label} recorded. The gap stays open until a final result passes the checks.")
        st.rerun()
    if decline and guard("S04", "Approve", "record a clinical decline"):
        mutate("WRK", "Clinician declined — conflict review", f"{person.person_id} · {result.measure}: {reason}",
               lambda state: vbc_store.add_action(state, actor(), person.person_id, result.measure, "decline", reason.strip()),
               toast="Decline recorded with reason (CONFLICT_REVIEW)")
        st.rerun()
    if preference and guard("S04", "Approve", "record a patient preference"):
        mutate("WRK", "Preference recorded", f"{person.person_id} · {result.measure}: {pref}",
               lambda state: vbc_store.add_action(state, actor(), person.person_id, result.measure, "preference", pref.strip(), reoffer=review.strip()),
               toast="PREFERENCE_RECORDED — stays in the denominator")
        st.rerun()


def _record_bp(person, systolic: int, diastolic: int):
    today = date.today()
    fact = Fact(f"POC-BP-{person.person_id}-{datetime.now():%H%M%S}", person.person_id, "bp", "LOINC", "85354-9",
                "Blood pressure (visit, ambient scribe)", today, datetime.now(), "ehr", f"Scribe capture {datetime.now():%Y%m%d%H%M%S}",
                value=(systolic, diastolic), unit="mmHg", provider=actor())
    systolic_code = "3074F" if systolic < 130 else "3075F" if systolic < 140 else "3077F"
    diastolic_code = "3078F" if diastolic < 80 else "3079F" if diastolic < 90 else "3080F"

    def change(state):
        post_fact(state, fact)
        state["cpt2_proposals"].append({"id": vbc_store.next_id(state, "CPT2"), "person_id": person.person_id, "codes": [systolic_code, diastolic_code],
                                        "measure": "CBP", "on": today.isoformat(), "status": "Proposed", "proposed_by": "Evidence capture agent",
                                        "at": _now_iso(), "reading": f"{systolic}/{diastolic}"})

    mutate("WRK", "Structured BP captured at visit", f"{person.person_id} · {systolic}/{diastolic}; CPT II {systolic_code}+{diastolic_code} proposed",
           change, toast="BP captured; CPT II proposal sent to the coder queue")


def _captured(snapshot, person):
    actions = [action for action in snapshot.state.get("actions", []) if action["person_id"] == person.person_id]
    proposals = [item for item in snapshot.state.get("cpt2_proposals", []) if item["person_id"] == person.person_id]
    with st.container(border=True):
        card_title("Captured for this person", "Orders, referrals and CPT II proposals are logged; the coder confirms codes, never the agent")
        events = [(action["at"], f"{action['detail']} · {action['measure']}", action["by"], action["kind"]) for action in actions]
        events += [(item["at"], f"CPT II {' + '.join(item['codes'])} proposed ({item['reading']}) → coder queue", item["proposed_by"], item["status"])
                   for item in proposals]
        if events:
            render_html_table(["When", "What", "By", "Kind"],
                              [[escape(at[:16].replace("T", " ")), escape(what), escape(by), badge(kind, "blue")] for at, what, by, kind in sorted(events)[-8:]])
        else:
            st.caption("Nothing captured yet for this person.")
        open_proposals = [item for item in proposals if item["status"] == "Proposed"]
        if open_proposals:
            st.markdown('<div class="nc-label" style="margin-top:.6rem">Coder queue · CPT II confirmation</div>', unsafe_allow_html=True)
            for item in open_proposals:
                text_col, ok_col, no_col = st.columns([3, 1, 1], vertical_alignment="center")
                text_col.markdown(f"CPT II **{' + '.join(item['codes'])}** for BP {item['reading']} on {item['on']}")
                confirm = ok_col.button("Confirm", key=f"s04_cpt2_ok_{item['id']}", type="primary", disabled=not can("Codes", "Approve"))
                reject = no_col.button("Reject", key=f"s04_cpt2_no_{item['id']}", type="secondary", disabled=not can("Codes", "Approve"))
                if confirm or reject:
                    _decide_cpt2(item, confirm)
                    st.rerun()


def _decide_cpt2(item, confirm: bool):
    def change(state):
        for proposal in state["cpt2_proposals"]:
            if proposal["id"] == item["id"]:
                proposal["status"] = "Confirmed" if confirm else "Rejected"
                proposal["decided_by"], proposal["decided_at"] = actor(), _now_iso()
        if confirm:
            for code in item["codes"]:
                post_fact(state, Fact(f"CPT2-{item['id']}-{code}", item["person_id"], "procedure", "CPT", code, f"CPT II {code} (coder confirmed)",
                                      date.fromisoformat(item["on"]), datetime.now(), "cpt2", f"Coder confirmation {item['id']}",
                                      payer_visible=True, provider=actor()))

    mutate("WRK", f"CPT II {'confirmed' if confirm else 'rejected'} by coder", f"{item['person_id']} · {' + '.join(item['codes'])}", change,
           toast=f"CPT II {'confirmed' if confirm else 'rejected'}")


# ---------------------------------------------------------------------------
# S05 Person profile
# ---------------------------------------------------------------------------

TILE_TONES = {"GAP_OPEN": "red", "GAP_UNKNOWN": "amber", "PENDING_EVIDENCE": "blue", "GAP_CLOSED": "green", "EXCLUDED": "gray",
              "NOT_IN_POPULATION": "gray", "PREFERENCE_RECORDED": "violet"}


def show_person():
    render_page("vbc_person")
    snapshot = get_snapshot()
    header("S05", snapshot)
    default_person(snapshot)
    pick_col, _ = st.columns([2, 3])
    with pick_col:
        person = pick_person(snapshot, "s05_person")
    if person is None:
        st.info("No persons available.")
        return
    person_banner(person)
    if person.identity != "confident":
        callout("guardrail", "Aggregation stopped — identity review", f"{person.identity_note} No gaps are worked until the MPI steward resolves it.")
    if person.coverage != "continuous":
        callout("guardrail", "Plan-level status unknown", f"{person.coverage_note} The provider view still shows the clinical picture.")
    results = {result.measure: result for result in snapshot.evaluation.for_person(person.person_id)}
    order = [code for code in PERSON_MEASURES + ("TRC", "FMC") if code in results]
    for start in range(0, len(order), 2):
        cols = st.columns(2, gap="medium")
        for col, code in zip(cols, order[start:start + 2], strict=False):
            result = results[code]
            with col:
                st.markdown(
                    f'<div class="vbc-tile tone-{TILE_TONES.get(result.status, "gray")}"><div class="top"><b>{code}</b>{status_badge(result.status)}</div>'
                    f"<p>{escape(result.reason)}</p></div>",
                    unsafe_allow_html=True,
                )
                if result.verdicts:
                    st.button("Open proof →", key=f"s05_proof_{code}", type="tertiary", on_click=open_evidence, args=(person.person_id, code),
                              disabled=not can_vbc("S06", "View"))

    with st.container(border=True):
        card_title(f"History this measurement period · {MP.code}", "Evidence and actions in one line — ready for a care conversation or an auditor")
        events = []
        for result in results.values():
            for verdict in result.verdicts:
                if verdict.fact.on and verdict.fact.on >= MP.start:
                    tone = "green" if verdict.accepted else "peach" if verdict.verdict == "ON_HOLD" else "red"
                    events.append((verdict.fact.on, f"{verdict.fact.description.split(' (')[0][:22]}", tone))
        for action in snapshot.state.get("actions", []):
            if action["person_id"] == person.person_id:
                events.append((parse_date(action["at"]), f"{action['detail'][:22]}", "violet"))
        events = sorted({(day, text, tone) for day, text, tone in events if day}, key=lambda item: item[0])[-7:]
        if events:
            ticks = "".join(f'<div class="vbc-tick"><i class="tone-{tone}"></i><b>{day:%d %b}</b>{escape(text)}</div>' for day, text, tone in events)
            st.markdown(f'<div class="vbc-timeline">{ticks}</div>', unsafe_allow_html=True)
        else:
            st.caption("No dated evidence this period.")

    req_col, pref_col = st.columns(2, gap="medium")
    with req_col:
        with st.container(border=True):
            card_title("Request outside records", "HIE query, portal or fax — the gap moves to pending evidence")
            open_codes = [code for code, result in results.items() if result.status == GAP_OPEN and MEASURES[code].kind == "person"]
            measure = st.selectbox("Measure", open_codes or ["—"], key=f"s05_req_measure_{person.person_id}")
            channel = st.radio("Channel", ("HIE", "Portal", "Fax"), horizontal=True, key=f"s05_req_channel_{person.person_id}")
            if st.button("Send record request", key=f"s05_req_{person.person_id}", type="primary", disabled=not open_codes or not can_vbc("S05", "Edit")):
                if guard("S05", "Edit", "request outside records"):
                    mutate("WRK", "Outside records requested", f"{person.person_id} · {measure} via {channel}",
                           lambda state: vbc_store.add_action(state, actor(), person.person_id, measure, "record_request",
                                                              "Outside report requested", channel=channel, status="open"),
                           toast=f"{measure} moved to PENDING_EVIDENCE")
                    st.rerun()
    with pref_col:
        with st.container(border=True):
            card_title("Preferences and barriers", "Stored for outreach — these are never exclusions")
            channel_pref = st.radio("Preferred channel", ("SMS", "Phone", "Portal"), horizontal=True,
                                    index=("SMS", "Phone", "Portal").index(person.preferences.get("channel", "Phone")), key=f"s05_pref_ch_{person.person_id}")
            time_pref = st.radio("Preferred time", ("AM", "PM"), horizontal=True, index=("AM", "PM").index(person.preferences.get("time", "AM")),
                                 key=f"s05_pref_time_{person.person_id}")
            barriers = st.multiselect("Barriers", ("transport", "cost", "language", "caregiver", "work hours"), default=person.barriers,
                                      key=f"s05_barriers_{person.person_id}")
            if st.button("Save preferences", key=f"s05_pref_save_{person.person_id}", type="secondary", disabled=not can_vbc("S05", "Edit")):
                if guard("S05", "Edit", "update preferences"):
                    mutate("WRK", "Preferences and barriers updated", f"{person.person_id}: {channel_pref}/{time_pref}; barriers {', '.join(barriers) or 'none'}",
                           lambda state: state["person_prefs"].update({person.person_id: {"channel": channel_pref, "time": time_pref, "barriers": barriers}}),
                           toast="Preferences saved")
                    st.rerun()
    how_it_works("S05")


# ---------------------------------------------------------------------------
# S11 Outreach console
# ---------------------------------------------------------------------------

CAMPAIGNS = {
    "COL-E": {"title": "COL-E · FIT kit offer", "clinical": True},
    "BCS-E": {"title": "BCS-E · mammogram booking", "clinical": False},
    "EED": {"title": "EED · eye exam booking", "clinical": False},
    "AIS-E": {"title": "AIS-E · vaccine reminder", "clinical": False, "draft": True},
}
TEMPLATES = {
    ("COL-E", "English"): "Hi {first}, this is the team at Dr. {pcp}'s office at {site}. You're due for colorectal cancer screening. We can mail you a simple home test kit, or help you schedule a colonoscopy. Reply 1 for the kit, 2 to schedule.",
    ("COL-E", "Spanish"): "Hola {first}, le escribe el equipo del Dr. {pcp} en {site}. Le corresponde una prueba de detección de cáncer de colon. Podemos enviarle una prueba sencilla para hacer en casa o ayudarle a programar una colonoscopía. Responda 1 para el kit, 2 para programar.",
    ("BCS-E", "English"): "Hi {first}, it's time for your screening mammogram. Reply 1 and we'll call you to book a time that works, or 2 if you've had one elsewhere this year.",
    ("BCS-E", "Spanish"): "Hola {first}, es momento de su mamografía de detección. Responda 1 y le llamaremos para programar una cita, o 2 si ya se la hizo en otro lugar este año.",
    ("EED", "English"): "Hi {first}, you're due for a yearly eye exam. Reply 1 and we'll book one with our eye care partner near {site}.",
    ("EED", "Spanish"): "Hola {first}, le corresponde su examen anual de la vista. Responda 1 y le programamos una cita con nuestro especialista cerca de {site}.",
    ("AIS-E", "English"): "Hi {first}, flu vaccines are available at {site} with no appointment needed. Reply 1 for opening hours.",
    ("AIS-E", "Spanish"): "Hola {first}, la vacuna contra la gripe está disponible en {site} sin cita previa. Responda 1 para ver el horario.",
}
TEMPLATES.update({
    ("COL-E", "French"): "Bonjour {first}, ici l'équipe du cabinet du Dr {pcp} à {site}. Il est temps de faire votre dépistage du cancer colorectal. Nous pouvons vous envoyer un test simple à faire à domicile, ou vous aider à planifier une coloscopie. Répondez 1 pour le kit, 2 pour prendre rendez-vous.",
    ("COL-E", "Mandarin"): "{first}您好，这里是{site} {pcp}医生诊所的团队。您该做结直肠癌筛查了。我们可以为您寄送简便的居家检测套件，或帮您预约结肠镜检查。回复1索取套件，回复2预约。",
    ("COL-E", "Vietnamese"): "Chào {first}, đây là nhóm chăm sóc tại phòng khám của bác sĩ {pcp} ở {site}. Đã đến lúc quý vị tầm soát ung thư đại trực tràng. Chúng tôi có thể gửi bộ xét nghiệm đơn giản tại nhà, hoặc giúp quý vị đặt lịch nội soi đại tràng. Trả lời 1 để nhận bộ xét nghiệm, 2 để đặt lịch.",
    ("COL-E", "Arabic"): "مرحبًا {first}، معك فريق عيادة الدكتور {pcp} في {site}. حان موعد فحص الكشف عن سرطان القولون والمستقيم. يمكننا إرسال مجموعة فحص منزلية بسيطة إليك، أو مساعدتك في حجز موعد لتنظير القولون. أرسل 1 للحصول على المجموعة، أو 2 لحجز موعد.",
    ("BCS-E", "French"): "Bonjour {first}, il est temps de faire votre mammographie de dépistage. Répondez 1 et nous vous appellerons pour fixer un rendez-vous, ou 2 si vous l'avez déjà faite ailleurs cette année.",
    ("BCS-E", "Mandarin"): "{first}您好，您该做乳腺X光筛查了。回复1，我们会致电为您预约合适的时间；如果您今年已在其他地方做过，请回复2。",
    ("BCS-E", "Vietnamese"): "Chào {first}, đã đến lúc quý vị chụp nhũ ảnh tầm soát. Trả lời 1 và chúng tôi sẽ gọi để đặt lịch phù hợp, hoặc 2 nếu quý vị đã chụp ở nơi khác trong năm nay.",
    ("BCS-E", "Arabic"): "مرحبًا {first}، حان موعد تصوير الثدي الشعاعي للكشف المبكر. أرسلي 1 وسنتصل بكِ لحجز موعد مناسب، أو 2 إذا كنتِ قد أجريتِه في مكان آخر هذا العام.",
    ("EED", "French"): "Bonjour {first}, c'est le moment de votre examen annuel des yeux. Répondez 1 et nous prendrons rendez-vous avec notre partenaire en soins oculaires près de {site}.",
    ("EED", "Mandarin"): "{first}您好，您该做每年一次的眼科检查了。回复1，我们会为您在{site}附近的合作眼科机构预约。",
    ("EED", "Vietnamese"): "Chào {first}, đã đến lúc quý vị khám mắt định kỳ hằng năm. Trả lời 1 và chúng tôi sẽ đặt lịch với đối tác chăm sóc mắt gần {site}.",
    ("EED", "Arabic"): "مرحبًا {first}، حان موعد فحص العين السنوي. أرسل 1 وسنحجز لك موعدًا لدى شريكنا لرعاية العيون بالقرب من {site}.",
    ("AIS-E", "French"): "Bonjour {first}, le vaccin contre la grippe est disponible à {site}, sans rendez-vous. Répondez 1 pour connaître les horaires.",
    ("AIS-E", "Mandarin"): "{first}您好，{site}现可接种流感疫苗，无需预约。回复1查询开放时间。",
    ("AIS-E", "Vietnamese"): "Chào {first}, vắc-xin cúm hiện có tại {site}, không cần hẹn trước. Trả lời 1 để biết giờ mở cửa.",
    ("AIS-E", "Arabic"): "مرحبًا {first}، لقاح الإنفلونزا متوفر في {site} دون الحاجة إلى موعد. أرسل 1 لمعرفة ساعات العمل.",
})
SURVEY_TERMS = ("survey", "cahps", " hos ", "rate us", "star rating", "questionnaire", "how would you rate")
REPLIES = (("1", "Replied 1: yes", "Fulfil (kit / booking)"), ("STOP", "Replied STOP", "Consent withdrawn on SMS"),
           ("No answer", "No answer", "Retry later"))
PHI_TERMS = ("diabet", "hypertens", "hiv", "depress", "a1c", "kidney", "dialysis", "pregnan", "mastectomy", "cancer diagnosis")


def _attempts(snapshot, person_id: str, measure: str) -> list:
    return [item for item in snapshot.state.get("outreach", []) if item["person_id"] == person_id and item["measure"] == measure]


def _eligible(snapshot, measure: str) -> list:
    suppressed = _suppressed(snapshot)
    out = []
    for result in snapshot.evaluation.for_measure(measure):
        if result.status != GAP_OPEN or (result.person_id, measure) in suppressed:
            continue
        attempts = _attempts(snapshot, result.person_id, measure)
        if len(attempts) >= OUTREACH_ATTEMPT_LIMIT or any("Replied" in item["outcome"] for item in attempts):
            continue
        last = max((parse_date(item["at"]) for item in attempts), default=None)
        if last and (snapshot.as_of - last).days < OUTREACH_RECONTACT_DAYS:
            continue
        out.append(result)
    return out


def _checks(person, text: str, channel: str, now: datetime, has_template: bool) -> list[tuple[str, bool, str]]:
    quiet_start, quiet_end = QUIET_HOURS
    in_quiet = now.hour >= quiet_start or now.hour < quiet_end
    lowered = f" {text.lower()} "
    interpreter = not has_template and channel == "Phone"
    if channel == "Phone":
        consent_note = "Phone consent active" if person.consent_phone else "Phone consent withdrawn"
    else:
        consent_note = "SMS / portal consent active" if person.consent_sms else "No SMS consent"
    language_note = (f"{person.language} · approved" if has_template
                     else f"{person.language} · interpreter call script" if interpreter else f"No {person.language} SMS template")
    return [
        ("Consent on file", person.consent_phone if channel == "Phone" else person.consent_sms, consent_note),
        ("Channel allowed", channel == "Phone" or person.consent_sms, f"{channel}"),
        ("Language template approved", has_template or interpreter, language_note),
        ("Quiet hours respected", not in_quiet, f"Now {now:%H:%M}; quiet {quiet_start}:00–0{quiet_end}:00"),
        ("No survey reference", not any(term in lowered for term in SURVEY_TERMS), "Survey-integrity scan"),
        ("Minimum necessary PHI", not any(term in lowered for term in PHI_TERMS), "No diagnosis in message"),
    ]


def show_outreach():
    render_page("vbc_outreach")
    snapshot = get_snapshot()
    header("S11", snapshot)
    campaign_state = snapshot.state.get("campaigns", {})
    queue = snapshot.state.get("outreach_queue", [])
    camp_col, preview_col, check_col = st.columns([1.15, 1.2, 1.2], gap="medium")
    with camp_col:
        with st.container(border=True):
            card_title("Campaigns", "Each maps to one HEDIS measure and reaches only genuine open gaps")
            options = list(CAMPAIGNS)
            measure = st.radio("Campaign", options, format_func=lambda code: CAMPAIGNS[code]["title"], key="s11_campaign", label_visibility="collapsed")
            for code in options:
                sent = len([item for item in snapshot.state.get("outreach", []) if item["measure"] == code])
                replied = len([item for item in snapshot.state.get("outreach", []) if item["measure"] == code and "Replied" in item["outcome"]])
                draft = CAMPAIGNS[code].get("draft") and not campaign_state.get(code, {}).get("approved")
                status = "Draft · approval pending" if draft else f"{sent} sent · {replied / sent:.0%} replied" if sent else "No sends yet"
                st.markdown(f'<div class="nc-meta">{escape(CAMPAIGNS[code]["title"])}: {escape(status)}'
                            f'{" · clinical — no bulk" if CAMPAIGNS[code]["clinical"] else ""}</div>', unsafe_allow_html=True)
            if CAMPAIGNS[measure].get("draft") and not campaign_state.get(measure, {}).get("approved"):
                if st.button("Approve campaign", key="s11_approve", type="secondary", disabled=not can_vbc("S11", "Approve")):
                    if guard("S11", "Approve", "approve an outreach campaign"):
                        mutate("OUT", "Campaign approved", CAMPAIGNS[measure]["title"],
                               lambda state: state["campaigns"].update({measure: {"approved": True, "by": actor(), "at": _now_iso()}}),
                               toast="Campaign approved")
                        st.rerun()
            if queue:
                st.caption(f"{len(queue)} gap(s) queued from the workbench.")

    eligible = _eligible(snapshot, measure)
    queued_ids = {entry["person_id"] for entry in queue if entry["measure"] == measure}
    eligible.sort(key=lambda result: (result.person_id not in queued_ids, snapshot.person(result.person_id).name))
    draft_blocked = CAMPAIGNS[measure].get("draft") and not campaign_state.get(measure, {}).get("approved")
    with preview_col:
        with st.container(border=True):
            card_title("What the person will see", "Plain language, their language, approved template — previewed before sending")
            if not eligible:
                st.info("No eligible people: only open gaps that are not suppressed, recently contacted or at the attempt limit.")
                person, text, channel, has_template = None, "", "SMS", False
            else:
                ids = [result.person_id for result in eligible]
                pid = st.selectbox("Person", ids, key=f"s11_person_{measure}",
                                   format_func=lambda value: f"{snapshot.person(value).name} · {snapshot.person(value).language}"
                                   + (" · queued" if value in queued_ids else ""))
                person = snapshot.person(pid)
                channel = st.radio("Channel", ("SMS", "Phone"), horizontal=True, index=0 if person.consent_sms else 1, key=f"s11_channel_{pid}")
                template = TEMPLATES.get((measure, person.language))
                has_template = template is not None
                values = {"first": person.name.split()[0], "pcp": person.pcp.split(",")[0].split()[-1], "site": person.site}
                if channel == "Phone":
                    interpreter = f" (with a {person.language} interpreter)" if person.language != "English" else ""
                    base = f"Call script{interpreter}: " + TEMPLATES[(measure, "English")].format(**values)
                else:
                    base = (template or f"[No approved {person.language} SMS template — use a phone call with an interpreter]").format(**values)
                editable = has_template or channel == "Phone"
                if editable:
                    text = st.text_area("Message (edits are re-checked)", value=base, key=f"s11_text_{pid}_{measure}_{channel}", height=120)
                else:
                    text = base
                footer = f"Approved template T-{measure}-01 v3" if editable else "No approved template"
                st.markdown(
                    f'<div class="vbc-phone"><div class="screen"><div class="meta">Preview · {escape(person.language)} · {escape(channel)}</div>'
                    f'<div class="bubble" dir="auto">{escape(text)}</div></div><div class="foot">{footer}</div></div>',
                    unsafe_allow_html=True,
                )
    with check_col:
        with st.container(border=True):
            card_title("Checked at send", "Consent, channel, language, quiet hours, survey integrity and minimum-necessary PHI")
            now = datetime.now()
            checks = _checks(person, text, channel, now, has_template) if person else []
            st.markdown("".join(
                f'<div class="vbc-check"><span class="{"ok" if ok else "no"}">{"✓" if ok else "✗"}</span><span>{escape(label)}</span><small>{escape(detail)}</small></div>'
                for label, ok, detail in checks
            ) or '<div class="nc-meta">Select a person to run the checks.</div>', unsafe_allow_html=True)
            week_ago = now - timedelta(days=7)
            blocked = [event for event in snapshot.state.get("audit", []) if event["family"] in ("OUT", "SRV") and event["outcome"] == "Blocked"
                       and datetime.fromisoformat(event["at"]) >= week_ago]
            reasons = Counter(event["detail"].split(":")[0] for event in blocked)
            st.markdown(f'<div class="nc-banner {"danger" if blocked else "ok"}" style="margin-top:.6rem"><span>&#9888;</span><div class="nc-grow">'
                        f'<b>Blocked this week: {len(blocked)}</b><small>{escape(" · ".join(f"{count} {reason}" for reason, count in reasons.items()) or "No policy blocks")}</small></div></div>',
                        unsafe_allow_html=True)
            send = st.button("Send", key="s11_send", type="primary", disabled=not person or draft_blocked or not can_vbc("S11", "Edit"))
            if draft_blocked:
                st.caption("Campaign is a draft — it needs approval before anything is sent.")
            if send and guard("S11", "Edit", "send outreach"):
                failed = [label for label, ok, _ in checks if not ok]
                if failed:
                    family = "SRV" if "No survey reference" in failed else "OUT"
                    mutate(family, f"{failed[0]}: send blocked", f"{person.person_id} · {measure} · {channel}", outcome="Blocked",
                           toast="Blocked by policy check — logged")
                    if "Quiet hours respected" in failed:
                        st.warning(f"Blocked: inside quiet hours. Next permitted window opens at 0{QUIET_HOURS[1]}:00.")
                    else:
                        st.error(f"Blocked: {', '.join(failed)}. Policy event logged.")
                else:
                    _send(person.person_id, measure, channel, len(_attempts(snapshot, person.person_id, measure)) + 1)
                    st.rerun()

    attempts = [item for item in snapshot.state.get("outreach", []) if item["measure"] == measure]
    with st.container(border=True):
        card_title("Every attempt recorded", f"Outcome and next step per attempt; limit {OUTREACH_ATTEMPT_LIMIT} attempts, channel changes only with consent")
        if not attempts:
            st.caption("No attempts yet for this campaign.")
        widths = [2, 0.8, 0.9, 2.2, 1.8, 2.6]
        for col, label in zip(st.columns(widths), ("Person", "Attempt", "Channel", "Outcome", "Next", "Record reply"), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        for item in attempts[-10:][::-1]:
            person = snapshot.person(item["person_id"])
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].markdown(f'<div class="nc-cell"><b>{escape(person.name if person else item["person_id"])}</b></div>', unsafe_allow_html=True)
            cols[1].markdown(f'<div class="nc-cell">{item["attempt"]} · {item["at"][5:10]}</div>', unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell">{escape(item["channel"])}</div>', unsafe_allow_html=True)
            tone = "green" if "Replied" in item["outcome"] else "red" if "limit" in item["outcome"] or "STOP" in item["outcome"] else "gray"
            cols[3].markdown(badge(item["outcome"], tone), unsafe_allow_html=True)
            cols[4].markdown(f'<div class="nc-cell muted">{escape(item["next"])}</div>', unsafe_allow_html=True)
            if item["outcome"].endswith("awaiting reply"):
                with cols[5], st.container(horizontal=True, gap="small"):
                    for label, outcome, next_step in REPLIES:
                        if st.button(label, key=f"s11_reply_{item['id']}_{label}", type="tertiary", disabled=not can_vbc("S11", "Edit")):
                            _record_reply(item, outcome, next_step)
                            st.rerun()
    how_it_works("S11")


def _send(person_id: str, measure: str, channel: str, attempt: int):
    def change(state):
        state["outreach"].append({
            "id": vbc_store.next_id(state, "OUT"), "at": _now_iso(), "by": actor(), "person_id": person_id, "measure": measure,
            "campaign": measure, "channel": channel, "attempt": attempt, "template": f"T-{measure}-01 v3",
            "outcome": ("Delivered" if channel == "SMS" else "Call placed") + " — awaiting reply", "next": "Wait for reply",
        })
        state["outreach_queue"] = [entry for entry in state["outreach_queue"] if (entry["person_id"], entry["measure"]) != (person_id, measure)]

    mutate("OUT", f"Outreach sent ({channel})", f"{person_id} · {measure} · attempt {attempt}", change,
           toast="Message sent" if channel == "SMS" else "Call logged")


def _record_reply(item, outcome: str, next_step: str):
    def change(state):
        for attempt in state["outreach"]:
            if attempt["id"] == item["id"]:
                attempt["outcome"], attempt["next"] = outcome, next_step
                if outcome == "No answer" and attempt["attempt"] >= OUTREACH_ATTEMPT_LIMIT:
                    attempt["outcome"], attempt["next"] = "No answer · limit reached", "Closed no-contact; stays in denominator"
        if outcome == "Replied STOP":
            state["consent_withdrawn"].setdefault(item["person_id"], {})["SMS"] = True

    mutate("OUT", f"Reply recorded: {outcome}", f"{item['person_id']} · {item['measure']}", change, toast=outcome)
    go_to("vbc_outreach")
