import pytest

from frontend.api_client import HealthcareApiClient
from frontend.streamlit_app import (
    filter_billing_eligible_cpt_results,
    get_backend_code_suggestions,
    prepare_billing_lines,
)


def _api_cpt(source="Chest X-ray", description="Chest imaging", code="71045"):
    return {
        "Extracted Procedure": source,
        "Matched Procedure/Service": description,
        "CPT/HCPCS Code": code,
    }


def test_performed_api_cpt_reaches_billing_eligibility():
    result = _api_cpt()

    eligible, excluded_count, unverifiable_count = filter_billing_eligible_cpt_results(
        [result], "Chest X-ray performed today."
    )

    assert eligible == [result]
    assert excluded_count == 0
    assert unverifiable_count == 0


@pytest.mark.parametrize(
    ("documentation", "expected_unverifiable"),
    [
        ("Chest X-ray is planned.", 0),
        ("Recommend chest X-ray.", 0),
        ("Chest X-ray was not performed.", 0),
        ("History of chest X-ray last year.", 0),
        ("We discussed obtaining a chest X-ray.", 0),
        ("Chest X-ray appears in the chart.", 1),
    ],
)
def test_nonperformed_and_unknown_api_cpts_are_excluded(
    documentation, expected_unverifiable
):
    result = _api_cpt()

    eligible, excluded_count, unverifiable_count = filter_billing_eligible_cpt_results(
        [result], documentation
    )

    assert eligible == []
    assert unverifiable_count == expected_unverifiable
    assert excluded_count == 1 - expected_unverifiable


def test_candidate_without_source_procedure_is_unverifiable_not_guessed():
    result = {
        "Matched Procedure/Service": "Chest X-ray performed today",
        "CPT/HCPCS Code": "71045",
    }

    eligible, excluded_count, unverifiable_count = filter_billing_eligible_cpt_results(
        [result], "Chest X-ray performed today."
    )

    assert eligible == []
    assert excluded_count == 0
    assert unverifiable_count == 1


def test_excluded_candidate_remains_in_normal_results():
    normal_cpt_results = [_api_cpt()]
    original_results = list(normal_cpt_results)

    eligible, excluded_count, unverifiable_count = filter_billing_eligible_cpt_results(
        normal_cpt_results, "Chest X-ray is planned."
    )

    assert normal_cpt_results == original_results
    assert eligible == []
    assert excluded_count == 1
    assert unverifiable_count == 0


def test_empty_cpt_results_are_safe():
    assert filter_billing_eligible_cpt_results([], "") == ([], 0, 0)


def test_generic_xray_transcript_still_triggers_cpt_recommendation():
    import frontend.streamlit_app as streamlit_app

    insights = streamlit_app.get_detected_insights("Chest xray was performed today for cough.")

    assert "Chest X-ray" in insights
    assert streamlit_app.get_code_suggestions(insights, "Chest xray was performed today for cough.")['cpt']


def test_ordered_ct_scan_still_surfaces_as_documented_cpt_suggestion():
    import frontend.streamlit_app as streamlit_app

    insights = ["CT Scan"]
    documentation = (
        "I am also ordering a CT scan of the head to rule out an underlying abnormality "
        "and continue monitoring the patient."
    )

    result = streamlit_app.get_code_suggestions(insights, documentation)

    assert result["cpt"]
    assert any(item["CPT/HCPCS Code"] == "70450" for item in result["cpt"])


def test_code_result_cache_signature_changes_with_transcript():
    import frontend.streamlit_app as streamlit_app

    insights = ["Chest X-ray", "History of Hypertension"]

    assert streamlit_app.get_code_results_signature(insights, "Chest X-ray performed today.") != (
        streamlit_app.get_code_results_signature(insights, "Chest X-ray planned.")
    )


def test_only_performed_candidates_reach_chargemaster(monkeypatch):
    import frontend.streamlit_app as streamlit_app

    lookup_inputs = []

    def capture_lookup(candidates, encounter_date):
        lookup_inputs.append(list(candidates))
        return []

    monkeypatch.setattr(streamlit_app, "build_cpt_bill_rows", capture_lookup)
    performed = _api_cpt()
    unverifiable = _api_cpt(source="CT scan", code="70450")

    eligible, excluded, unknown, _ = prepare_billing_lines(
        [performed, unverifiable],
        "Chest X-ray performed today. CT scan appears in the chart.",
        "2026-10-03",
    )

    assert eligible == [performed]
    assert excluded == 0
    assert unknown == 1
    assert lookup_inputs == [[performed]]

    prepare_billing_lines([unverifiable], "CT scan appears in the chart.")
    assert lookup_inputs == [[performed]]


def test_api_first_local_fallback_keeps_documented_procedures_in_code_suggestions(monkeypatch):
    def backend_unavailable(self, conditions, procedures, documentation):
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(HealthcareApiClient, "match_codes", backend_unavailable)

    planned = get_backend_code_suggestions(
        ["Complete Blood Count"], "A complete blood count is planned."
    )
    performed = get_backend_code_suggestions(
        ["Complete Blood Count"], "A complete blood count was performed today."
    )

    assert planned["cpt"]
    assert planned["cpt"][0]["CPT/HCPCS Code"] == "85025"
    assert performed["cpt"]
    assert performed["cpt"][0]["CPT/HCPCS Code"] == "85025"