"""Page registry and the per-run access gate."""

import streamlit as st

from frontend import access_control as ac
from frontend.admin_console import show_admin_dashboard
from frontend.claim_generation_page import show_claim_generation_page
from frontend.ipd_billing import show_ipd_billing
from frontend.settings import ACCESS_STORE_PATH
from frontend.ui.state import ACCESS
from frontend.views.auth import (
    show_access_denied,
    show_bootstrap,
    show_password_change_required,
    show_sign_in,
    signed_in_user,
)
from frontend.views.billing import show_billing
from frontend.views.clinical_tools import (
    show_knowledge_base,
    show_medications,
    show_prior_auth,
)
from frontend.views.coding import show_codes
from frontend.views.consultation import show_new_consultation
from frontend.views.dashboard import show_dashboard
from frontend.views.ehr_records import show_ehr_demo_data
from frontend.views.review import show_review_note
from frontend.views.user_access import show_user_access
from frontend.views.vbc.command import show_measures, show_stars, show_versions
from frontend.views.vbc.gaps import show_gaps, show_outreach, show_person, show_poc
from frontend.views.vbc.prove import (
    show_abstraction,
    show_evidence,
    show_exclusions,
    show_payer,
    show_supplemental,
)
from frontend.views.vbc.transitions import show_transitions
from frontend.views.vbc.value import (
    show_governance,
    show_playbook,
    show_surveys,
    show_value,
)
from frontend.views.vbc_poc.dashboard import show_dashboard as show_poc_dashboard
from frontend.views.vbc_poc.gaps import show_worklist as show_poc_worklist
from frontend.views.vbc_poc.governance import show_governance as show_poc_governance
from frontend.views.vbc_poc.patient import show_patient_360 as show_poc_patient


PAGES = {
    "dashboard": show_dashboard,
    "new_consult": show_new_consultation,
    "review": show_review_note,
    "medications": show_medications,
    "prior_auth": show_prior_auth,
    "codes": show_codes,
    "billing": show_billing,
    "knowledge_base": show_knowledge_base,
    "ipd_billing": show_ipd_billing,
    "claim_generation": show_claim_generation_page,
    "ehr_data": show_ehr_demo_data,
    "admin_dashboard": show_admin_dashboard,
    "super_admin": show_user_access,
    # Value-based care workspace (HEDIS / Stars), S01–S16
    "vbc_stars": show_stars,
    "vbc_measures": show_measures,
    "vbc_versions": show_versions,
    "vbc_playbook": show_playbook,
    "vbc_gaps": show_gaps,
    "vbc_poc": show_poc,
    "vbc_person": show_person,
    "vbc_outreach": show_outreach,
    "vbc_evidence": show_evidence,
    "vbc_exclusions": show_exclusions,
    "vbc_supplemental": show_supplemental,
    "vbc_payer": show_payer,
    "vbc_abstraction": show_abstraction,
    "vbc_transitions": show_transitions,
    "vbc_surveys": show_surveys,
    "vbc_value": show_value,
    "vbc_governance": show_governance,
    # Value-Based Care POC: longitudinal quality loop (feature flag VBC_POC_ENABLED)
    "vbcpoc_dashboard": show_poc_dashboard,
    "vbcpoc_patient": show_poc_patient,
    "vbcpoc_gaps": show_poc_worklist,
    "vbcpoc_governance": show_poc_governance,
}


def run_app():
    """Authenticate, enforce page permissions, then render the requested page."""
    store = ac.load_store(ACCESS_STORE_PATH)
    ACCESS["store"] = store
    ACCESS["user"] = None
    if ac.needs_bootstrap(store):
        show_bootstrap(store)
        return
    user = signed_in_user(store)
    if user is None:
        show_sign_in(store)
        return
    ACCESS["user"] = user
    if user.get("must_change_password"):
        show_password_change_required(user)
        return
    page = st.session_state.page if st.session_state.page in PAGES else "dashboard"
    if not ac.can_view_page(store, user, page):
        first_allowed = next((key for key in PAGES if ac.can_view_page(store, user, key)), None)
        if page == "dashboard" and first_allowed:
            st.session_state.page = first_allowed
            st.rerun()
        show_access_denied(page)
        return
    st.session_state.page = page
    PAGES[page]()
