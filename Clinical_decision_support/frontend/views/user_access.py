"""User Access Management: accounts, role permissions, fallback coverage and audit log."""

from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st

from frontend import access_control as ac
from frontend.ui.components import badge, card_title, page_title, render_banner, render_html_table, render_tiles
from frontend.ui.shell import render_page
from frontend.ui.state import ACCESS, can, current_user, persist_access, save_db, user_label


def _user_status_badges(user):
    parts = [badge("Active", "green") if user.get("active", True) else badge("Inactive", "gray")]
    if user.get("out_of_office"):
        parts.append(badge("Out of office", "amber"))
    locked_until = user.get("locked_until")
    if locked_until and datetime.strptime(locked_until, ac.TIME_FORMAT) > datetime.now():
        parts.append(badge("Locked", "red"))
    if user.get("must_change_password"):
        parts.append(badge("Temporary password", "violet"))
    return '<div class="nc-badge-row">' + "".join(parts) + "</div>"


def apply_availability_change(store, username, was_available, actor):
    """Reroute or return encounters when a user's availability changes. Returns (verb, records)."""
    user = ac.get_user(store, username)
    now_available = ac.is_available(user)
    records = st.session_state.records
    if was_available and not now_available:
        reason = "Out of office" if user.get("active", True) else "Account deactivated"
        changed, verb = ac.reroute_assignments(store, records, username, actor, reason), "rerouted to fallback"
    elif not was_available and now_available:
        changed, verb = ac.restore_assignments(store, records, username, actor), "returned"
    else:
        changed, verb = [], ""
    if changed:
        save_db(records)
    return verb, changed


def _flash(message):
    st.session_state.access_flash = message


def _fallback_label(store, username):
    chain = ac.fallback_chain(store, username)
    return " → ".join(user_label(name) for name in chain) if chain else "—"


def _render_users_tab(store, me, held, rerouted):
    rows = []
    for user in store["users"]:
        effective = ac.resolve_assignee(store, user["username"])
        holder = (
            ""
            if effective == user["username"]
            else f'<div class="nc-meta">Work goes to: {escape(user_label(effective)) if effective else "unassigned queue"}</div>'
        )
        rows.append(
            [
                f'<b>{escape(user["name"])}</b><div class="nc-meta">@{escape(user["username"])} · {escape(user.get("email") or "no email")}</div>',
                escape(user["role"]),
                _user_status_badges(user),
                f"{escape(_fallback_label(store, user['username']))}{holder}",
                f"{held.get(user['username'], 0)} held · {rerouted.get(user['username'], 0)} covered by others",
                escape(user.get("last_login") or "Never"),
            ]
        )
    render_html_table(["User", "Role", "Status", "Fallback chain", "Encounters", "Last sign-in"], rows)
    usernames = [user["username"] for user in store["users"]]
    add_col, edit_col = st.columns(2, gap="medium")
    with add_col:
        with st.container(border=True):
            card_title("Add user", "The password you set is temporary; the user must change it at first sign-in")
            with st.form("access_add_user", clear_on_submit=True):
                name = st.text_input("Full name")
                username = st.text_input("Username")
                email = st.text_input("Email")
                role = st.selectbox("Role", ac.ROLES)
                password = st.text_input("Temporary password", type="password")
                fallback = st.selectbox(
                    "Fallback user",
                    ["— none —", *usernames],
                    format_func=lambda n: n if n.startswith("—") else f"{user_label(n)} (@{n})",
                )
                if st.form_submit_button("Create user", type="primary"):
                    try:
                        user = ac.create_user(
                            store,
                            username,
                            name,
                            email,
                            role,
                            password,
                            fallback=None if fallback.startswith("—") else fallback,
                            actor=me,
                            must_change_password=True,
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        persist_access()
                        _flash(f"Created {user['name']} (@{user['username']}). Share the temporary password securely.")
                        st.rerun()
    with edit_col:
        with st.container(border=True):
            card_title("Manage user", "Role, availability, fallback and password reset")
            selected = st.selectbox("User", usernames, format_func=lambda n: f"{user_label(n)} (@{n})", key="access_edit_user")
            user = ac.get_user(store, selected)
            locked = bool(user.get("locked_until") and datetime.strptime(user["locked_until"], ac.TIME_FORMAT) > datetime.now())
            with st.form(f"access_edit_{selected}"):
                name = st.text_input("Full name", value=user["name"])
                email = st.text_input("Email", value=user.get("email", ""))
                role = st.selectbox("Role", ac.ROLES, index=ac.ROLES.index(user["role"]))
                active_col, away_col = st.columns(2)
                active = active_col.checkbox("Account active", value=user.get("active", True))
                away = away_col.checkbox(
                    "Out of office",
                    value=user.get("out_of_office", False),
                    help="While away, this user's encounters are routed to their fallback and returned when they are back.",
                )
                options = ["— none —", *[name_ for name_ in usernames if name_ != selected]]
                current_fallback = user.get("fallback") if user.get("fallback") in options else "— none —"
                fallback = st.selectbox(
                    "Fallback user",
                    options,
                    index=options.index(current_fallback),
                    format_func=lambda n: n if n.startswith("—") else f"{user_label(n)} (@{n})",
                )
                new_password = st.text_input("Reset password (optional, temporary)", type="password")
                unlock = st.checkbox("Unlock account", value=False, disabled=not locked)
                if st.form_submit_button("Save changes", type="primary"):
                    if selected == me and not active:
                        st.error("You cannot deactivate your own account.")
                    else:
                        was_available = ac.is_available(user)
                        try:
                            ac.update_user(
                                store, selected, me, name=name, email=email, role=role, active=active, out_of_office=away
                            )
                            ac.set_fallback(store, selected, None if fallback.startswith("—") else fallback, actor=me)
                            if new_password:
                                ac.set_password(store, selected, new_password, actor=me, temporary=True)
                            if unlock:
                                ac.unlock_user(store, selected, actor=me)
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            verb, changed = apply_availability_change(store, selected, was_available, me)
                            persist_access()
                            moved = f" {len(changed)} encounter(s) {verb}." if changed else ""
                            _flash(f"Saved {name}.{moved}")
                            st.rerun()


def _render_roles_tab(store, me):
    role = st.selectbox("Role", ac.ROLES, key="access_role")
    permissions = ac.role_permissions(store, role)
    frame = pd.DataFrame(
        [
            {"Module": module, **{action: bool(permissions.get(module, {}).get(action)) for action in ac.ACTIONS}}
            for module in ac.MODULES
        ]
    )
    locked = role == ac.SUPER_ADMIN
    edited = st.data_editor(
        frame,
        key=f"access_matrix_{role}",
        hide_index=True,
        width="stretch",
        disabled=True if locked else ["Module"],
        column_config={action: st.column_config.CheckboxColumn(action) for action in ac.ACTIONS},
    )
    if locked:
        st.caption("Super Admin always has full access.")
    save_col, reset_col, _ = st.columns([1.3, 1.5, 6])
    if save_col.button("Save permissions", type="primary", disabled=locked, key="access_save_role"):
        ac.save_role_permissions(store, role, edited.to_dict("records"), actor=me)
        persist_access()
        _flash(f"Permissions saved for {role}.")
        st.rerun()
    if reset_col.button("Reset to defaults", disabled=locked, key="access_reset_role"):
        defaults = ac.DEFAULT_PERMISSIONS[role]
        ac.save_role_permissions(store, role, [{"Module": m, **defaults[m]} for m in ac.MODULES], actor=me)
        persist_access()
        _flash(f"{role} reset to default permissions.")
        st.rerun()
    with st.expander("Which permission controls each page"):
        st.dataframe(
            pd.DataFrame(
                [
                    {"Page": page.replace("_", " ").title(), "Permission module": module}
                    for page, module in ac.PAGE_MODULES.items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "View opens a page · Edit changes drafts, codes and records · Approve signs notes and sends claims · Admin manages users."
        )


def _render_coverage_tab(store, held, rerouted):
    rows, gaps = [], []
    for user in store["users"]:
        effective = ac.resolve_assignee(store, user["username"])
        if not user.get("fallback"):
            gaps.append(f"{user['name']} has no fallback user")
        elif effective is None:
            gaps.append(f"Nobody in {user['name']}'s fallback chain is available")
        rows.append(
            [
                f"<b>{escape(user['name'])}</b>",
                badge("Available", "green") if ac.is_available(user) else badge("Away", "amber"),
                escape(_fallback_label(store, user["username"])),
                escape(user_label(effective)) if effective else badge("Unassigned queue", "red"),
                str(held.get(user["username"], 0)),
                str(rerouted.get(user["username"], 0)),
            ]
        )
    if gaps:
        render_banner(f"{len(gaps)} coverage gap(s)", " · ".join(gaps), tone="warn", icon="&#9888;")
    else:
        render_banner("Every user has an available fallback", tone="ok", icon="&#10003;")
    render_html_table(["User", "Status", "Fallback chain", "Work currently goes to", "Holding", "Covered by others"], rows)
    st.caption(
        "When a user is marked out of office or deactivated, their encounters move to the first available user in their chain, and return automatically when they are available again."
    )


def _render_audit_tab(store):
    query = st.text_input("Filter audit log", placeholder="user, action or detail", key="access_audit_filter").strip().lower()
    entries = [
        entry for entry in reversed(store["audit"]) if not query or query in " ".join(str(v) for v in entry.values()).lower()
    ]
    st.dataframe(
        pd.DataFrame(entries or [{"at": "", "actor": "", "action": "", "detail": "No entries"}]),
        hide_index=True,
        width="stretch",
        height=420,
    )


def show_user_access():
    from collections import Counter

    render_page("super_admin")
    store, me = ACCESS["store"], current_user()["username"]
    page_title("ADMINISTRATION", "User Access Management", "Accounts, role permissions, fallback coverage and audit trail")
    if not can("Super Admin", "Admin"):
        render_banner("Only Super Admins can manage user access.", tone="warn", icon="&#128274;")
        return
    flash = st.session_state.pop("access_flash", None)
    if flash:
        st.success(flash)
    records = st.session_state.records
    held = Counter(record.get("assigned_to") for record in records if record.get("assigned_to"))
    rerouted = Counter(record.get("rerouted_from") for record in records if record.get("rerouted_from"))
    users = store["users"]
    render_tiles(
        [
            ("Users", len(users)),
            ("Active", sum(user.get("active", True) for user in users)),
            ("Out of office", sum(bool(user.get("out_of_office")) for user in users)),
            ("Unassigned encounters", sum(1 for record in records if not record.get("assigned_to"))),
        ]
    )
    users_tab, roles_tab, coverage_tab, audit_tab = st.tabs(["Users", "Roles & permissions", "Fallback coverage", "Audit log"])
    with users_tab:
        _render_users_tab(store, me, held, rerouted)
    with roles_tab:
        _render_roles_tab(store, me)
    with coverage_tab:
        _render_coverage_tab(store, held, rerouted)
    with audit_tab:
        _render_audit_tab(store)
