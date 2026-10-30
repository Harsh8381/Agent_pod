"""Cached reads from the FastAPI EHR backend, plus backend error formatting."""

import requests
import streamlit as st

from frontend.api_client import HealthcareApiClient

BACKEND_ERRORS = (RuntimeError, requests.exceptions.RequestException, OSError, ValueError)


def backend_error_message(error):
    """Prefer the FastAPI `detail` text over the bare HTTP status line."""
    response = getattr(error, "response", None)
    if response is not None:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if detail:
            return f"{detail} (HTTP {response.status_code})"
    return str(error)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_ehr_patients():
    """Return the EHR patient list, or None when the backend is unavailable."""
    try:
        return HealthcareApiClient().list_patients().get("patients", [])
    except BACKEND_ERRORS:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_patient_chart(patient_id):
    try:
        return HealthcareApiClient().get_patient_chart(str(patient_id))
    except BACKEND_ERRORS:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def fetch_ehr_export():
    """Whole-EHR export ({"sheets": ...}) for population views, or None when offline."""
    try:
        return HealthcareApiClient().export_patient_data()
    except BACKEND_ERRORS:
        return None


def refresh_ehr_cache():
    """Drop cached EHR reads after a write so every page sees the change."""
    fetch_ehr_patients.clear()
    fetch_patient_chart.clear()
    fetch_ehr_export.clear()


def record_patient_id(record):
    return str((record or {}).get("ehr_patient_id") or (record or {}).get("patient_id") or "")


def record_chart(record):
    patient_id = record_patient_id(record)
    return fetch_patient_chart(patient_id) if patient_id else {}
