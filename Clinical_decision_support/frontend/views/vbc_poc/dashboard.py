"""Quality loop POC — population dashboard."""

from __future__ import annotations

from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from app.vbc_poc import catalog
from app.vbc_poc import service as vbc
from frontend.ui.components import badge, card_title, render_html_table, render_kpi_card
from frontend.ui.shell import render_page
from frontend.ui.theme import CHART_COLORS, PALETTE
from frontend.views.vbc_poc.common import (
    actor,
    can_poc,
    flag_disabled_notice,
    fmt_date,
    guard,
    header,
    open_gap,
    open_patient,
    risk_badge,
    run,
    state_badge,
)

OWNERS = ("P. Nair", "J. Ortiz", "L. Brooks", "Dr. A. Rivera", "Dr. S. Bennett")


def _pct(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


def show_dashboard():
    render_page("vbcpoc_dashboard")
    if flag_disabled_notice():
        return
    actions = header("Population dashboard", "20 synthetic patients · 12 months of history · four HEDIS-inspired measures")
    with actions:
        if st.button("Run population evaluation", key="poc_eval_all", icon=":material/refresh:", type="primary",
                     disabled=not can_poc("Edit")) and guard("Edit", "run a population evaluation"):
            run(vbc.evaluate_population, actor(), success="Population re-evaluated")
            st.rerun()
        with st.popover("Reset demo", icon=":material/restart_alt:", disabled=not can_poc("Admin")):
            st.caption("Clears all workflow activity and recaptures the baseline. The synthetic history is unchanged.")
            if st.button("Reset now", key="poc_reset", type="primary") and guard("Admin", "reset the demo"):
                run(vbc.reset_demo, actor(), success="Demo reset")
                st.rerun()
    data = vbc.dashboard()
    kpi = data["kpis"]

    row = st.columns(4)
    cards = (
        ("Eligible patients", f"{kpi['eligible_patients']} <small>/ {kpi['patients']}</small>", "lav", "In at least one measure denominator"),
        ("Open care gaps", str(kpi["open_gaps"]), "rose", f"{kpi['unassigned_gaps']} unassigned · {kpi['overdue']} overdue ({_pct(kpi['overdue_rate'])})"),
        ("High-risk patients", str(kpi["high_risk_patients"]), "peach", "Workload priority from visible rules"),
        ("Gap closure rate", _pct(kpi["closure_rate"]), "mint", f"{kpi['gaps_closed']} closed of {kpi['gaps_total']} opened this period"),
    )
    for col, (label, value, tone, context) in zip(row, cards, strict=True):
        with col:
            render_kpi_card(label, value, tone, context)
    row = st.columns(4)
    delta = (kpi["compliance_rate"] or 0) - (kpi["baseline_rate"] or 0)
    cards = (
        ("Measure compliance", f"{_pct(kpi['compliance_rate'])}", "sky",
         f"Baseline {_pct(kpi['baseline_rate'])} · {'+' if delta >= 0 else ''}{delta:.1f} pts · {data['totals']['met']}/{data['totals']['eligible']} met"),
        ("Data completeness", _pct(kpi["data_completeness_rate"]), "butter", "Evaluations with sufficient evidence"),
        ("Outreach success", _pct(kpi["outreach_success_rate"]), "lav", f"{kpi['outreach_sent']} message(s) sent; success = booked"),
        ("Readmission signal", _pct(kpi["readmission_rate"]), "rose", f"{kpi['readmissions']} of {kpi['discharges']} discharges within 30 days"),
    )
    for col, (label, value, tone, context) in zip(row, cards, strict=True):
        with col:
            render_kpi_card(label, value, tone, context)

    _scorecards(data)
    _trend(data)
    queue_col, dq_col = st.columns([1.55, 1], gap="medium")
    with queue_col:
        _priority_queue(data)
    with dq_col:
        _data_quality(data)
    _before_after(data)


def _scorecards(data: dict) -> None:
    with st.container(border=True):
        card_title("Measure scorecards", "Met, due, excluded and evidence-review counts per measure — compliance = numerator / denominator after exclusions")
        cols = st.columns(4, gap="small")
        for col, card in zip(cols, data["scorecards"], strict=True):
            with col:
                change = "" if card["baseline_rate"] is None or card["rate"] is None else f" · baseline {card['baseline_rate']:.1f}%"
                st.markdown(
                    f'<div class="poc-score"><div class="top"><b>{escape(card["short"])}</b><span>{card["measure_id"]}</span></div>'
                    f'<div class="rate">{_pct(card["rate"])}</div><div class="meta">{card["numerator"]}/{card["denominator"]} met{change}</div>'
                    f'<div class="track"><i style="width:{card["rate"] or 0:.0f}%"></i></div>'
                    f'<div class="meta">{escape(card["target"])}</div><div class="grid">'
                    + "".join(f"<span>{label}<b>{value}</b></span>" for label, value in (
                        ("Met", card["met"]), ("Open", card["open"]), ("Not controlled", card["outcome"]), ("Review", card["review"]),
                        ("Data quality", card["data_quality"]), ("Follow-up", card["follow_up"]), ("Excluded", card["excluded"]),
                        ("Not in denominator", card["not_eligible"])))
                    + "</div></div>",
                    unsafe_allow_html=True,
                )
        pick_col, _ = st.columns([1, 2])
        measure_id = pick_col.selectbox("Drill into denominator", list(catalog.MEASURES), key="poc_drill",
                                        format_func=lambda code: f"{code} · {catalog.MEASURES[code].short}")
        state = vbc.ensure_initialized()
        results = [result for (pid, code), result in vbc.latest_results(state).items() if code == measure_id and result["state"] != "NOT_ELIGIBLE"]
        results.sort(key=lambda result: (result["numerator_met"], result["state"], result["patient_id"]))
        widths = [1.6, 1.4, 4, 1]
        for col, label in zip(st.columns(widths), ("Patient", "Measure state", "Why", ""), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        names = {patient.patient_id: patient.name for patient in vbc.PATIENTS}
        for result in results:
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].markdown(f'<div class="nc-cell"><b>{escape(names[result["patient_id"]])}</b><div class="nc-meta">{result["patient_id"]}</div></div>',
                             unsafe_allow_html=True)
            cols[1].markdown(state_badge(result["state"]), unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell muted">{escape(result["reason"])}</div>', unsafe_allow_html=True)
            cols[3].button("Patient 360", key=f"poc_drill_{result['patient_id']}_{measure_id}", type="tertiary", on_click=open_patient,
                           args=(result["patient_id"],))


def _trend(data: dict) -> None:
    rows = data["trend"]
    if not rows:
        return
    rate_col, gap_col = st.columns([1.5, 1], gap="medium")
    axis = {"labelColor": PALETTE["muted"], "titleColor": PALETTE["muted"], "gridColor": PALETTE["border"], "domain": False}
    x = alt.X("Month:N", sort=[row["month"] for row in rows], title=None,
              axis=alt.Axis(labelColor=PALETTE["muted"], domainColor=PALETTE["border"], tickColor=PALETTE["border"], labelAngle=0))
    with rate_col, st.container(border=True):
        card_title("Compliance trend", "Re-evaluated as of each month end using only evidence known by then")
        frame = pd.DataFrame([{"Month": row["month"], "Series": "All measures", "Rate": row["rate"]} for row in rows]
                             + [{"Month": row["month"], "Series": code, "Rate": value} for row in rows for code, value in row["measures"].items()])
        series = ["All measures"] + list(catalog.MEASURES)
        chart = alt.Chart(frame.dropna()).mark_line(point=alt.OverlayMarkDef(size=45, filled=True)).encode(
            x=x, y=alt.Y("Rate:Q", title="Compliance (%)", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(**axis)),
            color=alt.Color("Series:N", sort=series, scale=alt.Scale(domain=series, range=[PALETTE["text"]] + CHART_COLORS),
                            legend=alt.Legend(title=None, orient="top", labelColor=PALETTE["text"])),
            strokeWidth=alt.condition(alt.datum.Series == "All measures", alt.value(3), alt.value(1.6)),
            tooltip=["Month:N", "Series:N", alt.Tooltip("Rate:Q", format=".1f")],
        ).properties(height=250).configure_view(strokeWidth=0).configure(font="Inter, 'Segoe UI', system-ui, sans-serif")
        st.altair_chart(chart, width="stretch")
    with gap_col, st.container(border=True):
        card_title("Open gaps at month end", "Hover a bar for gaps opened and closed that month")
        frame = pd.DataFrame([{"Month": row["month"], "Open gaps": row["open_gaps"], "Opened": row["opened"], "Closed": row["closed"]} for row in rows])
        chart = alt.Chart(frame).mark_bar(color=CHART_COLORS[3], cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=22).encode(
            x=x, y=alt.Y("Open gaps:Q", title="Open gaps", axis=alt.Axis(**axis)),
            tooltip=["Month:N", "Open gaps:Q", "Opened:Q", "Closed:Q"],
        ).properties(height=250).configure_view(strokeWidth=0).configure(font="Inter, 'Segoe UI', system-ui, sans-serif")
        st.altair_chart(chart, width="stretch")


def _priority_queue(data: dict) -> None:
    with st.container(border=True):
        card_title("Priority queue", "Patients ranked by risk score, then overdue days — the score's factors are always shown")
        widths = [1.7, 1.2, 2.6, 0.8, 1.1, 1.9]
        for col, label in zip(st.columns(widths), ("Patient", "Risk", "Contributing factors", "Gaps", "Next due", "Action"), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        for row in data["priority_queue"][:12]:
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].button(row["name"], key=f"poc_q_{row['patient_id']}", type="tertiary", on_click=open_patient, args=(row["patient_id"],))
            cols[1].markdown(risk_badge({"category": row["risk_category"], "score": row["risk_score"]}), unsafe_allow_html=True)
            cols[2].markdown(f'<div class="nc-cell muted">{escape(" · ".join(row["factors"]) or "—")}</div>', unsafe_allow_html=True)
            cols[3].markdown(f'<div class="nc-cell">{row["open_gaps"]}' + (f' <span class="nc-meta">({row["unassigned"]} new)</span>' if row["unassigned"] else "")
                             + "</div>", unsafe_allow_html=True)
            cols[4].markdown(f'<div class="nc-cell">{fmt_date(row["next_due"])}' + (f'<div class="nc-meta">{row["overdue_days"]} d overdue</div>'
                             if row["overdue_days"] else "") + "</div>", unsafe_allow_html=True)
            if row["unassigned"]:
                with cols[5].popover("Assign", icon=":material/person_add:", disabled=not can_poc("Edit")):
                    owner = st.selectbox("Owner", OWNERS, key=f"poc_q_owner_{row['patient_id']}")
                    st.caption("Confirms each new finding as valid (review), then assigns it with its due date.")
                    if st.button("Assign all new gaps", key=f"poc_q_assign_{row['patient_id']}", type="primary") and guard("Edit", "assign gaps"):
                        _assign_all(row["gap_ids"], owner)
                        st.rerun()
            else:
                cols[5].markdown(badge("Assigned" if row["open_gaps"] else "No open gaps", "green" if row["open_gaps"] else "gray"), unsafe_allow_html=True)


def _assign_all(gap_ids: list[str], owner: str) -> None:
    done = 0
    for gap_id in gap_ids:
        detail = vbc.gap_detail(gap_id)
        status = detail["gap"]["status"]
        if detail["gap"]["owner"]:
            continue
        if status == "DETECTED":
            run(vbc.update_gap, gap_id, actor(), status="REVIEWED", note="Finding confirmed from the priority queue")
            status = "REVIEWED"
        if status == "REVIEWED":
            if run(vbc.update_gap, gap_id, actor(), owner=owner, status="ASSIGNED"):
                done += 1
    st.toast(f"{done} gap(s) assigned to {owner}")


def _data_quality(data: dict) -> None:
    with st.container(border=True):
        card_title("Data-quality panel", "Missing, conflicting, invalid or pending evidence — reconcile instead of re-ordering care")
        if not data["data_quality"]:
            st.success("No data-quality findings.")
        for row in data["data_quality"]:
            text_col, button_col = st.columns([3, 1.2], vertical_alignment="center")
            text_col.markdown(f'<div class="nc-cell"><b>{escape(row["name"])}</b> · {row["measure_id"]} {badge(row["status"], "violet" if row["status"] == "invalid" else "amber")}'
                              f'<div class="nc-meta">{escape("; ".join(row["flags"]))}</div></div>', unsafe_allow_html=True)
            if row["gap_id"]:
                button_col.button("Start reconciliation", key=f"poc_dq_{row['patient_id']}_{row['measure_id']}", type="tertiary",
                                  on_click=open_gap, args=(row["gap_id"],))
            else:
                button_col.markdown(badge("Resolved in result", "green"), unsafe_allow_html=True)


def _before_after(data: dict) -> None:
    with st.container(border=True):
        card_title("Before and after", "Baseline captured at the first evaluation versus now — every change names the evidence that caused it")
        base, now = data["baseline"].get("totals", {}), data["totals"]
        render_html_table(["Checkpoint", "Eligible evaluations", "Met", "Open gaps", "Not controlled", "Evidence review", "Data quality", "Rate"], [
            [f"<b>Baseline</b><div class='nc-meta'>{fmt_date(data['baseline'].get('captured_at'))}</div>", str(base.get("eligible", "—")),
             str(base.get("met", "—")), str(base.get("open", "—")), str(base.get("outcome", "—")), str(base.get("review", "—")),
             str(base.get("data_quality", "—")), _pct(base.get("rate"))],
            ["<b>Now</b>", str(now["eligible"]), str(now["met"]), str(now["open"]), str(now["outcome"]), str(now["review"]), str(now["data_quality"]),
             f"<b>{_pct(now['rate'])}</b>"],
        ])
        activity = data["activity"]
        st.markdown(" ".join(badge(text, "blue") for text in (
            f"{activity['scheduled']} appointment(s) scheduled", f"{activity['completed']} service(s) completed",
            f"{activity['reconciled']} existing record(s) reconciled", f"{activity['verified']} evidence item(s) verified",
            f"{activity['closed']} gap(s) closed", f"{activity['outreach_sent']} outreach sent")), unsafe_allow_html=True)
        if data["changes"]:
            render_html_table(["Patient", "Measure", "Change", "Caused by", "Trigger"], [
                [f"<b>{escape(row['name'])}</b>", row["measure_id"], f"{state_badge(row['from'])} → {state_badge(row['to'])}",
                 "<br>".join(escape(item) for item in row["evidence"]) or "—", f"{escape(row['trigger'])}<div class='nc-meta'>{escape(row['actor'])}</div>"]
                for row in data["changes"]
            ])
        else:
            st.caption("No status changes since the baseline yet. Work a gap end to end and the change appears here with its evidence.")
