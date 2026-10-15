from pathlib import Path

import pandas as pd
import pytest

from frontend.chargemaster import build_cpt_bill_rows


def _cpt_result(code="71045", procedure="Chest X-ray", description="Chest imaging"):
    return {
        "Extracted Procedure": procedure,
        "Matched Procedure/Service": description,
        "CPT/HCPCS Code": code,
    }


def _write_csv(path: Path, headers=None, rows=None):
    pd.DataFrame(
        rows or [{"CPT/HCPCS Code": "71045", "Rate": "125.50", "Procedure Description": "Chest imaging", "Quantity/Units": "1"}],
        columns=headers,
    ).to_csv(path, index=False)


def test_valid_csv_lookup_returns_estimated_line(tmp_path, monkeypatch):
    source = tmp_path / "charges.csv"
    _write_csv(source)
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    rows = build_cpt_bill_rows([_cpt_result()], "2026-10-03")

    assert rows == [{
        "Date": "2026-10-03",
        "CPT/HCPCS Code": "71045",
        "Procedure Description": "Chest imaging",
        "Quantity/Units": "1",
        "Estimated Rate": "125.50",
        "Rate Source Status": "Found",
    }]


def test_valid_xlsx_lookup(tmp_path, monkeypatch):
    source = tmp_path / "charges.xlsx"
    pd.DataFrame([{
        "CPT Code": "71045",
        "Charge": "125.50",
        "Description": "Chest imaging",
        "Units": "1",
    }]).to_excel(source, index=False)
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    rows = build_cpt_bill_rows([_cpt_result()])

    assert rows[0]["Estimated Rate"] == "125.50"
    assert rows[0]["Rate Source Status"] == "Found"


def test_missing_cpt_is_not_found_without_invented_rate(tmp_path, monkeypatch):
    source = tmp_path / "charges.csv"
    _write_csv(source)
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result("99999")])[0]

    assert row["CPT/HCPCS Code"] == "99999"
    assert row["Estimated Rate"] == "Not available"
    assert row["Rate Source Status"] == "Not found in chargemaster"


def test_duplicate_cpt_rows_are_not_resolved_by_first_row(tmp_path, monkeypatch):
    source = tmp_path / "charges.csv"
    _write_csv(source, rows=[
        {"CPT/HCPCS Code": "71045", "Rate": "100.00"},
        {"CPT/HCPCS Code": "71045", "Rate": "200.00"},
    ])
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Estimated Rate"] == "Not available"
    assert row["Rate Source Status"] == "Duplicate code"


@pytest.mark.parametrize("rate", ["not-a-rate", "-1.00", ""])
def test_invalid_or_negative_rate_is_not_displayed(tmp_path, monkeypatch, rate):
    source = tmp_path / "charges.csv"
    _write_csv(source, rows=[{"Code": "71045", "Estimated Rate": rate}])
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Estimated Rate"] == "Not available"
    assert row["Rate Source Status"] == "Invalid rate"


@pytest.mark.parametrize(
    "headers",
    [
        ["Rate", "Description"],
        ["CPT/HCPCS Code", "Description"],
    ],
)
def test_missing_required_columns_are_reported(tmp_path, monkeypatch, headers):
    source = tmp_path / "charges.csv"
    _write_csv(source, headers=headers, rows=[])
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Estimated Rate"] == "Not available"
    assert row["Rate Source Status"] == "Missing required columns"


def test_missing_file_is_reported_without_breaking_coding(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARGEMASTER_PATH", str(tmp_path / "missing.csv"))

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Estimated Rate"] == "Not available"
    assert row["Rate Source Status"] == "Chargemaster unavailable"


def test_missing_path_configuration_is_reported(monkeypatch):
    monkeypatch.setenv("CHARGEMASTER_PATH", "")

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Estimated Rate"] == "Not available"
    assert row["Rate Source Status"] == "Chargemaster unavailable"


def test_unsupported_file_format_is_reported(tmp_path, monkeypatch):
    source = tmp_path / "charges.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Rate Source Status"] == "Unsupported format"
    assert row["Estimated Rate"] == "Not available"


def test_empty_cpt_input_returns_no_rows_without_source_config(monkeypatch):
    monkeypatch.delenv("CHARGEMASTER_PATH", raising=False)

    assert build_cpt_bill_rows([]) == []


def test_missing_description_and_quantity_are_not_fabricated(tmp_path, monkeypatch):
    source = tmp_path / "charges.csv"
    _write_csv(source, rows=[{"Code": "71045", "Rate": "125.50"}])
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result(description="")])[0]

    assert row["Procedure Description"] == "Not specified"
    assert row["Quantity/Units"] == "Not specified"
    assert row["Estimated Rate"] == "125.50"
    assert row["Rate Source Status"] == "Found"


def test_invalid_quantity_does_not_produce_an_estimated_line(tmp_path, monkeypatch):
    source = tmp_path / "charges.csv"
    _write_csv(source, rows=[{"Code": "71045", "Rate": "125.50", "Quantity": "several"}])
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Quantity/Units"] == "several"
    assert row["Estimated Rate"] == "Not available"
    assert row["Rate Source Status"] == "Invalid quantity"


def test_relative_path_resolves_against_project_root(tmp_path, monkeypatch):
    import frontend.chargemaster as chargemaster

    source = tmp_path / "charges.csv"
    _write_csv(source)
    monkeypatch.setattr(chargemaster, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("CHARGEMASTER_PATH", "charges.csv")

    row = chargemaster.build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Rate Source Status"] == "Found"


def test_absolute_path_is_used_directly(tmp_path, monkeypatch):
    source = tmp_path / "charges.csv"
    _write_csv(source)
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source.resolve()))

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Rate Source Status"] == "Found"


def test_code_formatting_is_normalized_for_lookup_only(tmp_path, monkeypatch):
    source = tmp_path / "charges.csv"
    _write_csv(source, rows=[{"Code": "710 45", "Rate": "80.00"}])
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result("71045")])[0]

    assert row["CPT/HCPCS Code"] == "71045"
    assert row["Estimated Rate"] == "80.00"
    assert row["Rate Source Status"] == "Found"


@pytest.mark.parametrize(
    ("candidate_code", "available_code"),
    [("71045", "71046"), ("99213", "99214"), ("82947", "85025")],
)
def test_lookup_never_substitutes_cpt_codes(
    tmp_path, monkeypatch, candidate_code, available_code
):
    source = tmp_path / "charges.csv"
    _write_csv(source, rows=[{"Code": available_code, "Rate": "999.00"}])
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result(candidate_code, "General blood test")])[0]

    assert row["CPT/HCPCS Code"] == candidate_code
    assert row["Estimated Rate"] == "Not available"
    assert row["Rate Source Status"] == "Not found in chargemaster"


@pytest.mark.parametrize(
    ("code_header", "rate_header"),
    [
        ("CPT/HCPCS Code", "Rate"),
        ("CPT Code", "Charge"),
        ("HCPCS Code", "Estimated Rate"),
        ("Procedure Code", "Standard Charge"),
        ("Code", "Gross Charge"),
    ],
)
def test_documented_required_column_aliases(
    tmp_path, monkeypatch, code_header, rate_header
):
    source = tmp_path / "charges.csv"
    _write_csv(source, rows=[{code_header: "71045", rate_header: "12.00"}])
    monkeypatch.setenv("CHARGEMASTER_PATH", str(source))

    row = build_cpt_bill_rows([_cpt_result()])[0]

    assert row["Estimated Rate"] == "12.00"
    assert row["Rate Source Status"] == "Found"