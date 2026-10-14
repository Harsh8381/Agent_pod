import frontend.billing_scrubber as billing_scrubber


def _priced_lines(services, encounter_type):
    prices = {"71045": 100.0, "71046": 145.0, "70450": 725.0, "99213": 120.0, "99214": 185.0}
    lines = []
    for service in services:
        code = service.get("CPT/HCPCS Code")
        amount = prices.get(code)
        lines.append({
            "cpt_hcpcs": code,
            "unit_charge_usd": amount,
            "gross_line_total_usd": amount if amount is not None else None,
            "rate_source_status": "Found" if amount is not None else "Not found in chargemaster",
        })
    return lines


def test_two_view_chest_xray_mismatch_blocks_and_recommends_correct_code(monkeypatch):
    monkeypatch.setattr(billing_scrubber, "price_cpt_bill_lines", _priced_lines)

    report = billing_scrubber.scrub_claim(
        "Two-view chest X-ray was performed.",
        [{"CPT/HCPCS Code": "71045", "Extracted Procedure": "Chest X-ray", "quantity": 1}],
    )

    assert report["blocking"]
    assert any(issue["type"] == "imaging_mismatch" for issue in report["errors"])
    assert report["recommended_cpt_changes"][0]["to_code"] == "71046"
    assert report["recommended_cpt_changes"][0]["estimated_revenue_delta_usd"] == 45.0
    assert report["scrub_score"] < 100


def test_missing_price_blocks_claim_generation(monkeypatch):
    monkeypatch.setattr(billing_scrubber, "price_cpt_bill_lines", _priced_lines)

    report = billing_scrubber.scrub_claim(
        "CT head without contrast was performed.",
        [{"CPT/HCPCS Code": "70450", "Extracted Procedure": "CT head", "quantity": 1}],
        priced_lines=[{
            "cpt_hcpcs": "70450",
            "unit_charge_usd": None,
            "gross_line_total_usd": None,
            "rate_source_status": "Not found in chargemaster",
        }],
    )

    assert report["blocking"]
    assert report["errors"][0]["type"] == "unpriced_cpt"
    assert report["revenue_impact"]["unpriced_code_count"] == 1


def test_invalid_cpt_blocks_claim_generation(monkeypatch):
    monkeypatch.setattr(billing_scrubber, "price_cpt_bill_lines", _priced_lines)

    report = billing_scrubber.scrub_claim(
        "An unsupported service was performed.",
        [{"CPT/HCPCS Code": "99999", "Extracted Procedure": "Unsupported service"}],
    )

    assert report["blocking"]
    assert any(issue["type"] == "invalid_cpt" for issue in report["errors"])


def test_multiple_chronic_conditions_and_workup_warn_for_em_review(monkeypatch):
    monkeypatch.setattr(billing_scrubber, "price_cpt_bill_lines", _priced_lines)

    report = billing_scrubber.scrub_claim(
        "Diabetes and hypertension follow-up with CT head and lipid panel workup.",
        [
            {"CPT/HCPCS Code": "99213", "Extracted Procedure": "Office visit"},
            {"CPT/HCPCS Code": "70450", "Extracted Procedure": "CT head"},
        ],
    )

    assert not report["blocking"]
    assert any(issue["type"] == "em_level_review" for issue in report["warnings"])
    assert any(item["to_code"] == "99213 vs 99214" for item in report["recommended_cpt_changes"])
    assert report["recommended_cpt_changes"][-1]["estimated_revenue_delta_usd"] == 65.0


def test_two_view_chest_xray_does_not_mismatch_unrelated_ct_code(monkeypatch):
    monkeypatch.setattr(billing_scrubber, "price_cpt_bill_lines", _priced_lines)

    report = billing_scrubber.scrub_claim(
        "Two-view chest X-ray and CT head without contrast were performed.",
        [
            {"CPT/HCPCS Code": "71046", "Extracted Procedure": "Chest X-ray"},
            {"CPT/HCPCS Code": "70450", "Extracted Procedure": "CT head"},
        ],
    )

    assert not any(issue["type"] == "imaging_mismatch" for issue in report["errors"])


def test_imaging_or_lab_without_documentation_is_flagged_as_unsupported(monkeypatch):
    monkeypatch.setattr(billing_scrubber, "price_cpt_bill_lines", _priced_lines)

    report = billing_scrubber.scrub_claim(
        "Diabetes follow-up was completed.",
        [{"CPT/HCPCS Code": "80061", "Extracted Procedure": "Lipid panel"}],
    )

    assert report["blocking"]
    assert any(issue["type"] == "unsupported_cpt" and issue["code"] == "80061" for issue in report["errors"])


def test_ct_head_with_contrast_billed_as_without_contrast_is_rejected(monkeypatch):
    monkeypatch.setattr(billing_scrubber, "price_cpt_bill_lines", _priced_lines)

    report = billing_scrubber.scrub_claim(
        "CT scan of head with contrast was performed.",
        [{"CPT/HCPCS Code": "70450", "Extracted Procedure": "CT head without contrast"}],
    )

    assert report["blocking"]
    assert any(issue.get("expected_code") == "70460" for issue in report["errors"])