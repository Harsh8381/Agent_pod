"""Shared building blocks for the value-based care screens (S01–S16)."""

from __future__ import annotations

import json
from datetime import date, datetime
from html import escape
from pathlib import Path

import streamlit as st

from frontend.admin_service import VBC_SCREENS
from frontend.settings import PROJECT_ROOT
from frontend.ui.components import badge, page_header
from frontend.ui.data import fetch_ehr_export
from frontend.ui.state import can, current_user, go_to
from frontend.vbc import store as vbc_store
from frontend.vbc.catalog import GAP_STATUSES, MEASURES, MP, VALUE_SET_SOURCE
from frontend.vbc.population import Person
from frontend.vbc.service import Snapshot, build_snapshot, fact_to_dict, seed_state

STATE_PATH = PROJECT_ROOT / "data" / "vbc_state.json"
EHR_STORE_FILE = PROJECT_ROOT / "Epic_Inspired_USA_50_Patient_Demo_Athena_Care_Gap.json"

SCREENS = {
    "S01": ("Command", "One honest view of where quality stands, and what it will take to move a star",
            "Quality leadership · Stars and contract analysts · HEDIS lead"),
    "S02": ("Command", "Open any HEDIS measure and see exactly who counts, who is excluded and why",
            "HEDIS and quality analysts · HEDIS lead · Clinical SMEs"),
    "S03": ("Work the gaps", "One ranked queue for every open, unknown and pending gap, with the reason in plain words",
            "Care managers and coordinators · Team leads · HEDIS analysts"),
    "S04": ("Work the gaps", "Close gaps as a by-product of the visit, without adding clicks for the clinician",
            "Physicians and APPs · Medical assistants · Coders (CPT II confirmation)"),
    "S05": ("Work the gaps", "Everything about one person's quality picture on a single page, with the history behind it",
            "Care managers · Physicians · HEDIS analysts"),
    "S06": ("Prove it", "Eight checks stand between a result and a closed gap, and every one is visible",
            "HEDIS and quality analysts · Lab interface owners · Auditors (read-only)"),
    "S07": ("Prove it", "Stop chasing people who should never have been on the list, with a human confirming every call",
            "Clinicians · HEDIS analysts · CDI reviewers"),
    "S08": ("Prove it", "Make sure payers can see the care that has already been given, before the deadline",
            "Supplemental data owner · HEDIS analysts · HEDIS lead"),
    "S09": ("Prove it", "When the payer's list and ours disagree, show exactly why, person by person",
            "HEDIS analysts · Supplemental data owner · Contracting"),
    "S10": ("Care transitions", "Every admission, discharge and ED visit becomes a coordinated set of follow-ups",
            "Transitions nurses · Care managers · Pharmacists"),
    "S11": ("Work the gaps", "Reach the right people, in their language and channel, without ever crossing a line",
            "Outreach coordinators · Care managers · Compliance (policy events)"),
    "S12": ("Command", "HEDIS changes every year; the agent changes with it, deliberately and reversibly",
            "HEDIS lead · Quality governance · Engineering"),
    "S13": ("Prove it", "While chart review still exists, make it fast, consistent and fully evidenced",
            "Medical record abstractors · QA over-readers · HEDIS analysts"),
    "S14": ("Value and trust", "Learn from what patients tell the surveys, without ever touching the surveys themselves",
            "Patient experience lead · Operations leaders · Compliance"),
    "S15": ("Value and trust", "Quality, utilization and cost on the same page, for the same people and period",
            "Finance and contracting · Executives · Pathway owners"),
    "S16": ("Value and trust", "Every number can be explained, every action traced, every guardrail proven",
            "Auditors (read-only) · Compliance · HEDIS lead"),
}
SCREEN_PAGES = {
    "S01": "vbc_stars", "S02": "vbc_measures", "S03": "vbc_gaps", "S04": "vbc_poc", "S05": "vbc_person", "S06": "vbc_evidence",
    "S07": "vbc_exclusions", "S08": "vbc_supplemental", "S09": "vbc_payer", "S10": "vbc_transitions", "S11": "vbc_outreach",
    "S12": "vbc_versions", "S13": "vbc_abstraction", "S14": "vbc_surveys", "S15": "vbc_value", "S16": "vbc_governance",
}
SCREEN_TITLES = {
    "S01": "Stars dashboard", "S02": "Measure explorer", "S03": "Gap workbench", "S04": "Point-of-care card", "S05": "Person profile",
    "S06": "Evidence panel", "S07": "Exclusion review", "S08": "Supplemental data", "S09": "Payer reconciliation",
    "S10": "Transitions board", "S11": "Outreach console", "S12": "Version center", "S13": "Abstraction",
    "S14": "Survey insights", "S15": "Utilization & value", "S16": "Governance & audit",
}


# ---------------------------------------------------------------------------
# Snapshot and state
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def _file_export(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_export() -> tuple[dict, str]:
    export = fetch_ehr_export()
    if export is not None:
        return export, "EHR backend · live"
    if EHR_STORE_FILE.is_file():
        return _file_export(str(EHR_STORE_FILE), EHR_STORE_FILE.stat().st_mtime), "EHR file · backend offline (read-only)"
    return {"sheets": {}}, "No EHR data"


def get_snapshot() -> Snapshot:
    """Build this run's snapshot; seed operational history once on a fresh install."""
    for key, value in (("vbc_person", None), ("vbc_measure", "KED")):
        st.session_state.setdefault(key, value)
    export, source = load_export()
    state = vbc_store.load_state(STATE_PATH)
    now = datetime.now()
    snapshot = build_snapshot(export, state, now, source)
    if not state.get("seeded") and snapshot.people:
        seed_state(state, snapshot)
        vbc_store.save_state(STATE_PATH, state)
        snapshot = build_snapshot(export, state, now, source)
    return snapshot


def actor() -> str:
    user = current_user()
    return f"{user['name']} ({user['role']})" if user.get("role") else user["name"]


def can_vbc(screen: str, action: str = "Edit") -> bool:
    return can(VBC_SCREENS[screen], action)


def mutate(family: str, action: str, detail: str, change=None, outcome: str = "Recorded", refs: dict | None = None,
           toast: str | None = None) -> dict:
    """Apply a change to the latest persisted state, append the audit event, save."""
    state = vbc_store.load_state(STATE_PATH)
    if change:
        change(state)
    event = vbc_store.audit(state, family, actor(), action, detail, outcome, refs)
    vbc_store.save_state(STATE_PATH, state)
    if toast:
        st.toast(toast)
    return event


def guard(screen: str, action: str, what: str) -> bool:
    """Defense in depth: every write re-checks permission; a denial is logged as an RBAC event."""
    if can_vbc(screen, action):
        return True
    mutate("RBAC", f"{current_user()['role'] or 'User'} tried to {what}", f"Requires {VBC_SCREENS[screen]} · {action}", outcome="Blocked")
    st.error(f"Your role cannot {what}. The attempt was logged for compliance.")
    return False


def post_fact(state: dict, fact) -> None:
    state.setdefault("posted_facts", []).append(fact_to_dict(fact))


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------


def open_person(person_id: str, page: str = "vbc_person") -> None:
    st.session_state.vbc_person = person_id
    go_to(page)


def open_measure(code: str) -> None:
    st.session_state.vbc_measure = code
    go_to("vbc_measures")


def open_evidence(person_id: str, code: str) -> None:
    st.session_state.vbc_person = person_id
    st.session_state.vbc_measure = code
    go_to("vbc_evidence")


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def header(screen: str, snapshot: Snapshot, title: str | None = None, action_weight: float = 3.2):
    job, tagline, used_by = SCREENS[screen]
    actions = page_header(f"{screen} · {job}", title or SCREEN_TITLES[screen], tagline, action_weight=action_weight)
    st.markdown(
        '<div class="vbc-context">'
        + badge(MP.label, "violet")
        + badge(f"Version {snapshot.version}", "blue")
        + badge(VALUE_SET_SOURCE, "amber")
        + badge(snapshot.export_source, "gray" if "live" in snapshot.export_source else "amber")
        + badge(f"As of {snapshot.as_of:%d %b %Y}", "gray")
        + f'<span class="nc-meta" style="margin-left:.4rem">Used by: {escape(used_by)}</span></div>',
        unsafe_allow_html=True,
    )
    return actions


def status_badge(code: str) -> str:
    status = GAP_STATUSES.get(code)
    return badge(code, status.tone if status else "gray")


def status_legend() -> str:
    tones = {"red": "#F3B4C2", "amber": "#EFD98C", "blue": "#B3D0F5", "green": "#A9E0CB", "gray": "#D9DCE6", "violet": "#CCC3F8"}
    items = "".join(
        f'<span style="--legend:{tones[status.tone]}" title="{escape(status.meaning)}">{escape(status.code)}</span>'
        for status in GAP_STATUSES.values()
    )
    return f'<div class="vbc-legend">{items}</div>'


def bars(rows: list[tuple]) -> None:
    """rows: (label, sub, value, maximum, display, tone). Magnitude uses one hue; status tones are explicit."""
    parts = []
    for label, sub, value, maximum, display, tone in rows:
        width = 0 if not maximum else max(0.0, min(100.0, value / maximum * 100))
        parts.append(
            f'<div class="lbl">{escape(label)}' + (f"<small>{escape(sub)}</small>" if sub else "") + "</div>"
            f'<div class="vbc-track" title="{escape(label)}: {escape(display)}"><div class="vbc-fill tone-{tone}" style="width:{width:.1f}%"></div></div>'
            f'<div class="val">{display}</div>'
        )
    st.markdown(f'<div class="vbc-bars">{"".join(parts)}</div>', unsafe_allow_html=True)


def callout(kind: str, title: str, text: str) -> None:
    st.markdown(f'<div class="vbc-callout {kind}"><div><b>{escape(title)}</b>{escape(text)}</div></div>', unsafe_allow_html=True)


def guardrail(text: str) -> None:
    callout("guardrail", "Guardrail", text)


def handoff(screens: tuple) -> None:
    st.markdown('<div class="nc-label" style="margin:.4rem 0 .2rem">Hands off to</div>', unsafe_allow_html=True)
    with st.container(horizontal=True, gap="small"):
        for code in screens:
            page = SCREEN_PAGES[code]
            st.button(f"{code} {SCREEN_TITLES[code]} →", key=f"handoff_{code}_{st.session_state.page}", type="tertiary",
                      on_click=go_to, args=(page,), disabled=not can_vbc(code, "View"))


def person_banner(person: Person, extra: str = "") -> None:
    trust = [
        badge("Identity: confident", "green") if person.identity == "confident" else badge("Identity: under review", "red"),
        badge("Coverage: continuous", "green") if person.coverage == "continuous" else badge("Coverage: gap", "amber"),
        badge(f"Prefers {person.preferences.get('channel', 'phone')}, {person.preferences.get('time', 'AM')}", "blue"),
        badge(f"Language: {person.language}", "gray"),
    ] + [badge(f"Barrier: {barrier}", "amber") for barrier in person.barriers]
    since = f" since {person.attributed_since:%Y}" if person.attributed_since else ""
    st.markdown(
        '<div class="vbc-person">'
        f'<div class="nc-initials lg">{escape(person.initials)}</div>'
        f'<div class="who"><b>{escape(person.name)} · {person.age if person.age is not None else "?"} {escape(person.sex[:1])}</b>'
        f"<small>{escape(person.person_key)} · {escape(person.product_line)} · {escape(person.payer_name)} · attributed to "
        f"{escape(person.pcp)}{since}{' · attribution ended ' + person.attribution_ended.strftime('%d %b') if person.attribution_ended else ''}</small></div>"
        f'<div class="vbc-grow"></div><div class="nc-badge-row">{"".join(trust)}{extra}</div></div>',
        unsafe_allow_html=True,
    )


def person_options(snapshot: Snapshot, people=None) -> dict:
    people = people if people is not None else snapshot.people
    return {person.person_id: f"{person.name} · {person.age} {person.sex[:1]} · {person.person_id}" for person in people}


def pick_person(snapshot: Snapshot, key: str, people=None, label: str = "Person") -> Person | None:
    options = person_options(snapshot, people)
    if not options:
        return None
    ids = list(options)
    current = st.session_state.get("vbc_person")
    index = ids.index(current) if current in ids else 0
    chosen = st.selectbox(label, ids, index=index, format_func=options.get, key=f"{key}_{current}")
    if chosen != current:
        st.session_state.vbc_person = chosen
    return snapshot.person(chosen)


def default_person(snapshot: Snapshot) -> None:
    """Open on the highest-priority person when nobody has been selected yet."""
    if st.session_state.get("vbc_person") in snapshot.people_by_id:
        return
    ranked = snapshot.ranked()
    if ranked:
        st.session_state.vbc_person = ranked[0].person_id


def pick_measure(key: str, codes: tuple, label: str = "Measure") -> str:
    current = st.session_state.get("vbc_measure")
    codes = tuple(codes)
    index = codes.index(current) if current in codes else 0
    chosen = st.selectbox(label, codes, index=index, format_func=lambda code: MEASURES[code].label, key=f"{key}_{current}")
    if chosen != current:
        st.session_state.vbc_measure = chosen
    return chosen


def fmt(day) -> str:
    if isinstance(day, datetime):
        return day.strftime("%d %b %Y %H:%M")
    if isinstance(day, date):
        return day.strftime("%d %b %Y")
    return str(day or "—")


def pct(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


def how_it_works(screen: str) -> None:
    """The screen's playbook: trigger, flow, happy path, exception paths, guardrail and hand-offs."""
    from frontend.ui.components import render_html_table
    from frontend.vbc.playbook import PLAYBOOK

    book = PLAYBOOK[screen]
    guardrail(book["guardrail"])
    handoff(book["handoff"])
    with st.expander("How this screen works — trigger, process flow and exception paths"):
        st.markdown(f'<div class="nc-meta" style="margin-bottom:.5rem"><b>Trigger</b> · {escape(book["trigger"])}</div>',
                    unsafe_allow_html=True)
        flow_col, paths_col = st.columns([1, 1.15], gap="medium")
        with flow_col:
            render_html_table(
                ["#", "Step", "Accountable"],
                [[str(index), f"<b>{escape(step)}</b><div class='nc-meta'>{escape(text)}</div>", badge(who, "gray")]
                 for index, (step, text, who) in enumerate(book["flow"], start=1)],
            )
        with paths_col:
            code, story = book["happy"]
            callout("story", f"Happy path · {code}", story)
            render_html_table(
                ["Code", "What goes wrong", "What the agent does", "Owner"],
                [[f"<code>{escape(code)}</code>", escape(wrong), escape(does), escape(owner)] for code, wrong, does, owner in book["unhappy"]],
            )
