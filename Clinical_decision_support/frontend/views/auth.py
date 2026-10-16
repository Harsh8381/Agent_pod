"""First-run setup, sign-in, forced password change, session timeout and access-denied page."""

from datetime import datetime, timedelta
from html import escape

import streamlit as st

from frontend import access_control as ac
from frontend.settings import SESSION_IDLE_MINUTES
from frontend.ui.components import page_title, render_banner
from frontend.ui.shell import render_page
from frontend.ui.state import ACCESS, current_user, persist_access, sign_out
from frontend.ui.theme import inject_auth_theme


def _auth_header(title, subtitle):
    inject_auth_theme()
    st.markdown(
        '<div class="nc-auth-brand"><div class="nc-brand-mark"></div><div><b>NuuCare</b>'
        "<small>CLINICAL DECISION PLATFORM</small></div></div>"
        f'<div class="page-heading">{escape(title)}</div><div class="page-kicker">{escape(subtitle)}</div>',
        unsafe_allow_html=True,
    )


def _start_session(user):
    st.session_state.auth_username = user["username"]
    st.session_state.auth_last_seen = datetime.now()


def signed_in_user(store):
    """Current session's user, re-read from the store so deactivation applies immediately."""
    username = st.session_state.get("auth_username")
    if not username:
        return None
    user = ac.get_user(store, username)
    if user is None or not user.get("active", True):
        st.session_state.pop("auth_username", None)
        st.session_state.auth_notice = "Your account is no longer active. Contact an administrator."
        return None
    last_seen = st.session_state.get("auth_last_seen")
    now = datetime.now()
    if last_seen and now - last_seen > timedelta(minutes=SESSION_IDLE_MINUTES):
        ACCESS["user"] = ac.public_user(user)
        sign_out(f"You were signed out after {SESSION_IDLE_MINUTES} minutes of inactivity.")
        ACCESS["user"] = None
        return None
    st.session_state.auth_last_seen = now
    return ac.public_user(user)


def show_bootstrap(store):
    _, middle, _ = st.columns([1, 1.25, 1])
    with middle:
        _auth_header("Set up access", "No administrator account exists yet. Create the first Super Admin.")
        with st.form("bootstrap_form"):
            name = st.text_input("Full name")
            username = st.text_input("Username", placeholder="e.g. admin")
            email = st.text_input("Email (optional)")
            password = st.text_input(
                "Password", type="password", help=f"At least {ac.MIN_PASSWORD_LENGTH} characters with letters and numbers."
            )
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create Super Admin and sign in", type="primary", width="stretch")
        if submitted:
            if password != confirm:
                st.error("Passwords do not match.")
                return
            try:
                user = ac.create_user(
                    store, username, name, email, ac.SUPER_ADMIN, password, actor=str(username).strip().lower() or "setup"
                )
            except ValueError as error:
                st.error(str(error))
                return
            ac.audit(store, user["username"], "auth.bootstrap", "first Super Admin created")
            persist_access()
            _start_session(user)
            st.rerun()


def show_sign_in(store):
    _, middle, _ = st.columns([1, 1.1, 1])
    with middle:
        _auth_header("Sign in", "Use the account provided by your administrator.")
        notice = st.session_state.pop("auth_notice", None)
        if notice:
            st.info(notice)
        with st.form("sign_in_form"):
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            try:
                user = ac.authenticate(store, username, password)
            except ac.AuthError as error:
                persist_access()
                st.error(str(error))
                return
            persist_access()
            _start_session(user)
            st.rerun()
        st.caption("Accounts are managed by a Super Admin under Administration → User Access.")


def show_password_change_required(user):
    _, middle, _ = st.columns([1, 1.1, 1])
    with middle:
        _auth_header(
            "Choose a new password",
            f"Signed in as {user['name']}. Your password was set by an administrator and must be changed.",
        )
        with st.form("forced_password_form"):
            new_password = st.text_input("New password", type="password", autocomplete="new-password")
            confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
            submitted = st.form_submit_button("Save password", type="primary", width="stretch")
        if submitted:
            if new_password != confirm:
                st.error("Passwords do not match.")
                return
            try:
                ac.set_password(ACCESS["store"], user["username"], new_password, actor=user["username"])
            except ValueError as error:
                st.error(str(error))
                return
            persist_access()
            st.rerun()
        st.button("Sign out", key="forced_sign_out", on_click=sign_out)


def show_access_denied(page):
    render_page(page)
    module = ac.PAGE_MODULES.get(page, "this page")
    page_title("ACCESS RESTRICTED", "You don't have access to this page", f"{current_user()['role']} · {module}")
    render_banner(
        f"Your role does not include View access to {module}.",
        "Ask a Super Admin to update your role permissions under Administration → User Access.",
        tone="warn",
        icon="&#128274;",
    )
