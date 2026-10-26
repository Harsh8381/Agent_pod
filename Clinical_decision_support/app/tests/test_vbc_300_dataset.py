from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from app.vbc.dashboard import get_population_dashboard
from app.vbc.dataset import (
    DEFAULT_DATASET_PATH,
    PROJECT_ROOT,
    VbcDatasetNotFoundError,
    VbcDatasetRepository,
    VbcDatasetValidationError,
    get_dataset_path,
)


EXPECTED_NEW_DATASET_PATH = (
    PROJECT_ROOT
    / "data"
    / "vbc_300"
    / "synthetic_vbc_dataset_300_hedis.zip"
)
SUPPLIED_NEW_DATASET_PATH = (
    PROJECT_ROOT / "data" / "synthetic_vbc_dataset_300_hedis.zip"
)


@pytest.fixture
def new_dataset_path(monkeypatch: pytest.MonkeyPatch) -> Path:
    path = (
        EXPECTED_NEW_DATASET_PATH
        if EXPECTED_NEW_DATASET_PATH.is_file()
        else SUPPLIED_NEW_DATASET_PATH
    )
    if not path.is_file():
        pytest.skip("The new synthetic VBC archive is not available.")
    monkeypatch.setenv("VBC_DATASET_PATH", str(path))
    return path


def test_dataset_path_defaults_and_resolves_relative_to_project_root(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("VBC_DATASET_PATH", raising=False)
    assert get_dataset_path() == DEFAULT_DATASET_PATH.resolve()

    monkeypatch.setenv("VBC_DATASET_PATH", "data/synthetic_vbc_dataset.zip")
    assert get_dataset_path() == DEFAULT_DATASET_PATH.resolve()


def test_absolute_and_explicit_dataset_paths_are_supported(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("VBC_DATASET_PATH", str(DEFAULT_DATASET_PATH))
    assert get_dataset_path() == DEFAULT_DATASET_PATH.resolve()

    monkeypatch.setenv("VBC_DATASET_PATH", "https://invalid.example/dataset.zip")
    assert get_dataset_path(DEFAULT_DATASET_PATH) == DEFAULT_DATASET_PATH.resolve()


def test_configured_missing_path_and_urls_fail_clearly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    missing_path = tmp_path / "missing-vbc.zip"
    monkeypatch.setenv("VBC_DATASET_PATH", str(missing_path))
    with pytest.raises(VbcDatasetNotFoundError, match="missing-vbc.zip"):
        VbcDatasetRepository()

    monkeypatch.setenv("VBC_DATASET_PATH", "https://example.invalid/data.zip")
    with pytest.raises(VbcDatasetValidationError, match="not a URL"):
        VbcDatasetRepository()


def test_dataset_switching_does_not_reuse_rows_from_another_archive(
    monkeypatch: pytest.MonkeyPatch, new_dataset_path: Path
):
    monkeypatch.delenv("VBC_DATASET_PATH", raising=False)
    legacy_repository = VbcDatasetRepository()
    monkeypatch.setenv("VBC_DATASET_PATH", str(new_dataset_path))
    new_repository = VbcDatasetRepository()

    assert len(legacy_repository.get_patients()) == 20
    assert len(new_repository.get_patients()) == 300
    assert new_repository.archive_path == new_dataset_path.resolve()


def test_new_dataset_capabilities_and_source_counts(new_dataset_path: Path):
    repository = VbcDatasetRepository()
    metadata = repository.get_dataset_metadata()

    assert metadata["patient_count"] == 300
    assert metadata["measure_count"] == 6
    assert metadata["is_legacy_dataset"] is False
    assert metadata["has_measure_eligibility"] is True
    assert metadata["has_measure_history"] is True
    assert metadata["has_cross_reference"] is True
    assert metadata["has_manifest"] is True
    assert metadata["has_data_dictionary"] is True
    assert len(repository.get_providers()) == 34
    assert len(repository.get_encounters()) == 2227
    assert len(repository.get_claims()) == 2227
    assert len(repository.get_measure_eligibility()) == 1800
    assert len(repository.get_measure_results()) == 1800
    assert len(repository.get_risk_scores()) == 300
    assert len(repository.get_care_gaps()) == 505
    assert len(repository.get_care_plans()) == 505
    assert len(repository.get_outreach()) == 592
    assert len(repository.get_audit_trail()) == 1197
    assert len(repository.get_measure_history()) == 72
    assert len(repository.get_patient_cross_reference()) == 300
    assert repository.list_supported_measures() == [
        "GSD",
        "BPD-E",
        "KED",
        "BCS-E",
        "COL-E",
        "TRC",
    ]
    assert Counter(
        repository.get_population_field(patient)
        for patient in repository.get_patients()
    ) == {
        "Diabetes": 120,
        "Adult Preventive": 80,
        "Post-Discharge": 50,
        "Hypertension": 30,
        "General Adult": 20,
    }
    assert all(
        len(repository.get_measure_eligibility(patient["patient_id"])) == 6
        for patient in repository.get_patients()
    )
    assert all(
        len(repository.get_measure_results(patient["patient_id"])) == 6
        for patient in repository.get_patients()
    )


def test_new_dashboard_uses_eligibility_and_orders_history(
    new_dataset_path: Path,
):
    repository = VbcDatasetRepository()
    dashboard = get_population_dashboard()

    assert dashboard["kpis"]["total_patients"] == 300
    assert dashboard["risk_distribution"] == {
        "High": 80,
        "Low": 179,
        "Medium": 41,
    }
    assert [row["measure_id"] for row in dashboard["measure_performance"]] == [
        "GSD",
        "BPD-E",
        "KED",
        "BCS-E",
        "COL-E",
        "TRC",
    ]
    assert dashboard["population_segment_distribution"] == {
        "Adult Preventive": 80,
        "Diabetes": 120,
        "General Adult": 20,
        "Hypertension": 30,
        "Post-Discharge": 50,
    }
    assert len(dashboard["measure_history"]) == 72
    for measure in dashboard["measure_performance"]:
        eligibility_rows = repository.get_measure_eligibility(
            measure_id=measure["measure_id"]
        )
        result_rows = {
            (row["patient_id"], row["measure_id"]): row
            for row in repository.get_measure_results()
        }
        eligible_rows = [
            row
            for row in eligibility_rows
            if row["eligible"].strip().casefold() == "yes"
        ]
        expected_met = sum(
            result_rows[(row["patient_id"], row["measure_id"])]["state"]
            .strip()
            .casefold()
            == "met"
            for row in eligible_rows
        )
        assert measure["eligible_count"] == len(eligible_rows)
        assert measure["met_count"] == expected_met

    history_dates = [
        row["snapshot_date"] for row in dashboard["measure_history"]
    ]
    assert history_dates == sorted(history_dates)
    assert not dashboard["warnings"]
    assert len(get_population_dashboard(measure_id="KED")["measure_history"]) == 12
    assert get_population_dashboard(
        population_segment="Diabetes"
    )["kpis"]["total_patients"] == 120
    if dashboard["priority_worklist"]:
        worklist_row = dashboard["priority_worklist"][0]
        assert not {
            "first_name",
            "last_name",
            "dob",
            "phone",
            "email",
            "address",
            "scenario_profile",
            "ehr_patient_id",
        }.intersection(worklist_row)
        assert "masked_display_name" in worklist_row
        assert "population_segment" in worklist_row


def test_legacy_dataset_supports_compatibility_population_field(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("VBC_DATASET_PATH", raising=False)
    repository = VbcDatasetRepository()
    assert len(repository.get_patients()) == 20
    assert repository.get_population_field(
        {"cohort": "Diabetes Controlled"}
    ) == "Diabetes"
    assert repository.get_population_field(
        {"cohort": "Recently Discharged"}
    ) == "Post-Discharge"
    assert repository.get_dataset_metadata()["has_measure_eligibility"] is False


def test_archive_hashes_do_not_change_after_read_only_load(
    new_dataset_path: Path,
):
    paths = (DEFAULT_DATASET_PATH, new_dataset_path)
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in paths}

    VbcDatasetRepository()

    assert {
        path: hashlib.sha256(path.read_bytes()).digest() for path in paths
    } == before
    assert not (PROJECT_ROOT / "data" / "patients.csv").exists()
