"""Care transitions: S10 Transitions board (TRC components, PCR risk, FMC follow-up)."""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

import streamlit as st

from frontend.ui.components import (
    badge,
    card_title,
    render_banner,
    render_html_table,
    render_kpi_card,
)
from frontend.ui.shell import render_page
from frontend.vbc import store as vbc_store
from frontend.vbc.engine import TRC_COMPONENTS
from frontend.views.vbc.common import (
    actor,
    bars,
    can_vbc,
    fmt,
    get_snapshot,
    guard,
    header,
    how_it_works,
    mutate,
)

COMPONENT_TONES = {"Done": "green", "Due": "amber", "Requested": "blue", "Not started": "gray", "Missed": "red", "—": "gray"}
COMPONENT_OWNERS = {"NIA": "Transitions agent", "RDI": "Transitions RN", "PED": "Transitions RN", "MRP": "Pharmacist"}
FMC_TONES = {"Done": "green", "Booked": "blue", "Due": "amber", "Missed": "red"}
HIGH_RISK = 0.25


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def show_transitions():
    render_page("vbc_transitions")
    snapshot = get_snapshot()
    header("S10", snapshot)
    for feed in snapshot.stale_feeds():
        if feed["kind"] == "ADT":
            hours = int((snapshot.now - feed["last"]).total_seconds() // 3600)
            render_banner(f"{feed['feed']} feed silent for {hours} hours",
                          "Transitions from this hospital cannot start automatically. Use the manual notification below; integration ticket INT-2291 is open.",
                          tone="danger", icon="&#9888;")
    cases = sorted(snapshot.evaluation.trc_cases, key=lambda case: (case.met, -case.stay.expected_readmit))
    open_cases = [case for case in cases if case.open_components]
    fmc = snapshot.evaluation.fmc_cases
    pcr = snapshot.rate("PCR")
    due_soon = sum(1 for case in open_cases for name in case.open_components
                   if case.components[name]["deadline"] and (case.components[name]["deadline"] - snapshot.as_of).days <= 5)
    for col, (label, value, tone, context) in zip(st.columns(4), (
        ("Open transitions", str(len(open_cases)), "lav", f"{len(cases)} admissions and discharges this period"),
        ("Components due ≤ 5 days", str(due_soon), "butter", "Discharge info, engagement and medication reconciliation"),
        ("PCR observed / expected", f"{pcr.value:.2f}" if pcr.value is not None else "—", "rose",
         f"{pcr.observed} readmission(s) vs {pcr.expected:.2f} expected · lower is better"),
        ("ED follow-ups due", str(sum(case.status in ("Due", "Booked") for case in fmc)), "sky", "FMC: seen within 7 days of the ED visit"),
    ), strict=True):
        with col:
            render_kpi_card(label, value, tone, context)

    with st.container(border=True):
        card_title("Transitions board", "Four TRC components per stay, with readmission risk and owner — high-risk discharges first")
        if not cases:
            st.caption("No inpatient stays this period.")
        rows = []
        for case in cases:
            person = snapshot.person(case.person_id)
            stay = case.stay
            risk = stay.expected_readmit
            rows.append([
                f"<b>{escape(person.name if person else case.person_id)}</b><div class='nc-meta'>{escape(stay.facility)} · {escape(stay.adt_ref)}</div>",
                f"{fmt(stay.admit)}<div class='nc-meta'>{'Discharged ' + fmt(stay.discharge) if stay.discharge else 'Still inpatient'}</div>",
                *[badge(case.components[name]["label"], COMPONENT_TONES.get(case.components[name]["label"], "gray")) for name in TRC_COMPONENTS],
                badge(f"{risk:.0%}" + (" · high" if risk >= HIGH_RISK else ""), "red" if risk >= HIGH_RISK else "gray"),
                badge("Readmitted ≤ 30 days", "red") if case.readmitted else badge("Readmission stay", "amber") if stay.readmission_of else "",
            ])
        if rows:
            render_html_table(["Person", "Admitted", "NIA", "RDI", "PED", "MRP", "PCR risk", ""], rows)

    detail_col, side_col = st.columns([1.5, 1], gap="medium")
    with detail_col, st.container(border=True):
        card_title("Work a transition", "Components close as evidence lands; every update is logged")
        if cases:
            def actionable(case) -> int:
                return sum(item["label"] in ("Due", "Not started", "Requested") for item in case.components.values())

            workable = sorted(cases, key=lambda case: (not actionable(case), -case.stay.expected_readmit))
            labels = {case.stay.stay_id: f"{snapshot.person(case.person_id).name} · {case.stay.facility} · {fmt(case.stay.admit)}"
                      + (f" · {actionable(case)} to do" if actionable(case) else "") for case in workable}
            stay_id = st.selectbox("Stay", list(labels), format_func=labels.get, key="s10_stay")
            case = next(item for item in cases if item.stay.stay_id == stay_id)
            _case_detail(snapshot, case)
    with side_col:
        with st.container(border=True):
            card_title("TRC component rates", "Share of discharges with each component done in its window")
            rate = snapshot.rate("TRC")
            bars([(label.split(" (")[0], code, met, total, f"{met}/{total}", "green") for code, (met, total, label) in rate.components.items()])
        with st.container(border=True):
            card_title("Readmissions (PCR)", "Observed against expected; each readmission gets a root-cause review")
            readmits = [stay for stay in snapshot.evaluation.pcr_stays if stay.readmission_of]
            if readmits:
                for stay in readmits:
                    person = snapshot.person(stay.person_id)
                    st.markdown(f'<div class="nc-meta"><b>{escape(person.name if person else stay.person_id)}</b> readmitted {fmt(stay.admit)} '
                                f'to {escape(stay.facility)} — counted as observed</div>', unsafe_allow_html=True)
                    reviewed = any(event["action"] == "Readmission root-cause review opened" and stay.stay_id in event["detail"]
                                   for event in snapshot.state.get("audit", []))
                    if not reviewed and st.button("Open root-cause review", key=f"s10_rca_{stay.stay_id}", type="tertiary",
                                                  disabled=not can_vbc("S10", "Edit")) and guard("S10", "Edit", "open a root-cause review"):
                        mutate("TRC", "Readmission root-cause review opened", f"{stay.stay_id} (index {stay.readmission_of})", toast="Root-cause review opened")
                        st.rerun()
            else:
                st.caption("No 30-day readmissions this period.")
    _fmc(snapshot, fmc)
    _manual_adt(snapshot)
    how_it_works("S10")


def _case_detail(snapshot, case):
    stay = case.stay
    today = snapshot.as_of
    for name, label in TRC_COMPONENTS.items():
        item = case.components[name]
        deadline = item["deadline"]
        cols = st.columns([2.4, 1, 1.4, 2], vertical_alignment="center")
        cols[0].markdown(f'<div class="nc-cell"><b>{name}</b> · {escape(label)}<div class="nc-meta">Owner: {COMPONENT_OWNERS[name]}'
                         + (f" · due {fmt(deadline)}" if deadline else "") + "</div></div>", unsafe_allow_html=True)
        cols[1].markdown(badge(item["label"], COMPONENT_TONES.get(item["label"], "gray")), unsafe_allow_html=True)
        cols[2].markdown(f'<div class="nc-meta">{escape(item.get("by") or "")}</div>', unsafe_allow_html=True)
        if item["met"] or item["label"] in ("Missed", "—"):
            if item["label"] == "Missed" and name == "MRP":
                cols[3].caption("Escalated to the pharmacy lead")
            continue
        with cols[3], st.container(horizontal=True, gap="small"):
            blocked = name == "PED" and stay.discharge == today
            if st.button("Done today", key=f"s10_done_{stay.stay_id}_{name}", type="primary", disabled=blocked or not can_vbc("S10", "Edit")):
                if guard("S10", "Edit", "update a transition"):
                    _set_component(stay.stay_id, name, {"status": "done", "on": today.isoformat(), "by": actor()}, f"{name} done")
                    st.rerun()
            if name == "RDI" and item["status"] != "requested" and st.button("Request from hospital", key=f"s10_req_{stay.stay_id}", type="secondary",
                                                                           disabled=not can_vbc("S10", "Edit")):
                if guard("S10", "Edit", "request discharge information"):
                    _set_component(stay.stay_id, name, {"status": "requested", "on": None, "by": actor()}, "Discharge information requested")
                    st.rerun()
            if name == "MRP" and deadline and (deadline - today).days <= 5 and st.button("Escalate to pharmacy lead", key=f"s10_esc_{stay.stay_id}",
                                                                                       type="secondary", disabled=not can_vbc("S10", "Edit")):
                if guard("S10", "Edit", "escalate medication reconciliation"):
                    mutate("TRC", "Medication reconciliation escalated", f"{stay.stay_id} · due {deadline:%d %b}", toast="Escalated to the pharmacy lead")
                    st.rerun()
            if blocked:
                st.caption("Engagement must happen after the discharge day.")
    if case.readmitted:
        st.warning("This stay was followed by a readmission within 30 days — counted in PCR as observed and flagged for review.")


def _set_component(stay_id: str, name: str, entry: dict, action: str):
    mutate("TRC", action, f"{stay_id} · {name} · {TRC_COMPONENTS[name]}",
           lambda state: state["transitions"].setdefault(stay_id, {}).update({name: entry}), refs={"stay": stay_id}, toast=action)


def _fmc(snapshot, cases):
    with st.container(border=True):
        card_title("ED visits needing follow-up (FMC)", "People with two or more chronic conditions must be seen within 7 days of the ED visit")
        if not cases:
            st.caption("No eligible ED visits this period.")
            return
        widths = [2, 1, 2.2, 1.1, 1, 2.6]
        for col, label in zip(st.columns(widths), ("Person", "ED visit", "Conditions", "Due", "Status", "Action"), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        for case in sorted(cases, key=lambda item: ({"Due": 0, "Booked": 1, "Missed": 2, "Done": 3}[item.status], item.due)):
            person = snapshot.person(case.person_id)
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].markdown(f'<div class="nc-cell"><b>{escape(person.name if person else case.person_id)}</b>'
                             f'<div class="nc-meta">{escape(case.visit.diagnosis)}</div></div>', unsafe_allow_html=True)
            cols[1].markdown(f'<div class="nc-cell">{fmt(case.visit.on)}</div>', unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell muted">{escape(", ".join(case.visit.conditions))}</div>', unsafe_allow_html=True)
            cols[3].markdown(f'<div class="nc-cell">{fmt(case.due)}</div>', unsafe_allow_html=True)
            cols[4].markdown(badge(case.status, FMC_TONES[case.status]), unsafe_allow_html=True)
            visit_id = case.visit.visit_id
            with cols[5], st.container(horizontal=True, gap="small"):
                if case.status == "Due" and st.button("Book follow-up", key=f"s10_book_{visit_id}", type="primary", disabled=not can_vbc("S10", "Edit")):
                    if guard("S10", "Edit", "book a follow-up"):
                        slot = min(case.due, snapshot.as_of + timedelta(days=2))
                        _fmc_task(visit_id, {"status": "booked", "note": f"Follow-up booked {slot:%d %b}", "by": actor()}, "FMC follow-up booked")
                        st.rerun()
                if case.status == "Booked" and st.button("Mark seen", key=f"s10_seen_{visit_id}", type="primary", disabled=not can_vbc("S10", "Edit")):
                    if guard("S10", "Edit", "record a follow-up visit"):
                        _fmc_task(visit_id, {"status": "done", "on": f"{snapshot.as_of:%d %b}", "by": actor()}, "FMC follow-up completed")
                        st.rerun()
                if case.status in ("Due", "Booked") and st.button("Unreachable", key=f"s10_unreach_{visit_id}", type="tertiary",
                                                                 disabled=not can_vbc("S10", "Edit")):
                    if guard("S10", "Edit", "log an outreach attempt"):
                        mutate("OUT", "FMC attempt — unreachable", f"{case.person_id} · ED {case.visit.on:%d %b}; channel changes only with consent",
                               toast="Attempt logged")
                        st.rerun()
                if case.status in ("Missed", "Done"):
                    st.caption(case.note)


def _fmc_task(visit_id: str, entry: dict, action: str):
    mutate("TRC", action, visit_id, lambda state: state["fmc_tasks"].update({visit_id: entry}), refs={"visit": visit_id}, toast=action)


def _manual_adt(snapshot):
    with st.expander("Manual admission or discharge notification (feed down)"):
        st.caption("Use when a hospital ADT feed is silent. The notification starts a TRC case exactly as an A01/A03 message would.")
        adults = [person for person in snapshot.people if (person.age or 0) >= 18]
        with st.form("s10_manual_adt", clear_on_submit=True):
            c1, c2 = st.columns(2)
            person_id = c1.selectbox("Person", [person.person_id for person in adults],
                                     format_func=lambda pid: f"{snapshot.person(pid).name} · {snapshot.person(pid).age}")
            facility = c2.text_input("Hospital", value="Riverside Hospital")
            c3, c4, c5 = st.columns(3)
            admit = c3.date_input("Admitted", value=snapshot.as_of - timedelta(days=3), max_value=snapshot.as_of)
            discharged = c4.checkbox("Discharged", value=True)
            discharge = c5.date_input("Discharge date", value=snapshot.as_of - timedelta(days=1), max_value=snapshot.as_of)
            source = st.text_input("Notified by", placeholder="e.g. Riverside case manager by phone, 14:20")
            submit = st.form_submit_button("Record notification", type="primary", disabled=not can_vbc("S10", "Edit"))
        if submit and guard("S10", "Edit", "record a manual ADT notification"):
            if discharged and discharge < admit:
                st.error("Discharge cannot be before admission.")
                return

            def change(state):
                stay_id = vbc_store.next_id(state, "MADT")
                state["manual_adt"].append({"stay_id": stay_id, "person_id": person_id, "facility": facility.strip() or "Manual notification",
                                            "admit": admit.isoformat(), "discharge": discharge.isoformat() if discharged else None, "expected": 0.18,
                                            "ref": f"Manual · {source.strip() or 'phone'}", "by": actor(), "at": _now_iso()})
                state["transitions"][stay_id] = {"NIA": {"status": "done", "on": snapshot.as_of.isoformat(), "by": actor()}}

            mutate("TRC", "Manual ADT notification recorded", f"{person_id} · {facility} · admitted {admit:%d %b}", change,
                   toast="Transition case opened")
            st.rerun()
