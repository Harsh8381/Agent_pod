"""Every coded service in the hospital chargemaster must reach a priced bill line once documented as performed."""

from pathlib import Path

import pandas as pd
import pytest

from frontend.encounter_pipeline import (
    build_estimated_hospital_bill,
    filter_billing_eligible_cpt_results,
    get_code_suggestions,
    get_detected_insights,
)

REAL_CHARGEMASTER = Path(__file__).resolve().parents[2] / "Hospital_IPD_OPD_Charges_Athena_Care_Gap.xlsx"


def _chargemaster_services():
    services = []
    for sheet, encounter_type in (("OPD Charges", "OPD"), ("IPD Charges", "IPD")):
        frame = pd.read_excel(REAL_CHARGEMASTER, sheet_name=sheet, header=3, dtype=str).fillna("")
        frame = frame[frame["CPT/HCPCS"] != ""].drop_duplicates("CPT/HCPCS")
        services.extend(
            pytest.param(encounter_type, row["CPT/HCPCS"], row["Charge Description"], id=f"{encounter_type}-{row['CPT/HCPCS']}")
            for _, row in frame.iterrows()
        )
    return services


def _bill(documentation, encounter_type):
    insights = get_detected_insights(documentation) + ["Established Patient Office Visit"]
    cpt_results = get_code_suggestions(insights, documentation)["cpt"]
    billable, _, _ = filter_billing_eligible_cpt_results(cpt_results, documentation)
    rows, total, _ = build_estimated_hospital_bill(billable, encounter_type)
    return {row["CPT/HCPCS Code"]: row for row in rows}, total


@pytest.mark.parametrize(("encounter_type", "code", "description"), _chargemaster_services())
def test_every_chargemaster_service_is_billed_when_performed(monkeypatch, encounter_type, code, description):
    monkeypatch.setenv("CHARGEMASTER_PATH", str(REAL_CHARGEMASTER))

    rows, total = _bill(f"{description} was performed today.", encounter_type)

    assert set(rows) == {code}
    assert rows[code]["Pricing Status"] == "Priced"
    assert total > 0


@pytest.mark.parametrize(
    ("encounter_type", "documentation", "expected_codes"),
    [
        ("OPD", "Head CT was done today. Lipid panel collected.", {"70450", "80061"}),
        ("OPD", "EGD performed. Colonoscopy completed today.", {"43235", "45378"}),
        ("OPD", "Office visit level 4 completed. MRI of the brain was performed.", {"99214", "70551"}),
        ("IPD", "Cefazolin 1 g given IV. Normal saline bolus infused. Vancomycin administered.", {"J0690", "J7030", "J3370"}),
        ("IPD", "INR 2.3 today. CMP collected. Nebulizer treatment given.", {"85610", "80053", "94640"}),
        ("IPD", "PT evaluation low complexity performed. Fixation screws implanted.", {"97161", "C1713"}),
    ],
)
def test_natural_documentation_is_billed(monkeypatch, encounter_type, documentation, expected_codes):
    monkeypatch.setenv("CHARGEMASTER_PATH", str(REAL_CHARGEMASTER))

    rows, _ = _bill(documentation, encounter_type)

    assert set(rows) == expected_codes
    assert all(row["Pricing Status"] == "Priced" for row in rows.values())


@pytest.mark.parametrize(
    "documentation",
    [
        "Recommend colonoscopy and mammogram. Plan MRI brain next week.",
        "History of colonoscopy 3 years ago. Prior echocardiogram normal.",
        "Stress test ordered, pending.",
        "Patient takes metformin at home. On vancomycin per outside records.",
        "Psychotherapy discussed; patient declined.",
    ],
)
def test_services_not_performed_are_not_billed(monkeypatch, documentation):
    monkeypatch.setenv("CHARGEMASTER_PATH", str(REAL_CHARGEMASTER))

    rows, total = _bill(documentation, "OPD")

    assert rows == {}
    assert total == 0
