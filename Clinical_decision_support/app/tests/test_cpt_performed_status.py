import pytest

from cpt_coder import (
    classify_procedure_status,
    extract_performed_procedures,
    infer_documented_procedures,
    is_explicitly_performed,
)


@pytest.mark.parametrize(
    ("documentation", "expected_status"),
    [
        ("A chest X-ray was performed today.", "performed"),
        ("Chest X-ray completed during this encounter.", "performed"),
        ("Two-view chest radiographs were obtained.", "performed"),
        ("The patient underwent chest X-ray.", "performed"),
        ("Chest X-ray results demonstrate a clear lung field.", "performed"),
        ("A chest X-ray is planned.", "planned"),
        ("Plan to obtain a chest X-ray.", "planned"),
        ("Recommend chest X-ray.", "recommended"),
        ("Consider a chest X-ray.", "planned"),
        ("Chest X-ray was ordered but not performed.", "ordered_not_performed"),
        ("Chest X-ray was ordered and completed today.", "performed"),
        ("Chest X-ray was not performed.", "negated"),
        ("No chest X-ray was completed.", "negated"),
        ("No chest X-ray today.", "negated"),
        ("Imaging was done without a chest X-ray.", "negated"),
        ("History of chest X-ray last year.", "historical"),
        ("Previous chest X-ray showed an opacity.", "historical"),
        ("We discussed obtaining a chest X-ray.", "discussed"),
        ("The patient declined chest X-ray.", "ordered_not_performed"),
        ("Chest X-ray is pending.", "ordered_not_performed"),
        ("Chest X-ray may be needed.", "planned"),
        ("Rule out pneumonia with possible chest X-ray.", "planned"),
        ("Chest X-ray could be considered if symptoms persist.", "planned"),
        ("Chest X-ray cancelled before completion.", "ordered_not_performed"),
        ("Chest X-ray appears in the chart.", "unknown"),
    ],
)
def test_procedure_status_classification(documentation, expected_status):
    assert classify_procedure_status("Chest x-ray", documentation) == expected_status
    assert is_explicitly_performed("Chest x-ray", documentation) is (expected_status == "performed")


@pytest.mark.parametrize(
    ("term", "documentation"),
    [
        ("Chest X-ray", "CHEST X-RAY WAS PERFORMED TODAY!"),
        ("Chest X-ray", "Chest x ray: performed today."),
        ("Chest X-ray", "Two-view chest radiographs were obtained."),
        ("CXR", "CXR was completed during this encounter."),
        ("Chest radiograph", "Chest radiograph completed today."),
    ],
)
def test_performed_aliases_casing_and_punctuation(term, documentation):
    assert is_explicitly_performed(term, documentation)


def test_multiple_procedures_keep_status_associated_with_each_mention():
    documentation = "Chest X-ray performed today, but CT scan is planned."

    assert is_explicitly_performed("Chest X-ray", documentation)
    assert not is_explicitly_performed("CT scan", documentation)


def test_negation_applies_only_to_the_named_procedure():
    documentation = "Chest X-ray was not performed; complete blood count was completed today."

    assert not is_explicitly_performed("Chest X-ray", documentation)
    assert is_explicitly_performed("Complete blood count", documentation)


def test_negation_is_scoped_in_a_mixed_procedure_sentence():
    documentation = "Chest X-ray was not performed, but CBC was completed today."

    assert not is_explicitly_performed("Chest X-ray", documentation)
    assert is_explicitly_performed("Complete blood count", documentation)


def test_historical_mention_does_not_override_separate_current_performance():
    documentation = "Previous chest X-ray showed opacity. Today's chest X-ray was performed."

    assert classify_procedure_status("Chest X-ray", documentation) == "performed"


def test_unknown_or_unassociated_procedure_is_not_performed():
    assert classify_procedure_status("Chest X-ray", "Chest imaging was discussed.") == "unknown"
    assert not is_explicitly_performed("Chest X-ray", "Chest imaging was discussed.")


def test_extract_performed_procedures_excludes_nonperformed_mentions():
    assert extract_performed_procedures(
        "Chest X-ray was planned; CBC was performed today."
    ) == ["cbc"]


def test_inferred_procedures_are_candidates_not_performance_validation():
    # Candidate discovery remains separate from billing eligibility.
    assert infer_documented_procedures("A lipid panel is recommended.") == ["lipid panel"]
    assert not is_explicitly_performed("lipid panel", "A lipid panel is recommended.")


def test_local_coding_fallback_requires_performed_status(monkeypatch):
    from frontend.api_client import HealthcareApiClient
    from frontend.streamlit_app import get_backend_code_suggestions

    def unavailable_backend(self, conditions, procedures, documentation):
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(HealthcareApiClient, "match_codes", unavailable_backend)

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
