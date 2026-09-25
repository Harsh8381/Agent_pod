import code_matcher


def suggested_codes(results):
    return [item["selected_code"] for item in results if item["status"] == "suggested"]


def test_fever_stays_in_r50_family():
    results = code_matcher.get_icd10_candidate_sets(["Fever"])
    assert suggested_codes(results) == ["R50.9"]
    assert "A01.0" not in suggested_codes(results)


def test_headache_stays_in_r51_family():
    results = code_matcher.get_icd10_candidate_sets(["Headache"])
    assert suggested_codes(results) == ["R51"]
    assert "G44.01" not in suggested_codes(results)


def test_type_two_diabetes_stays_in_e11_family():
    results = code_matcher.get_icd10_candidate_sets(["Diabetes (Type 2)"])
    assert suggested_codes(results) == ["E11.9"]
    assert "B97.33" not in suggested_codes(results)


def test_negated_fever_has_no_active_code():
    results = code_matcher.get_icd10_candidate_sets(["Fever"], evidence_by_term={"Fever": "No fever reported."})
    assert suggested_codes(results) == []
    assert results[0]["status"] == "review_required"


def test_history_of_diabetes_is_not_active():
    results = code_matcher.get_icd10_candidate_sets(["Diabetes (Type 2)"], evidence_by_term={"Diabetes (Type 2)": "History of diabetes only."})
    assert suggested_codes(results) == []


def test_rule_out_pneumonia_is_not_confirmed():
    results = code_matcher.get_icd10_candidate_sets(["Pneumonia"], evidence_by_term={"Pneumonia": "Rule out pneumonia."})
    assert suggested_codes(results) == []


def test_ordered_xray_is_not_a_performed_cpt_service():
    assert code_matcher.extract_performed_procedures("Chest x-ray ordered.") == []
    results = code_matcher.get_cpt_candidate_sets(["chest x ray"], "Chest x-ray ordered.")
    assert suggested_codes(results) == []


def test_performed_xray_uses_cpt_pipeline():
    assert code_matcher.extract_performed_procedures("Chest x-ray performed.") == ["x ray", "chest x ray"]
    results = code_matcher.get_cpt_candidate_sets(["chest x ray"], "Chest x-ray performed.")
    assert suggested_codes(results) == ["71045"]


def test_knee_pain_produces_icd_only():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Knee pain"])
    assert result["icd10"]
    assert result["cpt"] == []


def test_documented_performed_procedure_returns_cpt_only():
    results = code_matcher.get_cpt_candidate_sets(["complete blood count"], "Complete blood count performed.")
    assert suggested_codes(results)
    assert results[0]["selected_code"] == "85025"


def test_brief_procedure_list_still_returns_cpt_candidates():
    results = code_matcher.get_cpt_candidate_sets(["complete blood count"], "")
    assert suggested_codes(results) == ["85025"]
