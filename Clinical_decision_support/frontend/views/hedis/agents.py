"""AI agent console: L1 autonomous activity, the L2 approval queue, L3 escalations, kill switches."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from app.hedis import agents
from frontend.ui.components import card_title, render_html_table, render_kpi_card
from frontend.ui.shell import render_page
from frontend.views.hedis.common import (
    actor,
    allowed,
    code,
    flag_disabled_notice,
    guard,
    header,
    principal,
    run,
    status_badge,
    tier_badge,
)

MODULE = "AI Agent Console"


def show_hedis_agents():
    render_page("hedis_agents")
    if flag_disabled_notice():
        return
    actions = header("AI agent console", "L1 acts on reversible work · L2 proposes and a person approves · L3 escalates to an expert and never acts")
    if actions.button("Run all tiers", key="hag_run_all", type="primary", icon=":material/play_arrow:", disabled=not allowed(MODULE, "Edit")):
        if guard(MODULE, "Edit", "run agents"):
            for tier in ("L1", "L2", "L3"):
                run(agents.run_tier, tier, actor())
            st.toast("Agents ran under their tier policy")
            st.rerun()
    view = agents.view()
    _tiers(view)
    queue = [p for p in view["proposals"] if p["status"] == "PROPOSED"]
    escalations = [p for p in view["proposals"] if p["status"] in ("ESCALATED", "ACKNOWLEDGED")]
    tabs = st.tabs([f"Approval queue · L2 ({len(queue)})", f"Escalations · L3 ({len(escalations)})", "Autonomous activity · L1",
                    "Agents", "Worklist & tasks"])
    with tabs[0]:
        _queue(queue)
    with tabs[1]:
        _escalations(escalations)
    with tabs[2]:
        _activity(view)
    with tabs[3]:
        _catalogue(view)
    with tabs[4]:
        _worklist(view)


def _tiers(view: dict) -> None:
    cols = st.columns(3)
    stats = view["stats"]
    counts = {"L1": sum(stats.get(s, 0) for s in ("AUTO_EXECUTED", "REVERTED")), "L2": stats.get("PROPOSED", 0),
              "L3": stats.get("ESCALATED", 0) + stats.get("ACKNOWLEDGED", 0)}
    context = {"L1": "actions executed", "L2": "awaiting a decision", "L3": "open escalations"}
    for col, (tier, info) in zip(cols, view["tiers"].items(), strict=True):
        with col:
            render_kpi_card(f"{tier} · {info['label']}", str(counts[tier]), {"L1": "mint", "L2": "butter", "L3": "rose"}[tier],
                            f"{context[tier]} · {'on' if info['enabled'] else 'SWITCHED OFF'}")
            st.caption(info["summary"])
            enabled = st.toggle("Enabled", value=info["enabled"], key=f"hag_switch_{tier}", disabled=not allowed(MODULE, "Admin"),
                                help="Kill switch (Admin). Switching a tier off stops its agents immediately.")
            if enabled != info["enabled"] and guard(MODULE, "Admin", "switch an agent tier"):
                run(agents.set_tier, tier, enabled, actor(), "Console kill switch", success=f"{tier} {'enabled' if enabled else 'switched off'}")
                st.rerun()


def _permitted(module: str, action: str) -> bool:
    return allowed(module, action)


def _queue(queue: list[dict]) -> None:
    if not queue:
        st.success("Nothing is waiting for approval. Run the L2 agents to refresh proposals.")
        return
    for proposal in queue[:40]:
        with st.container(border=True):
            module, action = proposal["required_permission"]
            card_title(proposal["title"], f"{proposal['agent_name']} · {proposal['action_label']} · confidence {proposal['confidence']:.0%} · "
                       f"approval needs {action} on {module}", trailing=tier_badge(proposal["tier"]))
            st.markdown(f'<div class="nc-rec-body">{escape(proposal["narrative"])}</div>', unsafe_allow_html=True)
            c1, c2, c3 = st.columns([1, 1, 3], vertical_alignment="bottom")
            note = c3.text_input("Note", key=f"hag_note_{proposal['proposal_id']}", placeholder="Optional reviewer note",
                                 label_visibility="collapsed")
            can_decide = allowed(MODULE, "Approve") and _permitted(module, action)
            if c1.button("Approve", key=f"hag_ok_{proposal['proposal_id']}", type="primary", disabled=not can_decide):
                if guard(MODULE, "Approve", "approve an agent proposal") and run(agents.decide, proposal["proposal_id"], actor(), "approve",
                                                                                  _permitted, note, success="Approved and executed under your name"):
                    st.rerun()
            if c2.button("Reject", key=f"hag_no_{proposal['proposal_id']}", disabled=not allowed(MODULE, "Approve")):
                if guard(MODULE, "Approve", "reject an agent proposal") and run(agents.decide, proposal["proposal_id"], actor(), "reject",
                                                                                 _permitted, note, success="Rejected"):
                    st.rerun()
    if len(queue) > 40:
        st.caption(f"Showing 40 of {len(queue)} proposals.")


def _escalations(items: list[dict]) -> None:
    if not items:
        st.success("No open escalations.")
        return
    role = principal().role
    for item in items:
        with st.container(border=True):
            card_title(item["title"], f"{item['agent_name']} · routed to {', '.join(item['escalate_to'])} · severity {item['severity']}",
                       trailing=status_badge(item["status"]))
            st.markdown(f'<div class="nc-rec-body">{escape(item["narrative"])}</div>', unsafe_allow_html=True)
            if item["evidence"]:
                st.caption("Evidence: " + ", ".join(item["evidence"]))
            mine = role in item["escalate_to"] or role in ("HEDIS Lead", "Super Admin")
            with st.form(f"hag_esc_{item['proposal_id']}"):
                c1, c2 = st.columns([1, 3])
                outcome = c1.selectbox("Outcome", ["RESOLVED", "ACKNOWLEDGED", "NOT_AN_ISSUE"], key=f"hag_out_{item['proposal_id']}")
                note = c2.text_input("What was decided", key=f"hag_dec_{item['proposal_id']}")
                if st.form_submit_button("Record decision", disabled=not mine or not allowed(MODULE, "Approve")):
                    if guard(MODULE, "Approve", "resolve an escalation") and run(agents.resolve_escalation, item["proposal_id"], actor(), role,
                                                                                  outcome, note, success="Decision recorded"):
                        st.rerun()
            if not mine:
                st.caption(f"Only {', '.join(item['escalate_to'])} (or the HEDIS lead) can record the decision.")


def _activity(view: dict) -> None:
    done = [p for p in view["proposals"] if p["tier"] == "L1"]
    if not done:
        st.info("L1 agents have not acted yet.")
        return
    render_html_table(["Proposal", "Agent", "Action", "What", "Status", "Result"],
                      [[code(p["proposal_id"]), escape(p["agent_name"]), escape(p["action_label"]), escape(p["title"]), status_badge(p["status"]),
                        escape(p["error"] or ", ".join(f"{k} {v}" for k, v in (p["result"] or {}).items() if isinstance(v, (str, int)))[:90])]
                       for p in done[:60]])
    revertible = [p for p in done if p["status"] == "AUTO_EXECUTED"]
    if revertible:
        labels = {p["proposal_id"]: f"{p['proposal_id']} · {p['title']}" for p in revertible}
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        chosen = c1.selectbox("Undo an autonomous action", list(labels), format_func=labels.get, key="hag_revert_pick")
        if c2.button("Revert", key="hag_revert", disabled=not allowed(MODULE, "Edit")) and guard(MODULE, "Edit", "revert an agent action"):
            if run(agents.revert, chosen, actor(), success="Reverted"):
                st.rerun()


def _catalogue(view: dict) -> None:
    for agent in view["agents"]:
        with st.container(border=True):
            last = agent["last_run"]
            card_title(agent["name"], agent["purpose"], trailing=tier_badge(agent["tier"]))
            c1, c2, c3 = st.columns([2, 2, 1], vertical_alignment="center")
            c1.caption("Reads: " + ", ".join(agent["reads"]))
            c2.caption("May: " + (", ".join(agent["actions"]) or f"escalate to {', '.join(agent['escalates_to'])}"))
            if c3.button("Run", key=f"hag_run_{agent['key']}", disabled=not allowed(MODULE, "Edit")) and guard(MODULE, "Edit", "run an agent"):
                result = run(agents.run_agent, agent["key"], actor())
                if result:
                    st.toast(f"{agent['name']}: {result['findings']} finding(s), {result['executed']} executed, {result['proposed']} proposed, "
                             f"{result['escalated']} escalated, {result['skipped']} already known")
                    st.rerun()
            if last:
                st.caption(f"Last run {last['run_id']} by {last['triggered_by']} at {last['at'].replace('T', ' ')}: {last['findings']} finding(s), "
                           f"{last['skipped']} already known")


def _worklist(view: dict) -> None:
    c1, c2 = st.columns(2, gap="medium")
    with c1, st.container(border=True):
        card_title("Prioritized gap worklist", "Set by the Gap prioritizer (L1): measure weight first, then how quickly a gap can close")
        if view["priorities"]:
            st.dataframe(pd.DataFrame([{"Member": i["member_id"], "Measure": i["measure_id"], "Payer": i["payer"], "Score": i["score"],
                                        "Why": i["why"]} for i in view["priorities"][:25]]), hide_index=True, width="stretch")
        else:
            st.caption("Run the L1 agents to build the worklist.")
    with c2, st.container(border=True):
        card_title("Data-quality tasks", "Opened by the Registry quality sentinel (L1)")
        if view["dq_tasks"]:
            st.dataframe(pd.DataFrame([{"Check": t["code"], "Registry key": t["registry_key"], "Detail": t["detail"], "Status": t["status"]}
                                       for t in view["dq_tasks"]]), hide_index=True, width="stretch")
        else:
            st.caption("No open data-quality tasks.")
