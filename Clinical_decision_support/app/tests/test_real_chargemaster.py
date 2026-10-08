from pathlib import Path

from frontend.chargemaster import price_cpt_bill_lines


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_CHARGEMASTER = PROJECT_ROOT / "Original_Hospital_IPD_OPD_Charges_200_Demo.xlsx"


def test_real_opd_chargemaster_prices_ct_head_by_unit_times_quantity(monkeypatch):
    monkeypatch.setenv("CHARGEMASTER_PATH", str(REAL_CHARGEMASTER))

    lines = price_cpt_bill_lines(
        [{
            "Extracted Procedure": "CT scan of head without contrast",
            "Matched Procedure/Service": "CT Head",
            "CPT/HCPCS Code": "70450",
            "quantity": 2,
        }],
        encounter_type="OPD",
    )

    assert lines == [{
        "cpt_hcpcs": "70450",
        "description": "CT Head Without Contrast",
        "quantity": "2",
        "unit_charge_usd": 1900.0,
        "gross_line_total_usd": 3800.0,
        "rate_source_status": "Found",
        "encounter_type": "OPD",
        "chargemaster_sheet": "OPD Charges",
    }]


def test_real_ipd_sheet_is_selected_instead_of_opd_price(monkeypatch):
    monkeypatch.setenv("CHARGEMASTER_PATH", str(REAL_CHARGEMASTER))

    lines = price_cpt_bill_lines(
        [{"Extracted Procedure": "CT scan", "CPT/HCPCS Code": "70450"}],
        encounter_type="IPD",
    )

    assert lines[0]["chargemaster_sheet"] == "IPD Charges"
    assert lines[0]["rate_source_status"] == "Not found in chargemaster"
    assert lines[0]["gross_line_total_usd"] is None


def test_active_cdm_code_is_price_validated_against_opd_sheet(monkeypatch):
    monkeypatch.setenv("CHARGEMASTER_PATH", str(REAL_CHARGEMASTER))

    lines = price_cpt_bill_lines(
        [{"Extracted Procedure": "Office visit", "CPT/HCPCS Code": "99214"}],
        encounter_type="OPD",
    )

    assert lines[0]["unit_charge_usd"] == 410.0
    assert lines[0]["rate_source_status"] == "Found"


def test_unpriced_code_remains_visible_without_contributing_total(monkeypatch):
    monkeypatch.setenv("CHARGEMASTER_PATH", str(REAL_CHARGEMASTER))

    lines = price_cpt_bill_lines(
        [{"Extracted Procedure": "Unlisted service", "CPT/HCPCS Code": "99999", "quantity": 4}],
        encounter_type="OPD",
    )

    assert lines[0]["cpt_hcpcs"] == "99999"
    assert lines[0]["unit_charge_usd"] is None
    assert lines[0]["gross_line_total_usd"] is None
    assert lines[0]["rate_source_status"] == "Not found in chargemaster"
