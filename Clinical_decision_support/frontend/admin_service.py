"""Role model: roles, permission modules and the default role-permission matrix.

Two families of modules:
  * clinical workspace modules (consultations, coding, billing, administration);
  * value-based care screens S01-S16, granted from the "Who uses which screen"
    matrix of the VBC Agent screen-flow specification (primary vs occasional use).
"""

from copy import deepcopy

CLINICAL_ROLES = (
    "Coding Specialist",
    "RCM Analyst",
    "Clinical Reviewer",
    "Operations Admin",
    "Super Admin",
)
VBC_ROLES = (
    "Physician / APP",
    "Care Manager",
    "HEDIS Analyst",
    "HEDIS Lead",
    "Abstractor",
    "Pharmacist",
    "Stars / Contract",
    "Executive",
    "Auditor",
)
ROLES = CLINICAL_ROLES + VBC_ROLES
ACTIONS = ("View", "Edit", "Approve", "Admin")

CLINICAL_MODULES = (
    "Clinical Overview",
    "Clinical Review",
    "Codes",
    "RCM Workspace",
    "IPD Billing",
    "Admin Dashboard",
    "Super Admin",
    "EHR Demo Data",
)
# Screen code -> permission module name for the value-based care workspace.
VBC_SCREENS = {
    "S01": "S01 Stars dashboard",
    "S02": "S02 Measure explorer",
    "S03": "S03 Gap workbench",
    "S04": "S04 Point-of-care card",
    "S05": "S05 Person profile",
    "S06": "S06 Evidence panel",
    "S07": "S07 Exclusion review",
    "S08": "S08 Supplemental data",
    "S09": "S09 Payer reconciliation",
    "S10": "S10 Transitions board",
    "S11": "S11 Outreach console",
    "S12": "S12 Version center",
    "S13": "S13 Abstraction",
    "S14": "S14 Survey insights",
    "S15": "S15 Utilization & value",
    "S16": "S16 Governance & audit",
}
VBC_MODULES = tuple(VBC_SCREENS.values())
# Value-Based Care POC (longitudinal quality loop): one module, actions mapped to the POC role matrix.
POC_MODULE = "VBC Quality Loop (POC)"
MODULES = CLINICAL_MODULES + VBC_MODULES + (POC_MODULE,)
POC_EDITORS = ("Care Manager", "HEDIS Analyst", "HEDIS Lead", "Physician / APP", "Pharmacist", "Operations Admin")
POC_APPROVERS = ("Care Manager", "HEDIS Analyst", "HEDIS Lead", "Physician / APP")

# "Who uses which screen": P = primary user (view + work), O = occasional or read-only.
# Specification roles map onto app roles; Coder/CDI is the existing Coding Specialist and
# Physician/APP also applies to the existing Clinical Reviewer.
VBC_ROLE_MATRIX = {
    "S01": {"HEDIS Analyst": "O", "HEDIS Lead": "P", "Stars / Contract": "P", "Executive": "P", "Auditor": "O"},
    "S02": {"HEDIS Analyst": "P", "HEDIS Lead": "P", "Stars / Contract": "O", "Auditor": "O"},
    "S03": {"Care Manager": "P", "HEDIS Analyst": "P", "HEDIS Lead": "O", "Pharmacist": "O"},
    "S04": {"Physician / APP": "P", "Care Manager": "O", "Coding Specialist": "O"},
    "S05": {"Physician / APP": "O", "Care Manager": "P", "HEDIS Analyst": "P", "Pharmacist": "O", "Auditor": "O"},
    "S06": {"HEDIS Analyst": "P", "HEDIS Lead": "O", "Auditor": "O"},
    "S07": {"Physician / APP": "P", "HEDIS Analyst": "P", "Coding Specialist": "O", "Auditor": "O"},
    "S08": {"HEDIS Analyst": "P", "HEDIS Lead": "P", "Auditor": "O"},
    "S09": {"HEDIS Analyst": "P", "HEDIS Lead": "O", "Stars / Contract": "P"},
    "S10": {"Physician / APP": "O", "Care Manager": "P", "Pharmacist": "P"},
    "S11": {"Care Manager": "P", "Auditor": "O"},
    "S12": {"HEDIS Analyst": "O", "HEDIS Lead": "P", "Stars / Contract": "O", "Auditor": "O"},
    "S13": {"HEDIS Analyst": "O", "Abstractor": "P", "Auditor": "O"},
    "S14": {"Physician / APP": "O", "Care Manager": "O", "HEDIS Lead": "O", "Stars / Contract": "O", "Executive": "P"},
    "S15": {"HEDIS Lead": "O", "Stars / Contract": "P", "Executive": "P"},
    "S16": {"HEDIS Analyst": "O", "HEDIS Lead": "P", "Executive": "O", "Auditor": "P"},
}
# Sign-off rights that go beyond day-to-day work (four-eyes, attestation, confirmation).
VBC_APPROVERS = {
    "S03": ("HEDIS Lead",),  # assign work within team capacity
    "S04": ("Physician / APP",),  # place orders and record clinical decisions
    "S06": ("HEDIS Analyst",),  # human validation of edge-case evidence
    "S07": ("Physician / APP", "HEDIS Analyst"),  # confirm an exclusion
    "S08": ("HEDIS Lead",),  # authorized signatory for payer submission
    "S09": ("Stars / Contract",),  # raise a dispute with the payer
    "S11": ("Care Manager",),  # approve messages that policy says need a person
    "S12": ("HEDIS Lead", "Operations Admin"),  # four-eyes publication
    "S13": ("HEDIS Analyst",),  # QA over-read of abstractions
    "S14": ("Operations Admin",),  # route survey themes to operational owners
    "S16": ("HEDIS Lead",),  # restore quarantined lineage, freeze at period close
}
# Read-only roles never receive Edit, even where they are primary users.
READ_ONLY_ROLES = ("Auditor", "Executive")
# Existing app roles that act as specification roles on the VBC screens.
ROLE_ALIASES = {"Clinical Reviewer": "Physician / APP"}


def _empty():
    return {module: {action: False for action in ACTIONS} for module in MODULES}


def _grant_clinical(role, permissions):
    for module in ("Clinical Overview", "Clinical Review", "Codes", "EHR Demo Data"):
        permissions[module]["View"] = True
    if role == "Coding Specialist":
        permissions["Codes"].update(Edit=True, Approve=True)
    elif role == "RCM Analyst":
        permissions["RCM Workspace"].update(View=True, Edit=True, Approve=True)
        permissions["IPD Billing"].update(View=True, Edit=True)
    elif role in ("Clinical Reviewer", "Physician / APP"):
        permissions["Clinical Review"].update(Edit=True, Approve=True)
    elif role == "Operations Admin":
        for module in ("RCM Workspace", "IPD Billing", "Admin Dashboard"):
            permissions[module].update(View=True, Edit=True)
        permissions["Clinical Review"]["Approve"] = True
    elif role == "Care Manager" or role in ("HEDIS Lead", "Executive", "Auditor"):
        permissions["Admin Dashboard"]["View"] = True


def _grant_vbc(role, permissions):
    spec_role = ROLE_ALIASES.get(role, role)
    for screen, module in VBC_SCREENS.items():
        usage = VBC_ROLE_MATRIX[screen].get(spec_role) or VBC_ROLE_MATRIX[screen].get(role)
        if role == "Operations Admin":
            usage = usage or "O"
        if role == "RCM Analyst" and screen in ("S08", "S09"):
            usage = "O"
        if not usage:
            continue
        permissions[module]["View"] = True
        if usage == "P" and spec_role not in READ_ONLY_ROLES:
            permissions[module]["Edit"] = True
        if spec_role in VBC_APPROVERS.get(screen, ()) or role in VBC_APPROVERS.get(screen, ()):
            permissions[module]["Approve"] = True
            permissions[module]["Edit"] = permissions[module]["Edit"] or spec_role not in READ_ONLY_ROLES


def _grant_poc(role, permissions):
    spec_role = ROLE_ALIASES.get(role, role)
    if spec_role not in VBC_ROLES and role != "Operations Admin":
        return
    grant = permissions[POC_MODULE]
    grant["View"] = True
    grant["Edit"] = spec_role in POC_EDITORS or role in POC_EDITORS
    grant["Approve"] = spec_role in POC_APPROVERS
    grant["Admin"] = role == "Operations Admin"


def _role_permissions():
    defaults = {}
    for role in ROLES:
        permissions = _empty()
        if role == "Super Admin":
            for module in MODULES:
                permissions[module] = {action: True for action in ACTIONS}
        else:
            _grant_clinical(role, permissions)
            _grant_vbc(role, permissions)
            _grant_poc(role, permissions)
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
        normalized[module] = {action: bool(row.get(action, False)) for action in ACTIONS}
    session_state["admin_role_permissions"][role] = normalized
