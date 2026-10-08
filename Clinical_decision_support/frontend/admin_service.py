from copy import deepcopy


ROLES = (
    "Coding Specialist",
    "RCM Analyst",
    "Clinical Reviewer",
    "Operations Admin",
    "Super Admin",
)
ACTIONS = ("View", "Edit", "Approve", "Admin")
MODULES = (
    "Clinical Overview",
    "Clinical Review",
    "Codes",
    "RCM Workspace",
    "IPD Billing",
    "Admin Dashboard",
    "Super Admin",
    "EHR Demo Data",
)


def _role_permissions():
    defaults = {}
    for role in ROLES:
        permissions = {module: {action: False for action in ACTIONS} for module in MODULES}
        permissions["Clinical Overview"]["View"] = True
        permissions["Clinical Review"]["View"] = True
        permissions["Codes"]["View"] = True
        permissions["EHR Demo Data"]["View"] = True
        if role == "Coding Specialist":
            permissions["Codes"].update(Edit=True, Approve=True)
        elif role == "RCM Analyst":
            permissions["RCM Workspace"].update(View=True, Edit=True, Approve=True)
            permissions["IPD Billing"].update(View=True, Edit=True)
        elif role == "Clinical Reviewer":
            permissions["Clinical Review"].update(Edit=True, Approve=True)
        elif role == "Operations Admin":
            for module in ("RCM Workspace", "IPD Billing", "Admin Dashboard"):
                permissions[module].update(View=True, Edit=True)
            permissions["Clinical Review"]["Approve"] = True
        elif role == "Super Admin":
            for module in MODULES:
                permissions[module].update(View=True, Edit=True, Approve=True, Admin=True)
        defaults[role] = permissions
    return defaults


DEFAULT_PERMISSIONS = _role_permissions()


def ensure_admin_state(session_state):
    session_state.setdefault("admin_roles", list(ROLES))
    session_state.setdefault("admin_users", [])
    session_state.setdefault("admin_role_permissions", deepcopy(DEFAULT_PERMISSIONS))
    for role in session_state["admin_roles"]:
        if role not in session_state["admin_role_permissions"]:
            session_state["admin_role_permissions"][role] = deepcopy(DEFAULT_PERMISSIONS.get(role, {}))
    session_state.setdefault("admin_selected_role", ROLES[0])
    if session_state["admin_selected_role"] not in session_state["admin_roles"]:
        session_state["admin_selected_role"] = session_state["admin_roles"][0]


def add_admin_user(session_state, name, email, role):
    clean_name = str(name or "").strip()
    clean_email = str(email or "").strip()
    if not clean_name:
        raise ValueError("Enter a user name.")
    users = session_state["admin_users"]
    if any(user["name"].casefold() == clean_name.casefold() for user in users):
        raise ValueError(f"A user named {clean_name} is already listed.")
    if role not in session_state["admin_roles"]:
        raise ValueError("Choose one of the configured roles.")
    user = {"name": clean_name, "email": clean_email, "role": role}
    users.append(user)
    return user


def save_role_permissions(session_state, role, edited_permissions):
    if role not in session_state["admin_roles"]:
        raise ValueError(f"Unknown role: {role}")
    normalized = {}
    for row in edited_permissions:
        module = str(row.get("Module") or "").strip()
        if module not in MODULES:
            continue
        normalized[module] = {
            action: bool(row.get(action, False))
            for action in ACTIONS
        }
    session_state["admin_role_permissions"][role] = normalized