from frontend.streamlit_app import select_cpt_results_for_bill


def test_bill_selection_includes_only_selected_cpt_codes():
    results = [
        {"CPT/HCPCS Code": "71046", "Matched Procedure/Service": "Chest X-ray two views"},
        {"CPT/HCPCS Code": "70450", "Matched Procedure/Service": "CT head without contrast"},
    ]

    selected = select_cpt_results_for_bill(results, ["70450"])

    assert selected == [results[1]]


def test_bill_selection_deduplicates_selected_codes():
    results = [
        {"CPT/HCPCS Code": "71046", "Matched Procedure/Service": "Chest X-ray two views"},
        {"CPT/HCPCS Code": "71046", "Matched Procedure/Service": "Chest radiograph two views"},
    ]

    assert select_cpt_results_for_bill(results, ["71046", "71046"]) == [results[0]]


def test_empty_bill_selection_returns_no_codes():
    assert select_cpt_results_for_bill([{"CPT/HCPCS Code": "71046"}], []) == []