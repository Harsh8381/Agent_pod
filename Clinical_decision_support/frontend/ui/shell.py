"""App chrome: top bar, workspace switch, sidebar navigation, user menu, alerts and help.

Two workspaces share one shell: the clinical decision platform (consultations → coding →
billing) and value-based care (HEDIS and Stars, screens S01–S16 grouped by the five jobs).
"""

from datetime import datetime
from html import escape

import streamlit as st

from frontend import access_control as ac
from frontend.ui.components import initials
from frontend.ui.state import (
    ACCESS,
    can_open,
    current_user,
    go_to,
    open_record,
    persist_access,
    sign_out,
)

# Navigation per workspace: (section, [(page key, label, icon)]).
CLINICAL_NAV = (
    (
        "Clinical decision platform",
        (
            ("dashboard", "Clinical Overview", ":material/dashboard:"),
            ("new_consult", "Consultations", ":material/assignment:"),
            ("review", "Clinical Review & CDS", ":material/verified_user:"),
            ("medications", "Medication & RTBC", ":material/medication:"),
            ("prior_auth", "Prior Authorization", ":material/task_alt:"),
            ("codes", "Coding & HCC", ":material/code:"),
            ("billing", "Billing & RCM", ":material/receipt_long:"),
            ("knowledge_base", "Knowledge Base", ":material/menu_book:"),
        ),
    ),
    (
        "RCM tools",
        (
            ("ipd_billing", "IPD Billing", ":material/local_hospital:"),
            ("claim_generation", "Claim Generation", ":material/request_quote:"),
        ),
    ),
    (
        "Administration",
        (
            ("ehr_data", "EHR Records", ":material/database:"),
            ("admin_dashboard", "Oversight", ":material/monitoring:"),
            ("super_admin", "User Access", ":material/admin_panel_settings:"),
        ),
    ),
)
VBC_NAV = (
    (
        "Command",
        (
            ("vbc_stars", "Stars dashboard", ":material/star_half:"),
            ("vbc_measures", "Measure explorer", ":material/query_stats:"),
            ("vbc_versions", "Version center", ":material/history:"),
            ("vbc_playbook", "Operating playbook", ":material/menu_book:"),
        ),
    ),
    (
        "Work the gaps",
        (
            ("vbc_gaps", "Gap workbench", ":material/checklist:"),
            ("vbc_poc", "Point-of-care card", ":material/stethoscope:"),
            ("vbc_person", "Person profile", ":material/person:"),
            ("vbc_outreach", "Outreach console", ":material/campaign:"),
        ),
    ),
    (
        "Prove it",
        (
            ("vbc_evidence", "Evidence panel", ":material/fact_check:"),
            ("vbc_exclusions", "Exclusion review", ":material/rule:"),
            ("vbc_supplemental", "Supplemental data", ":material/upload_file:"),
            ("vbc_payer", "Payer reconciliation", ":material/compare_arrows:"),
            ("vbc_abstraction", "Abstraction", ":material/find_in_page:"),
        ),
    ),
    ("Care transitions", (("vbc_transitions", "Transitions board", ":material/swap_horiz:"),)),
    (
        "Quality loop (POC)",
        (
            ("vbcpoc_dashboard", "Population dashboard", ":material/insights:"),
            ("vbcpoc_patient", "Patient 360", ":material/account_circle:"),
            ("vbcpoc_gaps", "Care-gap worklist", ":material/assignment_turned_in:"),
            ("vbcpoc_governance", "Audit & demo", ":material/verified:"),
        ),
    ),
    (
        "Value and trust",
        (
            ("vbc_surveys", "Survey insights", ":material/forum:"),
            ("vbc_value", "Utilization & value", ":material/payments:"),
            ("vbc_governance", "Governance & audit", ":material/gavel:"),
        ),
    ),
)
WORKSPACES = {
    "clinical": {"label": "Clinical", "title": "Clinical Decision Platform", "nav": CLINICAL_NAV},
    "vbc": {"label": "Value-based care", "title": "Value-Based Care Agent", "nav": VBC_NAV},
}
PAGE_LABELS = {key: label for ws in WORKSPACES.values() for _, items in ws["nav"] for key, label, _ in items}
PAGE_WORKSPACE = {key: name for name, ws in WORKSPACES.items() for _, items in ws["nav"] for key, _, _ in items}

TOP_NAV = {
    "clinical": (
        ("dashboard", "Overview"),
        ("new_consult", "Consultations"),
        ("review", "CDS Workbench"),
        ("codes", "Coding & Billing"),
        ("prior_auth", "Prior Auth"),
        ("admin_dashboard", "Oversight"),
    ),
    "vbc": (
        ("vbc_stars", "Command"),
        ("vbc_gaps", "Work the gaps"),
        ("vbc_evidence", "Prove it"),
        ("vbc_transitions", "Care transitions"),
        ("vbc_value", "Value & trust"),
        ("vbcpoc_dashboard", "Quality loop"),
    ),
}
# Which top-bar tab is highlighted for each page.
TOP_NAV_GROUP = {
    "dashboard": "dashboard",
    "new_consult": "new_consult",
    "review": "review",
    "medications": "review",
    "knowledge_base": "review",
    "codes": "codes",
    "billing": "codes",
    "ipd_billing": "codes",
    "claim_generation": "codes",
    "prior_auth": "prior_auth",
    "admin_dashboard": "admin_dashboard",
    "super_admin": "admin_dashboard",
    "ehr_data": "admin_dashboard",
    "vbc_stars": "vbc_stars", "vbc_measures": "vbc_stars", "vbc_versions": "vbc_stars", "vbc_playbook": "vbc_stars",
    "vbc_gaps": "vbc_gaps", "vbc_poc": "vbc_gaps", "vbc_person": "vbc_gaps", "vbc_outreach": "vbc_gaps",
    "vbc_evidence": "vbc_evidence", "vbc_exclusions": "vbc_evidence", "vbc_supplemental": "vbc_evidence",
    "vbc_payer": "vbc_evidence", "vbc_abstraction": "vbc_evidence",
    "vbc_transitions": "vbc_transitions",
    "vbc_surveys": "vbc_value", "vbc_value": "vbc_value", "vbc_governance": "vbc_value",
    "vbcpoc_dashboard": "vbcpoc_dashboard", "vbcpoc_patient": "vbcpoc_dashboard", "vbcpoc_gaps": "vbcpoc_dashboard",
    "vbcpoc_governance": "vbcpoc_dashboard",
}
WORKFLOW_HELP = {
    "clinical": (
        "How a consultation flows",
        (
            ("Consultations", "Pick or register the patient, then record, upload or paste the conversation."),
            ("Clinical Review & CDS", "Review guideline-backed recommendations, medication safety and care gaps."),
            ("SOAP Note & Sign", "Edit the draft note and sign it; signed notes are locked."),
            ("Coding & HCC", "Accept or reject ICD-10/CPT suggestions; pre- and post-coding checks guide you."),
            ("Billing & RCM", "Only performed and documented services are billed; send the claim draft to RCM."),
        ),
    ),
    "vbc": (
        "How a gap closes, end to end",
        (
            ("Stars dashboard", "See which measure is short of its next estimated cut point."),
            ("Measure explorer", "Find the component that fails — e.g. uACR, not eGFR."),
            ("Gap workbench", "Work the ranked queue; visits route to the point-of-care card."),
            ("Point-of-care card", "The clinician orders, declines with a reason, or records a preference."),
            ("Evidence panel", "Results pass eight checks before a gap closes."),
            ("Supplemental data", "Closure evidence goes to the payer in its own format."),
            ("Payer reconciliation", "The next payer list agrees; differences have causes."),
            ("Governance & audit", "Every step is in the lineage, ready for the auditor."),
        ),
    ),
}


def workspace_of(page: str) -> str:
    return PAGE_WORKSPACE.get(page, "clinical")


def workspace_home(workspace: str) -> str | None:
    return next((key for _, items in WORKSPACES[workspace]["nav"] for key, _, _ in items if can_open(key)), None)


def render_page(active_page):
    """Draw the app chrome for a page: top bar plus sidebar."""
    workspace = workspace_of(active_page)
    _render_topbar(active_page, workspace)
    _render_sidebar(active_page, workspace)


def _render_topbar(active_page, workspace):
    group = TOP_NAV_GROUP.get(active_page)
    pills = "".join(
        f'<span class="nc-app-pill{" on" if name == workspace else ""}">{escape(ws["title"])}</span>' for name, ws in WORKSPACES.items()
    )
    # One flex row sized to content: brand left, navigation centred, tools right.
    with st.container(
        key="nc_topbar", horizontal=True, horizontal_alignment="distribute", vertical_alignment="center", gap="small"
    ):
        st.markdown(
            '<div class="nc-brand"><div class="nc-brand-mark"></div><div>'
            '<div class="nc-brand-name">NuuCare</div><div class="nc-brand-sub">POWERED BY DATA COSMOS</div></div>'
            f'<div class="nc-app-pills">{pills}</div></div>',
            unsafe_allow_html=True,
            width="content",
        )
        with st.container(horizontal=True, width="content", gap="small", vertical_alignment="center", key="nc_topnav"):
            for page_key, label in TOP_NAV[workspace]:
                if can_open(page_key):
                    state = "on" if group == page_key else "off"
                    st.button(label, key=f"topnav_{state}_{page_key}", on_click=go_to, args=(page_key,), width="content")
        with st.container(horizontal=True, width="content", vertical_alignment="center", gap="small", key="nc_user_menu"):
            with st.container(width="content", key="nc_tb_alerts"):
                _render_alerts(workspace)
            with st.container(width="content", key="nc_tb_help"):
                _render_help(workspace)
            with st.container(width="content", key="nc_tb_avatar"):
                _render_user_menu()


def _render_alerts(workspace):
    pending = [record for record in st.session_state.records if record.get("status") == "Pending"]
    stale = []
    if workspace == "vbc":
        from frontend.vbc.feeds import feed_registry

        now = datetime.now()
        stale = [feed for feed in feed_registry(now) if (now - feed["last"]).total_seconds() / 3600 > feed["expected_hours"]]
    count = len(pending) + len(stale)
    with st.popover(f"Alerts · {count}" if count else "Alerts", icon=":material/notifications:"):
        if stale:
            st.markdown("**Feed health**")
            for feed in stale:
                hours = int((datetime.now() - feed["last"]).total_seconds() // 3600)
                age = f"{hours // 24} days" if hours >= 48 else f"{hours} hours"
                st.markdown(f'{escape(feed["feed"])}  \n<span class="nc-meta">Silent for {age} · dependent gaps show as UNKNOWN</span>',
                            unsafe_allow_html=True)
            st.button("Open governance & audit", key="alert_open_gov", on_click=go_to, args=("vbc_governance",),
                      disabled=not can_open("vbc_governance"))
        st.markdown("**Notes awaiting sign-off**")
        if not pending:
            st.caption("Nothing waiting — every consultation note is signed.")
            return
        me = current_user()["username"]
        mine = [record for record in pending if record.get("assigned_to") == me]
        st.caption(f"{len(pending)} draft note(s) · {len(mine)} assigned to you")
        for record in (mine or pending)[:6]:
            text_col, open_col = st.columns([3, 1], vertical_alignment="center")
            text_col.markdown(
                f'{escape(record.get("name", "Patient"))}  \n<span class="nc-meta">{escape(record.get("id", ""))} · {escape(record.get("date", ""))}</span>',
                unsafe_allow_html=True,
            )
            open_col.button("Open", key=f"alert_open_{record.get('id')}", on_click=open_record, args=(record,),
                            disabled=not can_open("review"))


def _render_help(workspace):
    title, steps = WORKFLOW_HELP[workspace]
    with st.popover("Help", icon=":material/help:"):
        st.markdown(f"**{title}**")
        for index, (step, text) in enumerate(steps, start=1):
            st.markdown(f"{index}. **{step}** — {text}")
        st.caption("Your role decides which pages and actions are available. Ask a Super Admin to change access.")


def _render_user_menu():
    user = current_user()
    with st.popover(initials(user["name"]), help=f"{user['name']} · {user['role']}"):
        st.markdown(f"**{escape(user['name'])}**  \n{escape(user['role'])} · @{escape(user['username'])}")
        with st.form("own_password_form", clear_on_submit=True):
            current = st.text_input("Current password", type="password")
            new_password = st.text_input("New password", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("Change password"):
                stored = ac.get_user(ACCESS["store"], user["username"]) or {}
                if not ac.verify_password(current, stored.get("password_hash", "")):
                    st.error("Current password is incorrect.")
                elif new_password != confirm:
                    st.error("New passwords do not match.")
                else:
                    try:
                        ac.set_password(ACCESS["store"], user["username"], new_password, actor=user["username"])
                        persist_access()
                        st.success("Password changed.")
                    except ValueError as error:
                        st.error(str(error))
        st.button("Sign out", key="menu_sign_out", icon=":material/logout:", on_click=sign_out, width="stretch")


def _render_workspace_switch(workspace):
    homes = {name: workspace_home(name) for name in WORKSPACES}
    available = [name for name, home in homes.items() if home]
    if len(available) < 2:
        return
    with st.container(horizontal=True, gap="small", key="nc_ws_switch"):
        for name in available:
            state = "on" if name == workspace else "off"
            st.button(WORKSPACES[name]["label"], key=f"ws_{state}_{name}", on_click=go_to, args=(homes[name],), width="stretch")


def _render_sidebar(active_page, workspace):
    with st.sidebar:
        _render_workspace_switch(workspace)
        for index, (section, items) in enumerate(WORKSPACES[workspace]["nav"]):
            visible = [item for item in items if can_open(item[0])]
            if not visible:
                continue
            css = "shell-section-label title" if index == 0 else "shell-section-label"
            st.markdown(f'<div class="{css}">{escape(section.upper())}</div>', unsafe_allow_html=True)
            for page_key, label, icon in visible:
                state = "on" if page_key == active_page else "off"
                st.button(label, key=f"nav_{state}_{page_key}", icon=icon, on_click=go_to, args=(page_key,), width="stretch")
        st.markdown('<div class="shell-divider"></div>', unsafe_allow_html=True)
        user = current_user()
        st.markdown(
            f'<div class="shell-user"><div class="user-avatar">{escape(initials(user["name"]))}</div>'
            f"<div><b>{escape(user['name'])}</b><small>{escape(user['role'])} · @{escape(user['username'])}</small></div></div>",
            unsafe_allow_html=True,
        )
        st.button("Sign out", key="sidebar_sign_out", icon=":material/logout:", on_click=sign_out, width="stretch")
