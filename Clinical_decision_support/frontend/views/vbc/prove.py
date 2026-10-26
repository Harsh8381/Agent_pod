"""Prove it: S06 Evidence panel, S07 Exclusion review, S08 Supplemental data, S09 Payer reconciliation, S13 Abstraction."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, timedelta
from html import escape

import streamlit as st

from frontend.ui.components import (
    badge,
    card_title,
    render_banner,
    render_html_table,
    render_kpi_card,
    render_tiles,
)
from frontend.ui.shell import render_page
from frontend.ui.state import go_to
from frontend.vbc import store as vbc_store
from frontend.vbc.catalog import (
    BASE_VERSION,
    EXCLUDED,
    EXCLUSION_LABELS,
    EXCLUSION_SUPPRESS_CONFIDENCE,
    GAP_CLOSED,
    HYBRID_SUNSET,
    MEASURES,
    MP,
    PAYERS,
    PERSON_MEASURES,
    REASON_CODES,
    RECONCILIATION_TARGET,
    SOURCES,
    VALUE_SETS,
)
from frontend.vbc.engine import EvalContext, Spec, evaluate
from frontend.vbc.evidence import Fact
from frontend.vbc.population import draw
from frontend.views.vbc.common import (
    SCREEN_PAGES,
    actor,
    bars,
    callout,
    can_vbc,
    default_person,
    fmt,
    get_snapshot,
    guard,
    header,
    how_it_works,
    mutate,
    open_person,
    pick_measure,
    pick_person,
    post_fact,
    status_badge,
)

VERDICT_TONES = {"ACCEPTED": "green", "ON_HOLD": "amber", "REJECTED": "red", "REVIEW_REQUIRED": "violet"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _all_facts(snapshot) -> dict:
    return {fact.fact_id: fact for facts in snapshot.facts.values() for fact in facts}


def _name(snapshot, person_id: str) -> str:
    person = snapshot.person(person_id)
    return person.name if person else person_id


# ---------------------------------------------------------------------------
# S06 Evidence panel
# ---------------------------------------------------------------------------


def _fact_kind(system: str, code: str) -> str:
    if system == "LOINC" and VALUE_SETS["Blood Pressure Observation"].contains(system, code):
        return "bp"
    return {"LOINC": "lab", "LOCAL": "lab", "CPT": "procedure", "HCPCS": "procedure", "ICD-10-CM": "diagnosis", "CVX": "immunization",
            "RXNORM-NAME": "medication"}.get(system, "procedure")


def _component_codes(component) -> list[tuple[str, str]]:
    options = []
    for name in component.value_sets:
        value_set = VALUE_SETS.get(name)
        if value_set is None:
            continue
        if value_set.codes:
            options += [(value_set.system, code) for code in sorted(value_set.codes)[:4]]
        else:
            options += [(value_set.system, f"{prefix} 20 MG TABLET") for prefix in value_set.prefixes[:2]]
    return options


def show_evidence():
    render_page("vbc_evidence")
    snapshot = get_snapshot()
    header("S06", snapshot)
    counts = Counter(verdict.verdict for result in snapshot.evaluation.results.values() for verdict in result.verdicts)
    for col, (label, key, tone, context) in zip(st.columns(4), (
        ("Accepted", "ACCEPTED", "mint", "All eight checks passed — the gap closes"),
        ("On hold", "ON_HOLD", "butter", "Preliminary — re-checked when the final result posts"),
        ("Rejected", "REJECTED", "rose", "Reason code routed to its owner"),
        ("Review required", "REVIEW_REQUIRED", "lav", "Conflicting sources — a person decides"),
    ), strict=True):
        with col:
            render_kpi_card(label, str(counts.get(key, 0)), tone, context)

    default_person(snapshot)
    pick_col, measure_col, status_col = st.columns([2, 1.7, 2.3], vertical_alignment="bottom")
    with pick_col:
        person = pick_person(snapshot, "s06_person")
    if person is None:
        st.info("No persons available.")
        return
    results = {result.measure: result for result in snapshot.evaluation.for_person(person.person_id) if MEASURES[result.measure].kind == "person"}
    codes = tuple(code for code in PERSON_MEASURES if code in results and results[code].verdicts) or tuple(code for code in PERSON_MEASURES if code in results)
    with measure_col:
        code = pick_measure("s06_measure", codes)
    result = results[code]
    status_col.markdown(f'<div class="nc-badge-row" style="justify-content:flex-end">{status_badge(result.status)}'
                        f'<span class="nc-meta">{escape(result.reason)}</span></div>', unsafe_allow_html=True)

    verdicts = sorted(result.verdicts, key=lambda verdict: verdict.fact.on or date.min, reverse=True)
    selected = None
    list_col, check_col, lineage_col = st.columns([1.15, 1.05, 1.15], gap="medium")
    with list_col, st.container(border=True):
        card_title("Evidence received", f"{len(verdicts)} fact(s) for {code} · newest service date first")
        if verdicts:
            index = st.radio(
                "Evidence", list(range(len(verdicts))), key=f"s06_fact_{person.person_id}_{code}", label_visibility="collapsed",
                format_func=lambda i: f"{verdicts[i].fact.description} · {fmt(verdicts[i].fact.on)}",
                captions=[f"{verdict.verdict.replace('_', ' ')} · {verdict.fact.source_label}" for verdict in verdicts],
            )
            selected = verdicts[index]
        else:
            st.caption("No evidence has arrived for this measure. Use the demo feed below to post a result.")
    with check_col, st.container(border=True):
        card_title("Eight checks", "Deterministic rules tied to the measure version — no probabilistic closure")
        if selected:
            rows = []
            for check in selected.checks:
                css = "ok" if check.passed else ("hold" if selected.verdict == "ON_HOLD" and check.code == "STS" else "no")
                mark = "✓" if check.passed else ("…" if css == "hold" else "✗")
                rows.append(f'<div class="vbc-check"><span class="{css}">{mark}</span><span>{escape(check.label)} '
                            f'<code>{check.code}</code></span><small>{escape(check.detail)}</small></div>')
            st.markdown("".join(rows) + f'<div class="vbc-verdict {selected.verdict}">{escape(selected.summary)}</div>'
                        f'<div class="nc-meta" style="margin-top:.4rem">Owner: <b>{escape(selected.owner)}</b>'
                        + (f" · decided by {escape(selected.decided_by)}" if selected.decided_by else "") + "</div>",
                        unsafe_allow_html=True)
        else:
            st.caption("Select a piece of evidence.")
    with lineage_col, st.container(border=True):
        card_title("Lineage", "Source → message → fact → version → decision, append-only")
        if selected:
            lineage_chain(snapshot, selected)
    if selected:
        _evidence_actions(snapshot, person, code, selected)
    _feed_simulator(snapshot, person, code)
    how_it_works("S06")


def lineage_chain(snapshot, verdict):
    fact = verdict.fact
    quarantine = snapshot.state.get("quarantine", {}).get(fact.fact_id)
    broken = bool(quarantine and not quarantine.get("restored"))
    steps = [
        ("head", "Source", fact.source_label),
        ("broken" if broken else "", "Message", "Source message ID missing" if broken else (fact.source_ref or "—")),
        ("", "Normalised fact", f"{fact.system} {fact.code} · {fact.display_value}"),
        ("", "Measure version", f"{verdict.measure} · {snapshot.version} · {MP.code}"),
        ("", "Decision", verdict.verdict.replace("_", " ").title() + (f" by {verdict.decided_by}" if verdict.decided_by else " by rules")),
    ]
    html = '<span class="arrow">→</span>'.join(f'<div class="step {css}"><b>{escape(title)}</b>{escape(detail)}</div>' for css, title, detail in steps)
    st.markdown(f'<div class="vbc-lineage">{html}</div>', unsafe_allow_html=True)
    render_tiles((
        ("Date of service", fmt(fact.on)),
        ("Received", fmt(fact.received)),
        ("Provider", fact.provider or "—"),
        ("Payer can see it", "Yes" if fact.payer_visible or fact.fact_id in snapshot.payer_visible else "No — supplemental needed"),
    ))
    events = [event for event in snapshot.state.get("audit", []) if fact.fact_id in str(event.get("refs") or "") or fact.fact_id in event.get("detail", "")]
    if events:
        st.markdown('<div class="nc-label" style="margin-top:.6rem">Audit events</div>' + "".join(
            f'<div class="nc-meta">#{event["seq"]} · {event["at"][:16].replace("T", " ")} · {escape(event["action"])} · {escape(event["actor"])}</div>'
            for event in events[-3:]), unsafe_allow_html=True)


def _evidence_actions(snapshot, person, code, verdict):
    fact = verdict.fact
    with st.container(border=True):
        if verdict.verdict == "REVIEW_REQUIRED":
            other = _all_facts(snapshot).get(fact.conflict_ref or "")
            card_title("Human validation — conflicting sources", "Both facts side by side. The analyst decides; the source record is never edited.")
            pair = [item for item in (fact, other) if item]
            for col, item in zip(st.columns(len(pair)), pair, strict=True):
                with col:
                    render_tiles((("Source", item.source_label), ("Date of service", fmt(item.on)), ("Code", f"{item.system} {item.code}"),
                                  ("Reference", item.source_ref)))
            note = st.text_input("Decision note (required)", key=f"s06_note_{fact.fact_id}", placeholder="e.g. Operative report confirms the 2018 date")
            allowed = can_vbc("S06", "Approve") and bool(note.strip())
            for col, (winner, loser) in zip(st.columns(len(pair)), [(item, next((x for x in pair if x is not item), None)) for item in pair], strict=True):
                if col.button(f"Accept {winner.source_label.split(' ·')[0]} · {fmt(winner.on)}", key=f"s06_accept_{winner.fact_id}", type="primary",
                              disabled=not allowed) and guard("S06", "Approve", "resolve an evidence conflict"):
                    def change(state, winner=winner, loser=loser):
                        stamp = {"by": actor(), "at": _now_iso(), "note": note.strip()}
                        state["evidence_decisions"][winner.fact_id] = {"decision": "accepted", **stamp}
                        if loser:
                            state["evidence_decisions"][loser.fact_id] = {"decision": "rejected", **stamp}
                    mutate("EVD", "Evidence conflict resolved", f"{person.person_id} · {code}: kept {winner.fact_id} ({fmt(winner.on)}) — {note.strip()}",
                           change, refs={"fact": winner.fact_id}, toast="Conflict resolved and logged")
                    st.rerun()
            return
        if verdict.verdict == "ON_HOLD":
            card_title("Held, not rejected", "The result is preliminary. The gap stays open and is re-checked automatically when the final result posts.")
            if st.button("Simulate the final result posting (demo feed)", key=f"s06_final_{fact.fact_id}", type="secondary",
                         disabled=not can_vbc("S06", "Edit")) and guard("S06", "Edit", "post evidence"):
                final = Fact(f"{fact.fact_id}-F", fact.person_id, fact.kind, fact.system, fact.code, fact.description, fact.on, datetime.now(),
                             fact.source, f"{fact.source_ref} (final)", value=fact.value, unit=fact.unit, provider=fact.provider,
                             payer_visible=fact.payer_visible, extra=dict(fact.extra))
                mutate("EVD", "evidence.received — final result", f"{person.person_id} · {code}: {fact.description} final",
                       lambda state: post_fact(state, final), refs={"fact": final.fact_id}, toast="Final result received and re-checked")
                st.rerun()
            return
        if verdict.accepted:
            card_title("Accepted closure", "The quality team samples automated closures to confirm the rules behave as configured")
            if st.button("Sample this closure for QA", key=f"s06_sample_{fact.fact_id}", type="secondary", disabled=not can_vbc("S06", "Approve")):
                if guard("S06", "Approve", "sample an automated closure"):
                    mutate("EVD", "Automated closure sampled for QA", f"{person.person_id} · {code}: {fact.headline()}", outcome="Agreed",
                           refs={"fact": fact.fact_id}, toast="QA sample recorded")
                    st.rerun()
            return
        reason = verdict.reason_code or ""
        meaning, owner = REASON_CODES.get(reason, ("", "—"))
        card_title(f"Rejected · {reason}", f"{meaning} · routed to {owner}")
        with st.container(horizontal=True, gap="small"):
            if reason == "VSM":
                open_tasks = [task for task in snapshot.state.get("mapping_tasks", []) if task["measure"] == code and task["status"] == "open"]
                if open_tasks:
                    st.caption(f"Mapping task {open_tasks[0]['id']} is open with {open_tasks[0]['owner']}.")
                elif st.button("Open mapping task", key=f"s06_map_{fact.fact_id}", type="primary", disabled=not can_vbc("S06", "Edit")):
                    if guard("S06", "Edit", "open a mapping task"):
                        mutate("EVD", "Mapping task opened", f"{code}: {fact.system} {fact.code} not in value set",
                               lambda state: state["mapping_tasks"].append({
                                   "id": vbc_store.next_id(state, "MAP"), "measure": code, "component": verdict.component, "owner": "Lab interface owner",
                                   "status": "open", "at": _now_iso(), "detail": f"Map {fact.system} {fact.code} to the value set"}),
                               toast="Mapping task opened")
                        st.rerun()
            if reason == "WIN":
                st.button("Offer a new screening (S11) →", key=f"s06_win_{fact.fact_id}", type="primary", on_click=open_person,
                          args=(person.person_id, "vbc_outreach"), disabled=not can_vbc("S11", "View"))
            if reason in ("STR", "SRC") and MEASURES[code].data_method == "Hybrid":
                st.button("Abstract from the chart (S13) →", key=f"s06_abs_{fact.fact_id}", type="secondary", on_click=go_to,
                          args=("vbc_abstraction",), disabled=not can_vbc("S13", "View"))
            if reason == "LIN":
                st.button("Restore lineage (S16) →", key=f"s06_lin_{fact.fact_id}", type="secondary", on_click=go_to,
                          args=("vbc_governance",), disabled=not can_vbc("S16", "View"))
            if st.button("Request correction from the source", key=f"s06_fix_{fact.fact_id}", type="secondary", disabled=not can_vbc("S06", "Edit")):
                if guard("S06", "Edit", "request a source correction"):
                    mutate("EVD", "Correction requested from source", f"{person.person_id} · {code}: {reason} on {fact.source_ref} → {owner}",
                           outcome="Requested", refs={"fact": fact.fact_id}, toast=f"Correction requested from {owner}")
                    st.rerun()


def _feed_simulator(snapshot, person, code):
    measure = MEASURES[code]
    with st.expander("Demo feed · simulate an incoming result (evidence.received)"):
        st.caption("Posts a synthetic message into the evidence pipeline for this person. The eight checks run on the next refresh.")
        components = {component.key: component for component in measure.components}
        c1, c2, c3, c4 = st.columns(4)
        comp_key = c1.selectbox("Component", list(components), format_func=lambda key: components[key].label, key=f"s06_sim_comp_{code}")
        options = _component_codes(components[comp_key]) + [("LOCAL", f"LAB-{code}-01")]
        system, value_code = c2.selectbox("Code", options, key=f"s06_sim_code_{code}_{comp_key}",
                                          format_func=lambda item: f"{item[0]} {item[1]}" + (" (local, unmapped)" if item[0] == "LOCAL" else ""))
        status = c3.selectbox("Result status", ("final", "preliminary", "corrected"), key=f"s06_sim_status_{code}")
        source = c4.selectbox("Source", ("lab_interface", "ehr", "claims", "hie", "scanned", "cpt2", "rpm"), key=f"s06_sim_source_{code}",
                              format_func=lambda key: SOURCES[key].label)
        d1, d2, d3 = st.columns(3)
        on = d1.date_input("Date of service", value=snapshot.as_of - timedelta(days=1), key=f"s06_sim_on_{code}")
        kind = _fact_kind(system, value_code)
        value, unit = None, ""
        if kind == "bp":
            value = (int(d2.number_input("Systolic", 70, 260, 132, key=f"s06_sim_sys_{code}")),
                     int(d3.number_input("Diastolic", 40, 160, 82, key=f"s06_sim_dia_{code}")))
            unit = "mmHg"
        elif kind == "lab":
            value = round(float(d2.number_input("Result value", 0.0, 500.0, 7.2 if code == "GSD" else 30.0, step=0.1, key=f"s06_sim_val_{code}")), 1)
            unit = "%" if code == "GSD" else ""
        if st.button("Post result to the evidence pipeline", key=f"s06_sim_post_{code}", type="primary", disabled=not can_vbc("S06", "Edit")):
            if guard("S06", "Edit", "post evidence"):
                stamp = datetime.now()
                fact = Fact(f"SIM-{code}-{person.person_id}-{stamp:%H%M%S}", person.person_id, kind, system, value_code,
                            f"{components[comp_key].label} (demo feed)", on, stamp, source, f"DEMO {SOURCES[source].format} {stamp:%Y%m%d%H%M%S}",
                            status=status, value=value, unit=unit, structured=source != "scanned", payer_visible=SOURCES[source].payer_visible,
                            extra={"component": comp_key} if system == "LOCAL" else {})
                mutate("EVD", "evidence.received", f"{person.person_id} · {code}: {system} {value_code} ({status}, {SOURCES[source].label})",
                       lambda state: post_fact(state, fact), refs={"fact": fact.fact_id}, toast="Result received — checks applied")
                st.session_state.pop(f"s06_fact_{person.person_id}_{code}", None)
                st.rerun()


# ---------------------------------------------------------------------------
# S07 Exclusion review
# ---------------------------------------------------------------------------


def _decision(decisions: dict, candidate) -> str:
    return decisions.get(candidate.candidate_id, {}).get("decision", "")


def show_exclusions():
    render_page("vbc_exclusions")
    snapshot = get_snapshot()
    header("S07", snapshot)
    decisions = snapshot.state.get("exclusion_decisions", {})
    final = ("confirmed", "rejected")
    candidates = sorted(snapshot.candidates(), key=lambda item: (_decision(decisions, item) in final, -item.confidence))
    open_items = [item for item in candidates if _decision(decisions, item) not in final]
    excluded = [result for result in snapshot.evaluation.results.values() if result.status == EXCLUDED]
    for col, (label, value, tone, context) in zip(st.columns(4), (
        ("In review queue", len(open_items), "lav", "Candidates from unstructured text — never counted until confirmed"),
        ("Outreach paused", sum(item.confidence >= EXCLUSION_SUPPRESS_CONFIDENCE for item in open_items), "butter",
         f"Confidence ≥ {EXCLUSION_SUPPRESS_CONFIDENCE:.2f} while a person reviews"),
        ("Confirmed by a person", sum(_decision(decisions, item) == "confirmed" for item in candidates), "mint", "Documented as structured entries"),
        ("Structured exclusions", len(excluded), "sky", "Hospice, coded diagnoses and confirmed entries applied automatically"),
    ), strict=True):
        with col:
            render_kpi_card(label, str(value), tone, context)

    if not candidates:
        st.info("No exclusion candidates in the current documents.")
        how_it_works("S07")
        return
    queue_col, detail_col = st.columns([1, 1.8], gap="medium")
    with queue_col, st.container(border=True):
        card_title("Candidates", "Highest confidence first; decided items drop to the bottom")
        state_label = {"confirmed": "Confirmed", "rejected": "Rejected", "more_info": "More information requested", "": "Awaiting review"}
        index = st.radio(
            "Candidate", list(range(len(candidates))), key="s07_candidate", label_visibility="collapsed",
            format_func=lambda i: f"{_name(snapshot, candidates[i].person_id)} · {candidates[i].finding}",
            captions=[f"conf {item.confidence:.2f} · {', '.join(item.measures)} · {state_label[_decision(decisions, item)]}" for item in candidates],
        )
    candidate = candidates[index]
    decision = _decision(decisions, candidate)
    person = snapshot.person(candidate.person_id)
    with detail_col, st.container(border=True):
        suppressed = decision not in final and candidate.confidence >= EXCLUSION_SUPPRESS_CONFIDENCE
        card_title(f"{person.name if person else candidate.person_id} · {candidate.finding}", f"{candidate.document} · {candidate.page}",
                   badge("Outreach paused" if suppressed else "Outreach continues", "amber" if suppressed else "gray"))
        lines = "".join(
            f"<div>{'<mark>' + escape(line) + '</mark>' if position == candidate.highlight else escape(line)}</div>"
            for position, line in enumerate(candidate.lines)
        )
        st.markdown(f'<div class="vbc-doc"><div class="doc-head">{escape(candidate.document)} · {escape(candidate.page)} · received '
                    f'{candidate.received:%d %b %Y} · {escape(SOURCES[candidate.source].label)}</div>{lines}</div>', unsafe_allow_html=True)
        render_tiles((
            ("Proposed exclusion", EXCLUSION_LABELS.get(candidate.exclusion, candidate.exclusion)),
            ("Structured code if confirmed", f"ICD-10-CM {candidate.code}"),
            ("Measures affected", ", ".join(candidate.measures)),
            ("Text-match confidence", f"{candidate.confidence:.2f}"),
        ))
        if candidate.rule_note:
            callout("guardrail", "Rule", candidate.rule_note)
        if decision in final:
            item = decisions[candidate.candidate_id]
            st.success(f"{decision.title()} by {item['by']} on {item['at'][:10]}" + (f" — {item['note']}" if item.get("note") else ""))
        else:
            _exclusion_actions(candidate, decision, decisions)

    if decisions:
        with st.container(border=True):
            card_title("Decisions", "Every confirmation, rejection and information request, with the reviewer")
            by_id = {item.candidate_id: item for item in candidates}
            render_html_table(["Candidate", "Person", "Finding", "Decision", "By", "When", "Note"], [
                [f"<code>{escape(cid)}</code>", escape(_name(snapshot, by_id[cid].person_id)) if cid in by_id else "—",
                 escape(by_id[cid].finding) if cid in by_id else "—",
                 badge(item["decision"].replace("_", " "), {"confirmed": "green", "rejected": "red"}.get(item["decision"], "amber")),
                 escape(item["by"]), escape(item["at"][:16].replace("T", " ")), escape(item.get("note", ""))]
                for cid, item in decisions.items()
            ])
    with st.container(border=True):
        card_title("Structured exclusions applied automatically", "Coded, final, person-linked evidence — no review needed")
        if excluded:
            render_html_table(["Person", "Measure", "Exclusion", "Evidence", "Date"], [
                [escape(_name(snapshot, result.person_id)), result.measure, escape(EXCLUSION_LABELS.get(result.exclusion, result.exclusion)),
                 escape(result.exclusion_fact.source_label), fmt(result.exclusion_fact.on)]
                for result in sorted(excluded, key=lambda r: (r.person_id, r.measure))[:14]
            ])
            if len(excluded) > 14:
                st.caption(f"{len(excluded) - 14} more in the measure explorer (S02).")
        else:
            st.caption("None this period.")
    callout("story", "Never an exclusion", "A refusal or an unreachable person stays in the denominator as a preference or barrier.")
    how_it_works("S07")


def _exclusion_actions(candidate, decision, decisions):
    if decision == "more_info":
        item = decisions[candidate.candidate_id]
        st.info(f"More information requested by {item['by']} on {item['at'][:10]}: {item.get('note', '')}")
    note = st.text_area("Reviewer note", key=f"s07_note_{candidate.candidate_id}", height=70,
                        placeholder="What you checked and why — this goes to the audit log")
    verified = True
    if candidate.exclusion == "hospice":
        verified = st.checkbox(f"I checked hospice enrollment and claims for {MP.code} and they confirm hospice care this period",
                               key=f"s07_hospice_{candidate.candidate_id}")
    if not candidate.complete:
        st.warning("Exclusion criteria are not met by this document. It cannot be confirmed; request the missing evidence instead.")
    approve = can_vbc("S07", "Approve")
    with st.container(horizontal=True, gap="small"):
        confirm = st.button("Confirm and document", key=f"s07_confirm_{candidate.candidate_id}", type="primary",
                            disabled=not approve or not candidate.complete or not verified)
        reject = st.button("Reject", key=f"s07_reject_{candidate.candidate_id}", type="secondary", disabled=not approve)
        more = st.button("Request more information", key=f"s07_more_{candidate.candidate_id}", type="secondary", disabled=not can_vbc("S07", "Edit"))
    stamp = {"by": actor(), "at": _now_iso(), "note": note.strip()}
    if confirm and guard("S07", "Approve", "confirm an exclusion"):
        fact = Fact(f"ATT-{candidate.candidate_id}", candidate.person_id, "diagnosis", "ICD-10-CM", candidate.code,
                    f"{candidate.finding} — confirmed exclusion (structured entry)", candidate.received, datetime.now(), "attestation",
                    f"Exclusion review {candidate.candidate_id}", provider=actor(),
                    extra={"exclusion": candidate.exclusion, "candidate": candidate.candidate_id, "document": candidate.document})

        def change(state):
            state["exclusion_decisions"][candidate.candidate_id] = {"decision": "confirmed", **stamp}
            post_fact(state, fact)

        mutate("EXC", "Exclusion confirmed and documented", f"{candidate.person_id} · {candidate.finding} · ICD-10-CM {candidate.code} · {', '.join(candidate.measures)}",
               change, refs={"candidate": candidate.candidate_id, "fact": fact.fact_id}, toast="Confirmed — status becomes EXCLUDED and evidence queues for the payer")
        st.rerun()
    if reject and guard("S07", "Approve", "reject an exclusion candidate"):
        mutate("EXC", "Exclusion candidate rejected", f"{candidate.person_id} · {candidate.finding}",
               lambda state: state["exclusion_decisions"].update({candidate.candidate_id: {"decision": "rejected", **stamp}}),
               refs={"candidate": candidate.candidate_id}, toast="Rejected — outreach resumes")
        st.rerun()
    if more and guard("S07", "Edit", "request more information"):
        mutate("EXC", "More information requested", f"{candidate.person_id} · {candidate.finding}: {note.strip() or 'missing evidence'}",
               lambda state: state["exclusion_decisions"].update({candidate.candidate_id: {"decision": "more_info", **stamp}}),
               outcome="Requested", refs={"candidate": candidate.candidate_id}, toast="Request sent")
        st.rerun()


# ---------------------------------------------------------------------------
# S08 Supplemental data
# ---------------------------------------------------------------------------

BATCH_TONES = {"DRAFT": "gray", "RETRY_QUEUED": "amber", "AWAITING_ACK": "blue", "ACCEPTED": "green", "PARTIAL": "amber", "REJECTED_BY_PAYER": "red"}
PAYER_REJECTIONS = ("Missing rendering provider NPI", "Member ID mismatch", "Date of service format")


def _latest_records(state: dict) -> dict:
    latest = {}
    for batch in state.get("batches", []):
        for record in batch.get("records", []):
            if record.get("state") != "resubmitted":
                latest[record["fact_id"]] = (batch, record)
    return latest


def supplemental_queue(snapshot) -> list[dict]:
    """Closures and confirmed exclusions resting on evidence the payer cannot see in its own data."""
    latest = _latest_records(snapshot.state)
    rows = []
    for result in snapshot.evaluation.results.values():
        if MEASURES[result.measure].kind != "person":
            continue
        if result.status == GAP_CLOSED and result.closing:
            fact, kind = result.closing.fact, "Closure"
        elif result.status == EXCLUDED and result.exclusion_fact:
            fact, kind = result.exclusion_fact, "Exclusion"
        else:
            continue
        person = snapshot.person(result.person_id)
        if not person or not person.payer_key or fact.payer_visible or fact.fact_id in snapshot.payer_visible:
            continue
        batch, record = latest.get(fact.fact_id, (None, None))
        if record is None:
            state = "Withheld — proof of service missing" if fact.source == "abstraction" and not fact.extra.get("proof") else "Queued"
        elif record["state"] == "rejected":
            state = f"Rejected: {record.get('reason')}"
        else:
            state = "In draft" if batch["state"] == "DRAFT" else "Submitted — awaiting acknowledgement"
        rows.append({"fact": fact, "person_id": result.person_id, "measure": result.measure, "payer": person.payer_key, "kind": kind,
                     "state": state, "batch": batch["batch_id"] if batch else None})
    return sorted(rows, key=lambda row: (row["payer"], row["state"] != "Queued", -MEASURES[row["measure"]].star_weight, row["measure"], row["person_id"]))


def show_supplemental():
    render_page("vbc_supplemental")
    snapshot = get_snapshot()
    header("S08", snapshot)
    queue = supplemental_queue(snapshot)
    batches = snapshot.state.get("batches", [])
    records = [record for batch in batches if batch["state"] not in ("DRAFT",) for record in batch["records"]]
    accepted = sum(record["state"] == "accepted" for record in records)
    rejected_open = [(batch, record) for batch in batches for record in batch["records"] if record["state"] == "rejected"]
    decided = accepted + sum(record["state"] in ("rejected", "resubmitted") for record in records)

    for payer in PAYERS.values():
        days = (payer.supplemental_cutoff - snapshot.as_of).days
        open_items = sum(row["payer"] == payer.key and row["state"] == "Queued" for row in queue)
        open_items += sum(batch["payer"] == payer.key for batch, _ in rejected_open)
        tone = "danger" if days <= 21 and open_items else "warn" if days <= 45 else "info"
        render_banner(
            f"{payer.name} ({payer.label}) · supplemental cut-off {payer.supplemental_cutoff:%d %b %Y} — {max(days, 0)} days left",
            f"{open_items} item(s) still open · {payer.format}, {payer.cadence}"
            + (" · escalated to the HEDIS lead with the open list and expected rate impact" if tone == "danger" else ""),
            tone=tone, icon="&#9888;" if tone != "info" else "&#9432;",
        )
    for col, (label, value, tone, context) in zip(st.columns(4), (
        ("Records submitted", str(len(records)), "sky", f"{len(batches)} batch(es) this period"),
        ("Acceptance rate", f"{accepted / decided:.0%}" if decided else "—", "mint", f"{accepted} accepted by payers"),
        ("Rejected to fix", str(len(rejected_open)), "rose", "Parsed reasons, fixed at source, resubmitted"),
        ("Queued, not yet sent", str(sum(row["state"] == "Queued" for row in queue)), "butter", "Closures and exclusions the payer cannot see"),
    ), strict=True):
        with col:
            render_kpi_card(label, value, tone, context)

    queue_col, batch_col = st.columns([1.15, 1.35], gap="medium")
    with queue_col, st.container(border=True):
        card_title("Queue by payer", "Packaged with provenance: source type, system, received date and message ID")
        payer_key = st.radio("Payer", list(PAYERS), horizontal=True, key="s08_payer", format_func=lambda key: f"{PAYERS[key].label} · {PAYERS[key].name}")
        rows = [row for row in queue if row["payer"] == payer_key]
        if rows:
            render_html_table(["Person", "Measure", "Type", "Evidence", "State"], [
                [escape(_name(snapshot, row["person_id"])), row["measure"], row["kind"],
                 f"{escape(row['fact'].description[:38])}<div class='nc-meta'>{escape(row['fact'].source_ref[:34])}</div>",
                 badge(row["state"].split(":")[0].split(" —")[0], "green" if row["state"] == "Queued" else "red" if row["state"].startswith(("Rejected", "Withheld")) else "blue")]
                for row in rows[:14]
            ])
            if len(rows) > 14:
                st.caption(f"{len(rows) - 14} more not shown.")
        else:
            st.caption("Nothing waiting for this payer.")
        ready = [row for row in rows if row["state"] == "Queued"]
        if st.button(f"Build batch for {PAYERS[payer_key].label} ({len(ready)})", key="s08_build", type="primary",
                     disabled=not ready or not can_vbc("S08", "Edit")) and guard("S08", "Edit", "build a supplemental batch"):
            def change(state):
                batch_id = vbc_store.next_id(state, "SB")
                state["batches"].append({
                    "batch_id": batch_id, "payer": payer_key, "format": PAYERS[payer_key].format, "state": "DRAFT",
                    "created": snapshot.as_of.isoformat(), "submitted": None, "signed_by": None, "built_by": actor(),
                    "records": [{"fact_id": row["fact"].fact_id, "person_id": row["person_id"], "measure": row["measure"], "kind": row["kind"],
                                 "state": "pending", "reason": None} for row in ready],
                })
                st.session_state.s08_pending_batch = batch_id
            mutate("SUP", "Supplemental batch built", f"{PAYERS[payer_key].label}: {len(ready)} record(s)", change, toast="Draft batch built — awaiting signature")
            st.rerun()
        withheld = [row for row in rows if row["state"].startswith("Withheld")]
        if withheld:
            st.caption(f"{len(withheld)} abstracted record(s) withheld until proof of service is attached (S13).")
    with batch_col, st.container(border=True):
        card_title("Batches", "Idempotent submissions; nothing shows success until the payer acknowledges")
        if not batches:
            st.caption("No batches yet.")
        else:
            render_html_table(["Batch", "Payer", "Created", "State", "Records", "Accepted", "Rejected", "Signed by"], [
                [f"<code>{batch['batch_id']}</code>", PAYERS[batch["payer"]].label, escape(batch["created"]),
                 badge(batch["state"].replace("_", " ").title(), BATCH_TONES.get(batch["state"], "gray")), str(len(batch["records"])),
                 str(sum(r["state"] == "accepted" for r in batch["records"])), str(sum(r["state"] in ("rejected", "resubmitted") for r in batch["records"])),
                 escape(batch.get("signed_by") or "—")]
                for batch in reversed(batches)
            ])
            ids = [batch["batch_id"] for batch in reversed(batches)]
            if "s08_pending_batch" in st.session_state:
                st.session_state.s08_batch = st.session_state.pop("s08_pending_batch")
            if st.session_state.get("s08_batch") not in ids:
                st.session_state.s08_batch = ids[0]
            chosen = st.selectbox("Open batch", ids, key="s08_batch")
            _batch_detail(snapshot, next(batch for batch in batches if batch["batch_id"] == chosen))
    if rejected_open:
        with st.container(border=True):
            card_title("Why payers rejected records", "Reasons parsed from acknowledgements")
            reasons = Counter(record.get("reason") or "Unspecified" for _, record in rejected_open)
            bars([(reason, "", count, max(reasons.values()), str(count), "peach") for reason, count in reasons.most_common()])
    how_it_works("S08")


def _batch_detail(snapshot, batch):
    facts = _all_facts(snapshot)
    render_html_table(["Person", "Measure", "Evidence", "State", "Reason"], [
        [escape(_name(snapshot, record["person_id"])), record["measure"],
         escape((facts[record["fact_id"]].description if record["fact_id"] in facts else record["fact_id"])[:40]),
         badge(record["state"], {"accepted": "green", "rejected": "red", "resubmitted": "gray"}.get(record["state"], "blue")),
         escape(record.get("reason") or "—")]
        for record in batch["records"][:12]
    ])
    state = batch["state"]
    key = batch["batch_id"]
    with st.container(horizontal=True, gap="small"):
        if state == "DRAFT":
            timeout = st.checkbox("Simulate payer API timeout", key=f"s08_timeout_{key}")
            if st.button("Sign and submit", key=f"s08_submit_{key}", type="primary", disabled=not can_vbc("S08", "Approve")):
                if guard("S08", "Approve", "sign and submit a supplemental batch"):
                    token = hashlib.sha256("|".join(sorted(r["fact_id"] for r in batch["records"])).encode()).hexdigest()[:12]

                    def change(state_):
                        for item in state_["batches"]:
                            if item["batch_id"] == key:
                                item.update({"signed_by": actor(), "submitted": snapshot.as_of.isoformat(), "idempotency_key": token,
                                             "state": "RETRY_QUEUED" if timeout else "AWAITING_ACK", "attempts": 1})
                    mutate("SUP", f"Batch {key} signed and submitted", f"{PAYERS[batch['payer']].label} · {len(batch['records'])} record(s) · key {token}",
                           change, outcome="Timeout — retry queued" if timeout else "Submitted", refs={"batch": key},
                           toast="Payer API timed out — retry queued" if timeout else "Submitted — awaiting acknowledgement")
                    st.rerun()
        elif state == "RETRY_QUEUED":
            st.caption(f"Attempt {batch.get('attempts', 1)} timed out. The retry reuses idempotency key {batch.get('idempotency_key')} so nothing is submitted twice.")
            if st.button("Retry submission", key=f"s08_retry_{key}", type="primary", disabled=not can_vbc("S08", "Edit")) and guard("S08", "Edit", "retry a submission"):
                def change(state_):
                    for item in state_["batches"]:
                        if item["batch_id"] == key:
                            item["state"], item["attempts"] = "AWAITING_ACK", item.get("attempts", 1) + 1
                mutate("SUP", f"Batch {key} retried (idempotent)", f"key {batch.get('idempotency_key')}", change, outcome="Submitted", refs={"batch": key},
                       toast="Retried — awaiting acknowledgement")
                st.rerun()
        elif state == "AWAITING_ACK":
            if st.button("Simulate payer acknowledgement", key=f"s08_ack_{key}", type="primary", disabled=not can_vbc("S08", "Edit")):
                if guard("S08", "Edit", "record a payer acknowledgement"):
                    def change(state_):
                        for item in state_["batches"]:
                            if item["batch_id"] != key:
                                continue
                            for record in item["records"]:
                                roll = draw(record["fact_id"], f"ack-{key}")
                                if record["state"] == "pending" and roll < 0.18 and not record.get("fixed"):
                                    record["state"], record["reason"] = "rejected", PAYER_REJECTIONS[int(roll * 100) % len(PAYER_REJECTIONS)]
                                elif record["state"] == "pending":
                                    record["state"] = "accepted"
                            rejected = sum(r["state"] == "rejected" for r in item["records"])
                            item["state"] = "ACCEPTED" if not rejected else "PARTIAL" if rejected < len(item["records"]) else "REJECTED_BY_PAYER"
                    mutate("SUP", f"Acknowledgement received for {key}", PAYERS[batch["payer"]].label, change, refs={"batch": key},
                           toast="Acknowledgement processed")
                    st.rerun()
        rejected = [record for record in batch["records"] if record["state"] == "rejected"]
        if rejected and st.button(f"Fix at source and resubmit ({len(rejected)})", key=f"s08_fix_{key}", type="secondary",
                                  disabled=not can_vbc("S08", "Edit")) and guard("S08", "Edit", "resubmit rejected records"):
            def change(state_):
                new_id = vbc_store.next_id(state_, "SB")
                for item in state_["batches"]:
                    if item["batch_id"] == key:
                        for record in item["records"]:
                            if record["state"] == "rejected":
                                record["state"] = "resubmitted"
                                record["resubmitted_in"] = new_id
                state_["batches"].append({
                    "batch_id": new_id, "payer": batch["payer"], "format": batch["format"], "state": "DRAFT", "created": snapshot.as_of.isoformat(),
                    "submitted": None, "signed_by": None, "built_by": actor(), "resubmission_of": key,
                    "records": [{**{k: record[k] for k in ("fact_id", "person_id", "measure")}, "kind": record.get("kind", "Closure"), "state": "pending",
                                 "reason": None, "fixed": record.get("reason")} for record in rejected],
                })
                st.session_state.s08_pending_batch = new_id
            mutate("SUP", f"Rejected records corrected and re-queued from {key}", f"{len(rejected)} record(s): " + ", ".join(sorted({r.get('reason') or '' for r in rejected})),
                   change, refs={"batch": key}, toast="Corrections queued in a new draft batch")
            st.rerun()


# ---------------------------------------------------------------------------
# S09 Payer reconciliation
# ---------------------------------------------------------------------------

RECON_MEASURES = ("GSD", "CBP", "KED", "EED", "BCS-E", "COL-E")
PAYER_VERSIONS = {"A": None, "B": BASE_VERSION}  # Payer B still runs the base specification for MP 2026
CAUSE_ROUTES = {
    "Evidence not visible": ("S08", "Send supplemental data"),
    "Exclusion not shared": ("S08", "Send exclusion evidence"),
    "Timing": ("S08", "Await acknowledgement"),
    "Attribution": ("dispute", "Raise attribution dispute"),
    "Version": ("S12", "Explain version difference"),
    "Enrollment": ("S05", "Resolve eligibility"),
    "Payer evidence we lack": ("S06", "Request the payer's evidence"),
    "Population difference": ("S02", "Compare population rules"),
    "Identity review": ("S05", "Held for MPI steward"),
}


def _category(status: str) -> str:
    return {"GAP_CLOSED": "Met", "EXCLUDED": "Excluded", "NOT_IN_POPULATION": "Not in population"}.get(status, "Not met")


def reconcile(snapshot, payer_key: str) -> list[dict]:
    spec = snapshot.spec if PAYER_VERSIONS[payer_key] is None else Spec(PAYER_VERSIONS[payer_key], {})
    theirs = evaluate(snapshot.people, snapshot.facts, snapshot.layer,
                      EvalContext(snapshot.as_of, spec, snapshot.state, payer_view=True, payer_visible=snapshot.payer_visible), RECON_MEASURES)
    same_version = None
    if spec.overrides != snapshot.spec.overrides:
        same_version = evaluate(snapshot.people, snapshot.facts, snapshot.layer,
                                EvalContext(snapshot.as_of, snapshot.spec, snapshot.state, payer_view=True, payer_visible=snapshot.payer_visible),
                                RECON_MEASURES)
    in_flight = {record["fact_id"]: batch["batch_id"] for batch in snapshot.state.get("batches", [])
                 if batch["state"] in ("DRAFT", "AWAITING_ACK", "RETRY_QUEUED") for record in batch["records"] if record["state"] == "pending"}
    rows = []
    for person in snapshot.people:
        if person.payer_key != payer_key:
            continue
        for code in RECON_MEASURES:
            ours, payer = snapshot.evaluation.get(person.person_id, code), theirs.get(person.person_id, code)
            if ours is None or payer is None:
                continue
            ours_cat, payer_cat = _category(ours.status), _category(payer.status)
            if person.attribution_ended:
                payer_cat = "Not attributed"
            if ours_cat == "Not in population" and payer_cat in ("Not in population", "Not attributed"):
                continue
            cause = None
            if person.identity != "confident":
                cause = "Identity review"
            elif ours_cat == payer_cat:
                cause = None
            elif person.attribution_ended:
                cause = "Attribution"
            elif person.coverage != "continuous":
                cause = "Enrollment"
            elif same_version is not None and _category(same_version.get(person.person_id, code).status) == ours_cat:
                cause = "Version"
            elif ours_cat == "Met":
                cause = "Timing" if ours.closing and ours.closing.fact.fact_id in in_flight else "Evidence not visible"
            elif ours_cat == "Excluded":
                cause = "Exclusion not shared"
            elif payer_cat == "Met":
                cause = "Payer evidence we lack"
            else:
                cause = "Population difference"
            rows.append({"person_id": person.person_id, "measure": code, "ours": ours.status, "ours_cat": ours_cat, "payer_cat": payer_cat,
                         "cause": cause, "held": cause == "Identity review"})
    return rows


def show_payer():
    render_page("vbc_payer")
    snapshot = get_snapshot()
    header("S09", snapshot)
    results = {key: reconcile(snapshot, key) for key in PAYERS}
    cards = st.columns(len(PAYERS), gap="medium")
    for col, (key, payer) in zip(cards, PAYERS.items(), strict=True):
        rows = results[key]
        counted = [row for row in rows if not row["held"]]
        agree = sum(row["cause"] is None for row in counted)
        rate = agree / len(counted) * 100 if counted else 100.0
        version = PAYER_VERSIONS[key] or snapshot.version
        with col, st.container(border=True):
            card_title(f"{payer.label} · {payer.name}", f"{payer.format} · gap list received {fmt(snapshot.as_of - timedelta(days=3 if key == 'A' else 1))}",
                       badge(f"Spec {version}", "blue" if version == snapshot.version else "amber"))
            st.markdown(f'<div class="vbc-big">{rate:.1f}% <small>agreement · target {RECONCILIATION_TARGET:.0f}%</small></div>', unsafe_allow_html=True)
            bars([("Agreement", f"{agree} of {len(counted)} person-measure pairs", rate, 100, f"{rate:.1f}%", "green" if rate >= RECONCILIATION_TARGET else "amber")])
            st.caption(f"{sum(row['held'] for row in rows)} pair(s) held in identity review and left out of the agreement figure.")

    payer_key = st.radio("Reconcile", list(PAYERS), horizontal=True, key="s09_payer", format_func=lambda key: f"{PAYERS[key].label} · {PAYERS[key].name}")
    rows = results[payer_key]
    issues = [row for row in rows if row["cause"]]
    if PAYER_VERSIONS[payer_key] and PAYER_VERSIONS[payer_key] != snapshot.version:
        callout("handoff", f"{PAYERS[payer_key].label} still runs {PAYER_VERSIONS[payer_key]}",
                f"We report under {snapshot.version}. Differences caused by later published changes are labelled Version — explained, not argued.")
    cause_col, list_col = st.columns([1, 2], gap="medium")
    with cause_col, st.container(border=True):
        card_title("Why we disagree", "Every difference classified by cause and routed")
        causes = Counter(row["cause"] for row in issues)
        if causes:
            bars([(cause, CAUSE_ROUTES[cause][1], count, max(causes.values()), str(count), "blue" if cause != "Identity review" else "gray")
                  for cause, count in causes.most_common()])
            recoverable = sum(causes.get(name, 0) for name in ("Evidence not visible", "Exclusion not shared", "Timing"))
            st.markdown(f'<div class="nc-meta" style="margin-top:.6rem"><b>{recoverable}</b> pair(s) recoverable through supplemental data before the cut-off.</div>',
                        unsafe_allow_html=True)
        else:
            st.success("Full agreement with this payer.")
    with list_col, st.container(border=True):
        card_title("Person by person", "Ours versus the payer's list, with the cause and the next step")
        disputes = {item["person_id"] for item in snapshot.state.get("disputes", []) if item["payer"] == payer_key}
        widths = [2, 0.9, 1.1, 1.1, 1.5, 1.8]
        for col, label in zip(st.columns(widths), ("Person", "Measure", "Ours", "Payer", "Cause", "Next step"), strict=True):
            col.markdown(f'<div class="nc-label" style="margin:0">{label}</div>', unsafe_allow_html=True)
        for row in issues[:25]:
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].markdown(f'<div class="nc-cell"><b>{escape(_name(snapshot, row["person_id"]))}</b></div>', unsafe_allow_html=True)
            cols[1].markdown(f'<div class="nc-cell">{row["measure"]}</div>', unsafe_allow_html=True)
            cols[2].markdown(badge(row["ours_cat"], "green" if row["ours_cat"] == "Met" else "gray" if row["ours_cat"] == "Excluded" else "red"), unsafe_allow_html=True)
            cols[3].markdown(badge(row["payer_cat"], "green" if row["payer_cat"] == "Met" else "gray" if row["payer_cat"] != "Not met" else "red"), unsafe_allow_html=True)
            cols[4].markdown(badge(row["cause"], "violet" if row["cause"] == "Version" else "amber"), unsafe_allow_html=True)
            route, label = CAUSE_ROUTES[row["cause"]]
            key = f"s09_go_{payer_key}_{row['person_id']}_{row['measure']}"
            if route == "dispute":
                if row["person_id"] in disputes:
                    cols[5].markdown(badge("Dispute open", "blue"), unsafe_allow_html=True)
                elif cols[5].button(label, key=key, type="tertiary", disabled=not can_vbc("S09", "Edit")) and guard("S09", "Edit", "raise a dispute"):
                    _raise_dispute(snapshot, payer_key, row["person_id"])
                    st.rerun()
            else:
                cols[5].button(label, key=key, type="tertiary", on_click=open_person, args=(row["person_id"], SCREEN_PAGES[route]),
                               disabled=not can_vbc(route, "View"))
        if len(issues) > 25:
            st.caption(f"{len(issues) - 25} more differences not shown.")
        if not issues:
            st.caption("No differences.")
    _disputes(snapshot, payer_key)
    how_it_works("S09")


def _raise_dispute(snapshot, payer_key: str, person_id: str):
    person = snapshot.person(person_id)
    detail = f"Attribution ended {person.attribution_ended:%d %b %Y}; payer list no longer attributes this member to {person.pcp}" if person.attribution_ended else "Attribution differs"

    def change(state):
        state["disputes"].append({"id": vbc_store.next_id(state, "DSP"), "payer": payer_key, "person_id": person_id, "type": "Attribution",
                                  "detail": detail, "raised_by": actor(), "at": _now_iso(), "status": "Open"})

    mutate("PAY", "Attribution dispute raised", f"{PAYERS[payer_key].label} · {person_id}: {detail}", change, toast="Dispute raised with the payer")


def _disputes(snapshot, payer_key: str):
    items = [item for item in snapshot.state.get("disputes", []) if item["payer"] == payer_key]
    with st.container(border=True):
        card_title("Disputes with this payer", "Attribution and version disagreements tracked to outcome — not worked as care gaps")
        if not items:
            st.caption("No disputes raised.")
            return
        for item in items[::-1]:
            text_col, action_col = st.columns([3, 2], vertical_alignment="center")
            text_col.markdown(f'<div class="nc-cell"><b>{escape(item["id"])} · {escape(_name(snapshot, item["person_id"]))}</b> '
                              f'{badge(item["status"], "blue" if item["status"] == "Open" else "green")}'
                              f'<div class="nc-meta">{escape(item["detail"])} · raised by {escape(item["raised_by"])} {item["at"][:10]}'
                              + (f" · outcome: {escape(item['outcome'])}" if item.get("outcome") else "") + "</div></div>", unsafe_allow_html=True)
            if item["status"] == "Open":
                with action_col, st.container(horizontal=True, gap="small"):
                    for label, outcome in (("Upheld", "Upheld — member moved; removed from our list"), ("Denied", "Denied — stays attributed")):
                        if st.button(label, key=f"s09_out_{item['id']}_{label}", type="tertiary", disabled=not can_vbc("S09", "Approve")):
                            if guard("S09", "Approve", "record a dispute outcome"):
                                def change(state, dispute_id=item["id"], outcome=outcome):
                                    for dispute in state["disputes"]:
                                        if dispute["id"] == dispute_id:
                                            dispute.update({"status": "Closed", "outcome": outcome, "closed_by": actor(), "closed_at": _now_iso()})
                                mutate("PAY", "Dispute outcome recorded", f"{item['id']}: {outcome}", change, toast="Outcome recorded")
                                st.rerun()


# ---------------------------------------------------------------------------
# S13 Abstraction
# ---------------------------------------------------------------------------

SAMPLE_SIZE = 8
PACKET_TONES = {"Administrative hit": "green", "To abstract": "gray", "Abstracted · QA pending": "blue", "QA agreed": "green",
                "QA disagreed": "red", "Not found": "amber"}


def _hybrid_measures(snapshot) -> tuple:
    return tuple(code for code in PERSON_MEASURES
                 if snapshot.spec.param(code, "data_method", MEASURES[code].data_method) == "Hybrid" and "abstraction" in MEASURES[code].allowed_sources)


def _packet_status(snapshot, code: str, result) -> str:
    record = snapshot.state.get("abstractions", {}).get(f"{code}:{result.person_id}")
    if record:
        return record["status"]
    if result.status == GAP_CLOSED and result.closing and result.closing.fact.source != "abstraction":
        return "Administrative hit"
    return "To abstract"


def show_abstraction():
    render_page("vbc_abstraction")
    snapshot = get_snapshot()
    header("S13", snapshot)
    moved = [code for code in PERSON_MEASURES if snapshot.spec.param(code, "data_method", MEASURES[code].data_method) != MEASURES[code].data_method]
    render_banner(f"Hybrid method in {MP.code}; sunsets {HYBRID_SUNSET}",
                  " · ".join(f"{code} moved to {snapshot.spec.param(code, 'data_method', '')} under {snapshot.version} — sampling off, workspace closed for it"
                             for code in moved) or "No measure has moved off hybrid this period.", tone="info")
    codes = _hybrid_measures(snapshot)
    if not codes:
        st.info("No measure uses the hybrid method under the active specification — the abstraction workspace is closed.")
        how_it_works("S13")
        return
    code = st.radio("Measure", codes, horizontal=True, key="s13_measure", index=codes.index("CBP") if "CBP" in codes else 0,
                    format_func=lambda item: MEASURES[item].label)
    denominator = [result for result in snapshot.evaluation.for_measure(code) if result.in_denominator]
    sample = sorted(denominator, key=lambda result: draw(result.person_id, f"sample-{code}"))[:SAMPLE_SIZE]
    statuses = {result.person_id: _packet_status(snapshot, code, result) for result in sample}
    tally = Counter(statuses.values())
    abstracted = sum(tally[name] for name in ("Abstracted · QA pending", "QA agreed", "QA disagreed"))
    reviewed = tally["QA agreed"] + tally["QA disagreed"]
    met = sum(snapshot.evaluation.get(result.person_id, code).met for result in sample)
    for col, (label, value, tone, context) in zip(st.columns(4), (
        ("Sample", f"{len(sample)} of {len(denominator)}", "lav", "Systematic sample; administrative hits need no chart"),
        ("Administrative hits", str(tally["Administrative hit"]), "mint", "Met from standard data before abstraction"),
        ("Abstracted · over-read", f"{abstracted} · {reviewed / abstracted:.0%}" if abstracted else "0", "sky", "Share re-reviewed by QA"),
        ("Hybrid rate (sample)", f"{met / len(sample):.0%}" if sample else "—", "butter", f"{tally['Not found']} record(s) not found"),
    ), strict=True):
        with col:
            render_kpi_card(label, value, tone, context)
    if not sample:
        st.info("No one in this measure's denominator.")
        how_it_works("S13")
        return

    list_col, form_col = st.columns([1, 1.9], gap="medium")
    with list_col, st.container(border=True):
        card_title("Packets", "Sampled persons and where each stands")
        index = st.radio("Packet", list(range(len(sample))), key=f"s13_packet_{code}", label_visibility="collapsed",
                         format_func=lambda i: _name(snapshot, sample[i].person_id),
                         captions=[statuses[result.person_id] for result in sample])
    result = sample[index]
    person = snapshot.person(result.person_id)
    key = f"{code}:{person.person_id}"
    record = snapshot.state.get("abstractions", {}).get(key)
    with form_col, st.container(border=True):
        card_title(f"{person.name} · {code}", f"Packet {key}", badge(statuses[person.person_id], PACKET_TONES.get(statuses[person.person_id], "gray")))
        _chart_packet(person, code)
        status = statuses[person.person_id]
        if status == "Administrative hit":
            st.success(f"Met from standard data: {result.reason}. No chart abstraction needed.")
        elif status == "Not found":
            attempts = snapshot.state.get("record_searches", {}).get(key, [])
            st.warning("Marked not found per hybrid rules. Search attempts: " + "; ".join(attempts))
        else:
            if record and record["status"] != "QA disagreed":
                _qa(snapshot, key, record)
            if not record or record["status"] == "QA disagreed":
                _abstraction_form(snapshot, code, person, key, record)
                _not_found(key)
    how_it_works("S13")


def _chart_packet(person, code):
    lines = []
    if code == "CBP":
        for row in person.vitals:
            day = str(row.get("Observed_Date") or "")[:10]
            if row.get("Systolic_mmHg") not in (None, "") and day >= MP.start.isoformat():
                lines.append((day, f"VITALS {day} · BP {row['Systolic_mmHg']}/{row['Diastolic_mmHg']} · {row.get('Position') or ''} · {row.get('Recorded_By') or ''}"))
    else:
        for row in person.labs:
            day = str(row.get("Result_Date") or "")[:10]
            if "a1c" in str(row.get("Test_Name", "")).lower():
                lines.append((day, f"LAB {day} · {row.get('Test_Name')} {row.get('Result')}{row.get('Unit') or ''} · {row.get('Status')} · {row.get('Performing_Lab') or ''}"))
    lines.sort()
    latest = lines[-1][0] if lines else None
    body = "".join(f"<div>{'<mark>' + escape(text) + '</mark>' if day == latest else escape(text)}</div>" for day, text in lines) or "<div>No candidate pages located.</div>"
    st.markdown(f'<div class="vbc-doc"><div class="doc-head">Chart packet · candidate pages located and highlighted</div>{body}</div>',
                unsafe_allow_html=True)


def _abstraction_form(snapshot, code, person, key, record):
    with st.form(f"s13_form_{key}"):
        st.markdown('<div class="nc-label">Abstraction form</div>', unsafe_allow_html=True)
        dos = st.date_input("Date of service", value=snapshot.as_of - timedelta(days=14), min_value=MP.start, max_value=snapshot.as_of)
        readings = []
        if code == "CBP":
            st.caption("Enter every reading documented on that date. The form applies the specification rule: lowest systolic and lowest diastolic.")
            for slot in range(3):
                left, right = st.columns(2)
                systolic = left.number_input(f"Reading {slot + 1} · systolic", 0, 260, 0, key=f"s13_s{slot}_{key}")
                diastolic = right.number_input(f"Reading {slot + 1} · diastolic", 0, 160, 0, key=f"s13_d{slot}_{key}")
                if systolic and diastolic:
                    readings.append((int(systolic), int(diastolic)))
        else:
            a1c = st.number_input("HbA1c result (%)", 3.0, 20.0, 7.4, step=0.1)
        proof = st.text_input("Proof of service — page reference", placeholder="e.g. Clinic note p.2, signed by Dr. Carter")
        attached = st.checkbox("Proof-of-service page attached to the packet")
        save = st.form_submit_button("Save abstraction", type="primary", disabled=not can_vbc("S13", "Edit"))
    if not save or not guard("S13", "Edit", "save an abstraction"):
        return
    if not proof.strip() or not attached:
        mutate("ABS", "Abstraction save blocked — no proof of service", f"{key}", outcome="Blocked")
        st.error("A value without proof of service cannot be saved or submitted. Attach the page and reference it.")
        return
    if code == "CBP" and not readings:
        st.error("Enter at least one complete reading.")
        return
    stamp = datetime.now()
    fact_id = f"ABS-{code}-{person.person_id}"
    if code == "CBP":
        value = (min(item[0] for item in readings), min(item[1] for item in readings))
        fact = Fact(fact_id, person.person_id, "bp", "LOINC", "85354-9", "Blood pressure (hybrid abstraction)", dos, stamp, "abstraction",
                    f"Abstraction {key}", value=value, unit="mmHg", provider=actor(),
                    extra={"proof": proof.strip(), "readings": readings, "rule": "Lowest systolic and lowest diastolic on the same date"})
        shown = f"{value[0]}/{value[1]} from {len(readings)} reading(s)"
    else:
        fact = Fact(fact_id, person.person_id, "lab", "LOINC", "4548-4", "Hemoglobin A1c (hybrid abstraction)", dos, stamp, "abstraction",
                    f"Abstraction {key}", value=round(float(a1c), 1), unit="%", provider=actor(), extra={"proof": proof.strip()})
        shown = f"{a1c:.1f}%"

    def change(state):
        state["posted_facts"] = [item for item in state["posted_facts"] if item["fact_id"] != fact_id]
        state["evidence_decisions"].pop(fact_id, None)
        post_fact(state, fact)
        state["abstractions"][key] = {"status": "Abstracted · QA pending", "by": actor(), "at": _now_iso(), "dos": dos.isoformat(),
                                      "value": shown, "proof": proof.strip(), "fact_id": fact_id}

    mutate("ABS", "Abstraction saved", f"{key}: {shown} on {dos:%d %b %Y} · proof {proof.strip()}", change, refs={"fact": fact_id},
           toast="Abstraction saved — queued for QA over-read")
    st.rerun()


def _qa(snapshot, key, record):
    render_tiles((("Abstracted value", record["value"]), ("Date of service", record["dos"]), ("Proof", record["proof"]), ("Abstractor", record["by"])))
    if record["status"] != "Abstracted · QA pending":
        st.caption(f"{record['status']} by {record.get('qa_by', '—')}.")
        return
    same_person = record["by"] == actor()
    note = st.text_input("Over-read note", key=f"s13_qa_note_{key}", placeholder="What the over-reader checked")
    if same_person:
        st.caption("Over-read must be done by someone other than the abstractor.")
    with st.container(horizontal=True, gap="small"):
        agree = st.button("QA agree", key=f"s13_agree_{key}", type="primary", disabled=same_person or not can_vbc("S13", "Approve"))
        disagree = st.button("QA disagree", key=f"s13_disagree_{key}", type="secondary",
                             disabled=same_person or not note.strip() or not can_vbc("S13", "Approve"))
    if (agree or disagree) and guard("S13", "Approve", "over-read an abstraction"):
        def change(state):
            state["abstractions"][key].update({"status": "QA agreed" if agree else "QA disagreed", "qa_by": actor(), "qa_at": _now_iso(), "qa_note": note.strip()})
            if disagree:
                state["evidence_decisions"][record["fact_id"]] = {"decision": "rejected", "by": actor(), "at": _now_iso(), "note": f"QA disagreement: {note.strip()}"}
        mutate("ABS", "QA over-read " + ("agreed" if agree else "disagreed"), f"{key}: {note.strip() or record['value']}", change,
               outcome="Agreed" if agree else "Disagreement logged", refs={"fact": record["fact_id"]},
               toast="Over-read recorded" + ("" if agree else " — value withdrawn until re-abstracted"))
        st.rerun()


def _not_found(key):
    with st.expander("Record cannot be found"):
        first = st.text_input("Search attempt 1", key=f"s13_nf1_{key}", placeholder="e.g. Clinic EHR and scanned media searched 21 Oct")
        second = st.text_input("Search attempt 2", key=f"s13_nf2_{key}", placeholder="e.g. Records request to Riverside Hospital 23 Oct")
        if st.button("Mark not found per hybrid rules", key=f"s13_nf_{key}", type="secondary",
                     disabled=not (first.strip() and second.strip()) or not can_vbc("S13", "Edit")) and guard("S13", "Edit", "mark a record not found"):
            def change(state):
                state["record_searches"][key] = [first.strip(), second.strip()]
                state["abstractions"][key] = {"status": "Not found", "by": actor(), "at": _now_iso(), "dos": "—", "value": "—", "proof": "—", "fact_id": ""}
            mutate("ABS", "Record not found", f"{key}: {first.strip()}; {second.strip()}", change, toast="Not found — both searches documented")
            st.rerun()
