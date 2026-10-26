"""Command: S01 Stars dashboard, S02 Measure explorer, S12 Version center."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from frontend.ui.components import (
    badge,
    card_title,
    render_html_table,
    render_kpi_card,
    render_tiles,
)
from frontend.ui.shell import render_page
from frontend.ui.state import go_to
from frontend.ui.theme import CHART_COLORS, CHART_NEUTRAL, PALETTE
from frontend.vbc import store as vbc_store
from frontend.vbc.catalog import (
    CHANGE_REQUESTS,
    CHANGE_REQUESTS_BY_ID,
    CLAIMS_RUNOUT,
    CUT_POINT_NOTICE,
    DASHBOARD_MEASURES,
    EXCLUSION_LABELS,
    GAP_UNKNOWN,
    MEASURES,
    MP,
    REASON_CODES,
    VALUE_SET_NOTICE,
    VALUE_SET_SOURCE,
    VALUE_SETS,
    WORKABLE_STATUSES,
)
from frontend.vbc.engine import Spec, compute_rate
from frontend.views.vbc.common import (
    callout,
    can_vbc,
    get_snapshot,
    guard,
    header,
    how_it_works,
    mutate,
    open_measure,
    open_person,
    pct,
    status_badge,
)

STATUS_TONES = {"At risk": "red", "Close": "amber", "On track": "green", "Watch": "amber", "No data": "gray"}
PRODUCT_LINES = ("Medicare Advantage", "Commercial", "All")


def _fmt_value(code: str, value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}" if MEASURES[code].direction == "lower" else f"{value:.1f}%"


# ---------------------------------------------------------------------------
# S01 Stars dashboard
# ---------------------------------------------------------------------------


def _freshness(snapshot) -> str:
    parts = []
    for kind in ("EHR", "Lab", "Claims", "ADT"):
        feeds = [feed for feed in snapshot.feeds if feed["kind"] == kind]
        worst = max(feeds, key=lambda feed: snapshot.now - feed["last"])
        hours = (snapshot.now - worst["last"]).total_seconds() / 3600
        stale = hours > worst["expected_hours"]
        age = f"{hours / 24:.0f}d" if hours >= 24 else f"{hours:.0f}h"
        parts.append(f"{kind} {age}{' ⚠' if stale else ''}")
    tone = "amber" if snapshot.stale_feeds() else "green"
    return badge("Freshness: " + " · ".join(parts), tone)


def _trend(snapshot, code: str, ids: set | None):
    rows = []
    month = MP.start
    while month <= snapshot.as_of:
        next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        month_end = min(next_month - timedelta(days=1), snapshot.as_of)
        evaluation = snapshot.evaluate_as_of(month_end, (code,))
        rate = compute_rate(code, evaluation, ids)
        if rate.value is not None:
            rows.append({"Month": month_end, "Series": f"{code} actual", "Rate": rate.value})
        month = next_month
    cut = next((cut for cut in MEASURES[code].cut_points if not rows or rows[-1]["Rate"] < cut), MEASURES[code].cut_points[-1])
    for row in list(rows):
        rows.append({"Month": row["Month"], "Series": "Est. next cut", "Rate": cut})
    return pd.DataFrame(rows), cut


def show_stars():
    render_page("vbc_stars")
    snapshot = get_snapshot()
    header("S01", snapshot)
    sites = ("All sites",) + tuple(sorted({person.site for person in snapshot.people}))
    line_col, site_col, period_col, fresh_col = st.columns([1.5, 1.5, 1.3, 3.2], vertical_alignment="bottom")
    product = line_col.selectbox("Product line", PRODUCT_LINES, key="s01_product")
    site = site_col.selectbox("Site", sites, key="s01_site")
    period_col.markdown(badge(f"{MP.code} → {MP.rating_year}", "violet"), unsafe_allow_html=True)
    fresh_col.markdown(_freshness(snapshot), unsafe_allow_html=True)

    ids = None if product == "All" and site == "All sites" else snapshot.person_ids(product, site)
    rows = snapshot.scorecard(product, site)
    current, projected = snapshot.overall(rows), snapshot.overall(rows, "projected_level")
    needs = sorted((row for row in rows if row["measure"].rated and row["gaps_to_goal"]),
                   key=lambda row: -row["measure"].star_weight * row["gaps_to_goal"])[:2]
    workable = [result for result in snapshot.evaluation.results.values()
                if result.status in WORKABLE_STATUSES and MEASURES[result.measure].rated and (ids is None or result.person_id in ids)]
    unknown = sum(result.status == GAP_UNKNOWN for result in workable)
    verdicts = [verdict for result in snapshot.evaluation.results.values() if result.in_denominator and (ids is None or result.person_id in ids)
                for verdict in result.verdicts]
    accepted = sum(verdict.accepted for verdict in verdicts)
    failures = Counter(verdict.reason_code for verdict in verdicts if not verdict.accepted)
    pcr = snapshot.rate("PCR", product, site)
    tiles = st.columns(4)
    with tiles[0]:
        render_kpi_card(
            "Projected overall Star (est.)",
            f"{current or 0:.1f} → {projected or 0:.1f}" if current else "—",
            "lav",
            "Needs " + " + ".join(f"{row['gaps_to_goal']} {row['code']}" for row in needs) + " closures" if needs else "At or above every next cut",
        )
    with tiles[1]:
        render_kpi_card("Open HEDIS gaps", len(workable), "rose",
                        f"Unknown share {unknown / len(workable) * 100:.1f}% (data, not care)" if workable else "No open gaps")
    with tiles[2]:
        top = failures.most_common(1)
        render_kpi_card("Evidence completeness", pct(accepted / len(verdicts) * 100 if verdicts else None), "mint",
                        f"Top failure: {REASON_CODES[top[0][0]][0].lower()}" if top else "No failed evidence")
    with tiles[3]:
        render_kpi_card("PCR observed / expected", f"{pcr.value:.2f}" if pcr.value is not None else "—", "peach",
                        ("Above expected: review transitions" if (pcr.value or 0) > 1 else "Better than expected") + " · ESTIMATED")

    with st.container(border=True):
        card_title("Measures", "Star weight, projected rate, estimated next cut point and exact closures needed",
                   trailing=badge(f"Overall {current or 0:.1f} ★ now", "violet"))
        widths = [3.2, 0.8, 1.1, 1.1, 1.2, 1.2, 1.1, 0.9]
        for col, label in zip(st.columns(widths), ("Measure", "Star wt", "Current", "Projected", "Est. next cut", "Gaps to goal", "Status", ""), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        for row in rows:
            code = row["code"]
            with st.container(key=f"encrow_off_s01_{code}"):
                cols = st.columns(widths, vertical_alignment="center")
                cols[0].markdown(f'<div class="nc-cell"><b>{code}</b> · {escape(row["measure"].name)}</div>', unsafe_allow_html=True)
                cols[1].markdown(f'<div class="nc-cell">{row["measure"].star_weight or "—"}</div>', unsafe_allow_html=True)
                cols[2].markdown(f'<div class="nc-cell">{_fmt_value(code, row["current"])}</div>', unsafe_allow_html=True)
                cols[3].markdown(f'<div class="nc-cell">{_fmt_value(code, row["projected"])}</div>', unsafe_allow_html=True)
                cols[4].markdown(f'<div class="nc-cell muted">{_fmt_value(code, row["next_cut"]) if row["next_cut"] else "—"}</div>', unsafe_allow_html=True)
                gaps = "n/a" if MEASURES[code].direction == "lower" else (row["gaps_to_goal"] if row["gaps_to_goal"] is not None else "—")
                cols[5].markdown(f'<div class="nc-cell">{gaps}</div>', unsafe_allow_html=True)
                cols[6].markdown(badge(row["status"], STATUS_TONES[row["status"]]), unsafe_allow_html=True)
                cols[7].button("Explore", key=f"s01_open_{code}", type="tertiary", on_click=open_measure, args=(code,),
                               disabled=not can_vbc("S02", "View"))

    chart_col, note_col = st.columns([2.2, 1], gap="medium")
    with chart_col:
        with st.container(border=True):
            at_risk = [row["code"] for row in rows if row["status"] in ("At risk", "Close") and MEASURES[row["code"]].kind == "person"]
            options = [code for code in DASHBOARD_MEASURES if MEASURES[code].kind == "person"]
            default = options.index(at_risk[0]) if at_risk else 0
            title_col, pick_col = st.columns([2, 1], vertical_alignment="bottom")
            with title_col:
                card_title("Trend against the target", "Monthly rate as of each month end, against the estimated next cut point")
            code = pick_col.selectbox("Measure", options, index=default, key="s01_trend", label_visibility="collapsed")
            frame, cut = _trend(snapshot, code, ids)
            if frame.empty:
                st.info("No denominator for this selection yet.")
            else:
                chart = (
                    alt.Chart(frame)
                    .mark_line(strokeWidth=2.5, point=alt.OverlayMarkDef(size=60, filled=True))
                    .encode(
                        x=alt.X("Month:T", title=None, axis=alt.Axis(format="%b", labelColor=PALETTE["muted"], tickColor=PALETTE["border"], domainColor=PALETTE["border"])),
                        y=alt.Y("Rate:Q", title="Rate (%)", scale=alt.Scale(domain=[0, 100]),
                                axis=alt.Axis(labelColor=PALETTE["muted"], titleColor=PALETTE["muted"], gridColor=PALETTE["border"], domain=False)),
                        color=alt.Color("Series:N", scale=alt.Scale(range=[CHART_COLORS[1], CHART_NEUTRAL]),
                                        legend=alt.Legend(title=None, orient="top", labelColor=PALETTE["text"])),
                        strokeDash=alt.StrokeDash("Series:N", scale=alt.Scale(range=[[1, 0], [6, 4]]), legend=None),
                        tooltip=[alt.Tooltip("Month:T", format="%b %Y"), "Series:N", alt.Tooltip("Rate:Q", format=".1f")],
                    )
                    .properties(height=230)
                    .configure_view(strokeWidth=0)
                    .configure(font="Inter, 'Segoe UI', system-ui, sans-serif")
                )
                st.altair_chart(chart, width="stretch")
                st.caption(f"Estimated next cut for {code}: {cut:.0f}%. Each point re-evaluates the measure with the evidence received by that date.")
    with note_col:
        callout("guardrail", "Read with care", CUT_POINT_NOTICE + f" Claims run-out is {CLAIMS_RUNOUT:.0%} this month.")
        callout("handoff", "Population", f"{len(snapshot.person_ids(product, site))} members in this view · {snapshot.export_source}. "
                "Rates are internal calculations, not certified HEDIS results.")
    how_it_works("S01")


# ---------------------------------------------------------------------------
# S02 Measure explorer
# ---------------------------------------------------------------------------


def _drill_table(snapshot, results, key: str):
    if not results:
        st.caption("No persons in this group.")
        return
    widths = [2.4, 1.3, 3.6, 1]
    for col, label in zip(st.columns(widths), ("Person", "Status", "Why", ""), strict=True):
        col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
    for result in results[:40]:
        person = snapshot.person(result.person_id)
        cols = st.columns(widths, vertical_alignment="center")
        cols[0].markdown(f'<div class="nc-cell"><b>{escape(person.name)}</b><div class="nc-meta">{person.age} · {escape(person.person_id)}</div></div>',
                         unsafe_allow_html=True)
        cols[1].markdown(status_badge(result.status), unsafe_allow_html=True)
        cols[2].markdown(f'<div class="nc-cell muted">{escape(result.reason)}</div>', unsafe_allow_html=True)
        cols[3].button("Profile", key=f"{key}_{result.person_id}", type="tertiary", on_click=open_person, args=(result.person_id,),
                       disabled=not can_vbc("S05", "View"))


def show_measures():
    render_page("vbc_measures")
    snapshot = get_snapshot()
    actions = header("S02", snapshot)
    codes = tuple(MEASURES)
    current = st.session_state.get("vbc_measure") if st.session_state.get("vbc_measure") in codes else "KED"
    code = st.selectbox("Measure", codes, index=codes.index(current), format_func=lambda c: MEASURES[c].label, key=f"s02_measure_{current}")
    st.session_state.vbc_measure = code
    measure = MEASURES[code]
    rate = snapshot.rate(code)
    st.markdown(
        '<div class="nc-badge-row">'
        + badge(f"{MP.code} · {snapshot.version}", "blue")
        + badge(measure.data_method, "violet")
        + badge(f"Star weight {measure.star_weight} · Rated" if measure.rated else "Display only", "green" if measure.rated else "gray")
        + badge(measure.pathway, "gray")
        + "</div>",
        unsafe_allow_html=True,
    )
    actions.button("Reconcile to payer", key="s02_reconcile", type="secondary", on_click=go_to, args=("vbc_payer",),
                   disabled=not can_vbc("S09", "View"))

    if code == "PCR":
        render_tiles((("Index stays", rate.denominator), ("Observed readmissions", rate.observed),
                      ("Expected (risk-adjusted)", f"{rate.expected:.2f}"), ("Observed / expected", f"{rate.value:.2f}" if rate.value else "—"),
                      ("MP 2025 (frozen)", f"{measure.prior_rate:.2f}")))
        callout("handoff", "How PCR reads", "Lower is better. Readmissions are shown with outcomes, never as blame; cases are on the Transitions board (S10).")
        how_it_works("S02")
        return

    funnel_col, comp_col = st.columns(2, gap="medium")
    with funnel_col:
        with st.container(border=True):
            card_title("Population funnel", "Each stage is clickable below to see exactly who counts")
            excluded = sum(rate.exclusions.values())
            top = max(rate.initial, 1)
            from frontend.views.vbc.common import bars

            bars([
                ("Initial population", "", rate.initial, top, f"{rate.initial:,}", "violet"),
                ("Denominator exclusions", "", excluded, top, f"−{excluded:,}", "gray"),
                ("Denominator", "", rate.denominator, top, f"{rate.denominator:,}", "blue"),
                ("Numerator met", measure.numerator_label, rate.numerator, top, f"{rate.numerator:,}", "green"),
                ("Rate", f"MP 2025 {measure.prior_rate:.1f}% (frozen)" if measure.prior_rate else "", rate.value or 0, 100, pct(rate.value), "peach"),
            ])
    with comp_col:
        with st.container(border=True):
            from frontend.views.vbc.common import bars

            if code == "FMC":
                card_title("ED visits", "Follow-up within 7 days for members with multiple chronic conditions")
                cases = snapshot.evaluation.fmc_cases
                counts = Counter(case.status for case in cases)
                bars([(status, "", counts.get(status, 0), max(len(cases), 1), str(counts.get(status, 0)), tone)
                      for status, tone in (("Done", "green"), ("Booked", "blue"), ("Due", "amber"), ("Missed", "red"))])
            else:
                card_title("Numerator components", "Multi-part numerators show each component so the weak step is obvious")
                rows = []
                for key, (met, total, label) in rate.components.items():
                    rows.append((label, "", met, max(total, 1), f"{met:,} · {met / total * 100:.1f}%" if total else "—", "violet" if key != "both" else "green"))
                bars(rows or [("No components", "", 0, 1, "—", "gray")])
            if rate.exclusions:
                st.markdown('<div class="nc-label" style="margin-top:.8rem">Denominator exclusions</div>', unsafe_allow_html=True)
                render_html_table(["Exclusion", "Persons"], [[escape(EXCLUSION_LABELS.get(key, key)), str(count)] for key, count in rate.exclusions.most_common()])

    vs_col, rej_col = st.columns([1.1, 1], gap="medium")
    with vs_col:
        with st.container(border=True):
            card_title("Value sets", f"Source: {VALUE_SET_SOURCE}", trailing=badge("Demo — not licensed", "amber"))
            names = sorted({name for component in measure.components for name in component.value_sets})
            render_html_table(["Value set", "Version", "Codes", "Status"],
                              [[escape(name), "Demo VSD MP 2026", str(len(VALUE_SETS[name].codes) + len(VALUE_SETS[name].prefixes)),
                                badge("Loaded ✓", "green")] for name in names] or [["Event-based measure", "—", "—", "—"]])
            st.caption(VALUE_SET_NOTICE)
    with rej_col:
        with st.container(border=True):
            card_title("Rejected evidence", "Counted and linked to a correction task — never silently dropped")
            if not rate.rejections:
                st.caption("No evidence was rejected for this measure.")
            for (component, reason), count in rate.rejections.most_common():
                label = next((c.label for c in measure.components if c.key == component), component)
                st.markdown(
                    f'<div class="nc-banner warn" style="margin-bottom:.4rem"><span>&#9888;</span><div class="nc-grow">'
                    f'<b>{count} {escape(label)} result(s) {"held" if reason == "STS" else "rejected"} · {reason}</b>'
                    f'<small>{escape(REASON_CODES.get(reason, ("", ""))[0])} · owner: {escape(REASON_CODES.get(reason, ("", "—"))[1])}</small></div></div>',
                    unsafe_allow_html=True,
                )
                if reason == "VSM":
                    open_tasks = [t for t in snapshot.state.get("mapping_tasks", []) if t["measure"] == code and t["status"] == "open"]
                    if open_tasks:
                        st.caption(f"Mapping task {open_tasks[0]['id']} open with {open_tasks[0]['owner']} since {open_tasks[0]['at'][:10]}.")
                    elif st.button("Open mapping task with lab interface owner", key=f"s02_map_{code}_{component}", type="secondary",
                                   disabled=not can_vbc("S02", "Edit")):
                        if guard("S02", "Edit", "open a mapping task"):
                            def change(state, component=component, label=label):
                                state["mapping_tasks"].append({"id": vbc_store.next_id(state, "MAP"), "measure": code, "component": component,
                                                               "owner": "Lab interface owner", "status": "open",
                                                               "at": datetime.now().isoformat(timespec="seconds"),
                                                               "detail": f"Map local codes for {label} to the value set"})
                            mutate("EVD", "Mapping task opened", f"{code} {label}: local code not in value set", change, toast="Mapping task opened")
                            st.rerun()

    if measure.kind == "person":
        with st.container(border=True):
            card_title("Strata and version comparison", "Age bands on current data; prior period and the frozen previous version")
            strata = (("18–64", 18, 64), ("65–74", 65, 74), ("75+", 75, 200))
            cells = []
            for label, low, high in strata:
                ids = {p.person_id for p in snapshot.people if p.age is not None and low <= p.age <= high}
                value = compute_rate(code, snapshot.evaluation, ids)
                cells.append((f"Age {label}", f"{pct(value.value)} · n={value.denominator}"))
            frozen = snapshot.state.get("snapshots", {}).get("v2026.1", {}).get("rates", {}).get(code)
            cells += [(f"{MP.code} · {snapshot.version}", pct(rate.value)), ("MP 2025 (frozen)", pct(measure.prior_rate)),
                      ("v2026.1 (frozen)", pct(frozen))]
            render_tiles(cells)

        with st.container(border=True):
            groups = {
                "Not met (open, unknown, pending, preference)": [r for r in snapshot.evaluation.for_measure(code) if r.in_denominator and not r.met],
                "Numerator met": [r for r in snapshot.evaluation.for_measure(code) if r.met],
                "Excluded": [r for r in snapshot.evaluation.for_measure(code) if r.status == "EXCLUDED"],
                "Not in population": [r for r in snapshot.evaluation.for_measure(code) if r.status == "NOT_IN_POPULATION"],
            }
            title_col, pick_col = st.columns([2, 1.3], vertical_alignment="bottom")
            with title_col:
                card_title("Drill to persons", "Every count is reproducible down to the person and the evidence that decided it")
            group = pick_col.selectbox("Group", list(groups), key=f"s02_group_{code}", label_visibility="collapsed")
            _drill_table(snapshot, groups[group], f"s02_drill_{code}")
    how_it_works("S02")


# ---------------------------------------------------------------------------
# S12 Version center
# ---------------------------------------------------------------------------


def _impact(snapshot, cr):
    if not cr.spec_override:
        return None
    overrides = {measure: dict(params) for measure, params in snapshot.spec.overrides.items()}
    for measure, params in cr.spec_override.items():
        overrides.setdefault(measure, {}).update(params)
    evaluation = snapshot.evaluate_with(Spec("draft", overrides), (cr.measure,))
    return snapshot.rate(cr.measure), compute_rate(cr.measure, evaluation)


def _impact_text(cr, impact) -> str:
    if impact is None:
        return "No change on current data" if cr.tests_ok else "Indicator not yet computable"
    old, new = impact
    parts = []
    if new.denominator != old.denominator:
        parts.append(f"Denominator {new.denominator - old.denominator:+d}")
    if new.numerator != old.numerator:
        parts.append(f"numerator {new.numerator - old.numerator:+d}")
    if old.value is not None and new.value is not None and round(new.value - old.value, 1):
        parts.append(f"rate {new.value - old.value:+.1f} pts")
    if cr.change_type == "Moved to ECDS":
        parts.append("data method switched; sampling off")
    return "; ".join(parts) or "No change on current data"


def show_versions():
    render_page("vbc_versions")
    snapshot = get_snapshot()
    header("S12", snapshot)
    states = snapshot.state.get("change_requests", {})
    blocked = sum(item.get("status") == "Blocked" for item in states.values())
    awaiting = sum(item.get("status") == "Awaiting 2nd approver" for item in states.values())
    impacts = {cr.cr_id: _impact(snapshot, cr) for cr in CHANGE_REQUESTS}

    with st.container(border=True):
        card_title(f"{MP.code} change set · {len(CHANGE_REQUESTS)} change requests", "Every NCQA change for the period in one place",
                   trailing=badge(f"{blocked} blocked · {awaiting} awaiting 2nd approver", "amber" if blocked or awaiting else "green"))
        tones = {"Published": "green", "Awaiting 2nd approver": "amber", "Blocked": "red", "Draft": "gray", "Rolled back": "violet"}
        render_html_table(
            ["CR", "Measure", "Change", "Impact (current data)", "Tests", "Approval"],
            [[f"<code>{cr.cr_id}</code>", f"<b>{cr.measure}</b>", f"{escape(cr.change)}<div class='nc-meta'>{escape(cr.change_type)}</div>",
              escape(_impact_text(cr, impacts[cr.cr_id])),
              badge(f"{cr.tests_passed}/{cr.tests_total}{'' if cr.tests_ok else ' ✗'}", "green" if cr.tests_ok else "red"),
              badge(states.get(cr.cr_id, {}).get("status", "Draft"), tones.get(states.get(cr.cr_id, {}).get("status", "Draft"), "gray"))]
             for cr in CHANGE_REQUESTS],
        )

    pick_col, _ = st.columns([2, 3])
    cr_id = pick_col.selectbox("Change request", [cr.cr_id for cr in CHANGE_REQUESTS],
                               format_func=lambda value: f"{value} · {CHANGE_REQUESTS_BY_ID[value].measure} · {CHANGE_REQUESTS_BY_ID[value].change}",
                               key="s12_cr")
    cr = CHANGE_REQUESTS_BY_ID[cr_id]
    entry = states.get(cr_id, {"status": "Draft", "approvals": []})
    impact_col, approve_col = st.columns([1.2, 1], gap="medium")
    with impact_col:
        with st.container(border=True):
            card_title(f"Impact analysis · {cr_id} {cr.measure}", cr.note)
            impact = impacts[cr_id]
            if impact is None:
                st.caption("This change does not alter current results (or the new indicator cannot be computed until tests pass).")
            else:
                old, new = impact
                render_html_table(
                    ["", "Current version", "With this change", "Delta"],
                    [
                        ["Denominator", f"{old.denominator:,}", f"{new.denominator:,}", f"{new.denominator - old.denominator:+,}"],
                        ["Numerator", f"{old.numerator:,}", f"{new.numerator:,}", f"{new.numerator - old.numerator:+,}"],
                        ["Rate", pct(old.value), pct(new.value),
                         f"{(new.value or 0) - (old.value or 0):+.1f} pts" if old.value is not None and new.value is not None else "—"],
                        ["Data method", MEASURES[cr.measure].data_method, cr.spec_override.get(cr.measure, {}).get("data_method", MEASURES[cr.measure].data_method), ""],
                    ],
                )
            st.caption("Change note to care teams is drafted from this analysis; the version boundary shows on dashboards.")
    with approve_col:
        with st.container(border=True):
            card_title(f"Four-eyes approval · {cr_id}", "Two named approvers; history stays frozen under the version that produced it")
            approvals = entry.get("approvals", [])
            checks = [
                ("Draft loaded from specification", True, ""),
                ("Impact analysis reviewed", True, ""),
                ("Test decks and regression passed", cr.tests_ok, f"{cr.tests_passed}/{cr.tests_total}"),
                ("Approver 1: HEDIS lead", len(approvals) >= 1, approvals[0]["by"] if approvals else "pending"),
                ("Approver 2: Quality governance", len(approvals) >= 2, approvals[1]["by"] if len(approvals) > 1 else "pending"),
            ]
            st.markdown("".join(
                f'<div class="vbc-check"><span class="{"ok" if done else "no" if label.startswith("Test") else "hold"}">{"✓" if done else "✗" if label.startswith("Test") else "·"}</span>'
                f"<span>{escape(label)}</span><small>{escape(detail)}</small></div>"
                for label, done, detail in checks
            ), unsafe_allow_html=True)
            status = entry.get("status", "Draft")
            me = f"{st.session_state.get('auth_username', '')}"
            already = any(item.get("username") == me for item in approvals)
            can_approve = can_vbc("S12", "Approve") and status not in ("Published",) and cr.tests_ok and not already
            with st.container(horizontal=True, gap="small"):
                approve = st.button("Approve & publish" if len(approvals) == 1 else "Approve", key=f"s12_approve_{cr_id}", type="primary",
                                    disabled=not can_approve)
                back = st.button("Return to draft", key=f"s12_draft_{cr_id}", type="secondary",
                                 disabled=not can_vbc("S12", "Approve") or status in ("Draft", "Published"))
                rollback = st.button("Roll back", key=f"danger_s12_rollback_{cr_id}", type="secondary",
                                     disabled=not can_vbc("S12", "Approve") or status != "Published")
            if not cr.tests_ok:
                st.caption("Publication blocked: failing tests. The previous version stays live.")
            elif already:
                st.caption("You already approved this change — four-eyes needs a second, different approver.")
            if approve and guard("S12", "Approve", "approve a measure version change"):
                _approve(snapshot, cr, me)
                st.rerun()
            if back and guard("S12", "Approve", "return a change request to draft"):
                mutate("VER", "Change request returned to draft", cr_id, lambda state: state["change_requests"].update({cr_id: {"status": "Draft", "approvals": []}}))
                st.rerun()
            if rollback and guard("S12", "Approve", "roll back a published version"):
                _publish(snapshot, cr, rollback=True)
                st.rerun()

    with st.container(border=True):
        card_title("Version history", "Results stay frozen under the version that produced them")
        render_html_table(["Version", "Published", "By", "Change requests"],
                          [[f"<b>{escape(item['version'])}</b>", escape(item.get("published", "")), escape(item.get("by", "")),
                            escape(", ".join(item.get("crs", [])) or item.get("note", ""))] for item in reversed(snapshot.state.get("versions", []))])
        frozen = snapshot.state.get("snapshots", {})
        if frozen:
            rows = []
            for version, data in frozen.items():
                rows.append([f"<b>{escape(version)}</b>", escape(data.get("frozen_at", ""))]
                            + [pct(data.get("rates", {}).get(code)) if MEASURES[code].direction == "higher" else
                               (f"{data['rates'][code]:.2f}" if data.get("rates", {}).get(code) is not None else "—") for code in DASHBOARD_MEASURES])
            render_html_table(["Frozen version", "Frozen"] + list(DASHBOARD_MEASURES), rows)
    how_it_works("S12")


def _approve(snapshot, cr, username):
    from frontend.views.vbc.common import actor

    def change(state):
        entry = state["change_requests"].setdefault(cr.cr_id, {"status": "Draft", "approvals": []})
        entry["approvals"].append({"by": actor(), "username": username, "at": datetime.now().isoformat(timespec="seconds")})
        entry["status"] = "Awaiting 2nd approver" if len(entry["approvals"]) < 2 else "Approved"

    approvals = snapshot.state.get("change_requests", {}).get(cr.cr_id, {}).get("approvals", [])
    mutate("VER", f"Approval recorded for {cr.cr_id}", f"{cr.measure}: {cr.change}", change, toast="Approval recorded")
    if len(approvals) + 1 >= 2:
        _publish(snapshot, cr)


def _publish(snapshot, cr, rollback: bool = False):
    from frontend.views.vbc.common import actor

    rates = {code: snapshot.rate(code).value for code in DASHBOARD_MEASURES}
    previous = snapshot.version

    def change(state):
        versions = state["versions"]
        last = versions[-1]["version"]
        major, minor = last.lstrip("v").split(".")
        new_version = f"v{major}.{int(minor) + 1}"
        state["snapshots"][previous] = {"frozen_at": date.today().isoformat(), "rates": rates,
                                         "note": f"Frozen when {cr.cr_id} was {'rolled back' if rollback else 'published'}"}
        entry = state["change_requests"].setdefault(cr.cr_id, {"approvals": []})
        entry["status"] = "Rolled back" if rollback else "Published"
        versions.append({"version": new_version, "published": date.today().isoformat(), "by": actor(),
                         "crs": [f"{cr.cr_id} rolled back" if rollback else cr.cr_id]})

    mutate("VER", f"{cr.cr_id} {'rolled back' if rollback else 'published'}", f"{cr.measure}: {cr.change}; {previous} frozen", change,
           toast=f"{cr.cr_id} {'rolled back' if rollback else 'published'} — new version live")
