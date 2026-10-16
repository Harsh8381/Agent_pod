"""App chrome: top bar, sidebar navigation, user menu, alerts and help."""

from html import escape

import streamlit as st

from frontend import access_control as ac
from frontend.ui.components import initials
from frontend.ui.state import ACCESS, can_open, current_user, go_to, open_record, persist_access, sign_out

# One source of truth for navigation: (section, [(page key, label, icon)]).
NAV_SECTIONS = (
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
PAGE_LABELS = {key: label for _, items in NAV_SECTIONS for key, label, _ in items}

TOP_NAV = (
    ("dashboard", "Overview"),
    ("new_consult", "Consultations"),
    ("review", "CDS Workbench"),
    ("codes", "Coding & Billing"),
    ("prior_auth", "Prior Auth"),
    ("admin_dashboard", "Oversight"),
)
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
}
WORKFLOW_HELP = (
    ("Consultations", "Pick or register the patient, then record, upload or paste the conversation."),
    ("Clinical Review & CDS", "Review guideline-backed recommendations, medication safety and care gaps."),
    ("SOAP Note & Sign", "Edit the draft note and sign it; signed notes are locked."),
    ("Coding & HCC", "Accept or reject ICD-10/CPT suggestions; pre- and post-coding checks guide you."),
    ("Billing & RCM", "Only performed and documented services are billed; send the claim draft to RCM."),
)


def render_page(active_page):
    """Draw the app chrome for a page: top bar plus sidebar."""
    _render_topbar(active_page)
    _render_sidebar(active_page)


def _render_topbar(active_page):
    group = TOP_NAV_GROUP.get(active_page)
    # One flex row sized to content: brand left, navigation centred, tools right.
    with st.container(
        key="nc_topbar", horizontal=True, horizontal_alignment="distribute", vertical_alignment="center", gap="small"
    ):
        st.markdown(
            '<div class="nc-brand"><div class="nc-brand-mark"></div><div>'
            '<div class="nc-brand-name">NuuCare</div><div class="nc-brand-sub">POWERED BY DATA COSMOS</div></div>'
            '<div class="nc-app-pills"><span class="nc-app-pill">Quote-to-Card</span>'
            '<span class="nc-app-pill on">Clinical Decision Platform</span></div></div>',
            unsafe_allow_html=True,
            width="content",
        )
        with st.container(horizontal=True, width="content", gap="small", vertical_alignment="center", key="nc_topnav"):
            for page_key, label in TOP_NAV:
                if can_open(page_key):
                    state = "on" if group == page_key else "off"
                    st.button(label, key=f"topnav_{state}_{page_key}", on_click=go_to, args=(page_key,), width="content")
        with st.container(horizontal=True, width="content", vertical_alignment="center", gap="small", key="nc_user_menu"):
            with st.container(width="content", key="nc_tb_alerts"):
                _render_alerts()
            with st.container(width="content", key="nc_tb_help"):
                _render_help()
            with st.container(width="content", key="nc_tb_avatar"):
                _render_user_menu()


def _render_alerts():
    pending = [record for record in st.session_state.records if record.get("status") == "Pending"]
    me = current_user()["username"]
    mine = [record for record in pending if record.get("assigned_to") == me]
    label = f"Alerts · {len(pending)}" if pending else "Alerts"
    with st.popover(label, icon=":material/notifications:"):
        st.markdown("**Notes awaiting sign-off**")
        if not pending:
            st.caption("Nothing waiting — every consultation note is signed.")
            return
        st.caption(f"{len(pending)} draft note(s) · {len(mine)} assigned to you")
        for record in (mine or pending)[:6]:
            text_col, open_col = st.columns([3, 1], vertical_alignment="center")
            text_col.markdown(
                f'{escape(record.get("name", "Patient"))}  \n<span class="nc-meta">{escape(record.get("id", ""))} · {escape(record.get("date", ""))}</span>',
                unsafe_allow_html=True,
            )
            open_col.button(
                "Open",
                key=f"alert_open_{record.get('id')}",
                on_click=open_record,
                args=(record,),
                disabled=not can_open("review"),
            )


def _render_help():
    with st.popover("Help", icon=":material/help:"):
        st.markdown("**How a consultation flows**")
        for index, (title, text) in enumerate(WORKFLOW_HELP, start=1):
            st.markdown(f"{index}. **{title}** — {text}")
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


def _render_sidebar(active_page):
    with st.sidebar:
        for index, (section, items) in enumerate(NAV_SECTIONS):
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
