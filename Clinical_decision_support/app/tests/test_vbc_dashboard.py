from __future__ import annotations

import copy
import hashlib
import zipfile
from datetime import date

import pytest

from app.vbc import dashboard
from app.vbc.dataset import DEFAULT_DATASET_PATH, VbcDatasetRepository


class MemoryRepository:
    def __init__(self, tables, summary=None):
        self.tables = tables
        self.summary = (
            {"as_of_date": "2026-10-22"} if summary is None else summary
        )

    def __getattr__(self, name):
        if name == "get_population_summary":
            return lambda: copy.deepcopy(self.summary)
        if name.startswith("get_"):
            table_name = f"{name[4:]}.csv"
            if table_name in self.tables:
                return lambda: copy.deepcopy(self.tables[table_name])
        raise AttributeError(name)


def _empty_tables():
    return {
        "patients.csv": [],
        "providers.csv": [],
        "encounters.csv": [],
        "conditions.csv": [],
        "observations.csv": [],
        "procedures.csv": [],
        "claims.csv": [],
        "documents.csv": [],
        "measure_results.csv": [],
        "care_gaps.csv": [],
        "risk_scores.csv": [],
        "care_plans.csv": [],
        "outreach.csv": [],
        "audit_trail.csv": [],
    }


def _memory_repository(monkeypatch, tables, summary=None):
    repository = MemoryRepository(tables, summary)
    monkeypatch.setattr(dashboard, "VbcDatasetRepository", lambda: repository)
    return repository


def _minimal_tables():
    tables = _empty_tables()
    tables["patients.csv"] = [
        {
            "patient_id": "P-1",
            "first_name": "Ada",
            "last_name": "Miller",
            "dob": "1980-01-01",
            "cohort": "Cohort A",
            "attributed_provider_id": "PR-1",
            "risk_category": "High",
        },
        {
            "patient_id": "P-2",
            "first_name": "Ben",
            "last_name": "Jones",
            "dob": "",
            "cohort": "Cohort B",
            "attributed_provider_id": "PR-2",
            "risk_category": "Low",
        },
    ]
    tables["providers.csv"] = [
        {"provider_id": "PR-1", "provider_name": "Provider One"},
        {"provider_id": "PR-2", "provider_name": "Provider Two"},
    ]
    tables["encounters.csv"] = [
        {
            "encounter_id": "E-1",
            "patient_id": "P-1",
            "provider_id": "PR-1",
            "date": "2026-10-20",
        }
    ]
    tables["measure_results.csv"] = [
        {
            "result_id": f"R-{index}",
            "patient_id": "P-1",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "state": state,
            "reason": "",
        }
        for index, state in enumerate(
            ["Met", "Met", "Met", "Not Met", "Not Eligible"]
        )
    ]
    tables["care_gaps.csv"] = [
        {
            "gap_id": "G-1",
            "patient_id": "P-1",
            "measure_id": "M-1",
            "gap_type": "Clinical Opportunity",
            "priority": "High",
            "owner": "Care Manager",
            "due_date": "2026-10-21",
            "status": "Detected",
        },
        {
            "gap_id": "G-2",
            "patient_id": "P-1",
            "measure_id": "M-1",
            "gap_type": "Outcome Management",
            "priority": "Medium",
            "owner": "Care Manager",
            "due_date": "2026-10-30",
            "status": "Verified",
        },
    ]
    tables["risk_scores.csv"] = [
        {
            "patient_id": "P-1",
            "risk_score": "80",
            "risk_category": "High",
            "contributing_factors": '["condition"]',
        },
        {
            "patient_id": "P-2",
            "risk_score": "10",
            "risk_category": "Low",
            "contributing_factors": "",
        },
    ]
    tables["documents.csv"] = [
        {
            "document_id": "D-1",
            "patient_id": "P-1",
            "document_date": "2026-10-20",
            "verification_status": "Verified",
        },
        {
            "document_id": "D-2",
            "patient_id": "P-1",
            "document_date": "2026-10-21",
            "verification_status": "Pending Verification",
        },
    ]
    tables["observations.csv"] = [
        {
            "observation_id": "O-1",
            "patient_id": "P-1",
            "type": "Blood Pressure",
            "value": "120/80",
            "unit": "mmHg",
            "date": "2026-10-20",
        },
        {
            "observation_id": "O-2",
            "patient_id": "P-1",
            "type": "Blood Pressure",
            "value": "150/95",
            "unit": "mmHg",
            "date": "2026-10-20",
        },
        {
            "observation_id": "O-3",
            "patient_id": "P-2",
            "type": "Blood Pressure",
            "value": "850/40",
            "unit": "mmHg",
            "date": "2026-10-20",
        },
        {
            "observation_id": "O-4",
            "patient_id": "P-2",
            "type": "HbA1c",
            "value": "7.2",
            "unit": "%",
            "date": "",
        },
        {
            "observation_id": "O-5",
            "patient_id": "P-2",
            "type": "HbA1c",
            "value": "7.2",
            "unit": "%",
            "date": "",
        },
    ]
    tables["claims.csv"] = [
        {
            "claim_id": "C-1",
            "patient_id": "P-1",
            "service_date": "2026-10-18",
        }
    ]
    tables["outreach.csv"] = [
        {"outreach_id": "O-1", "patient_id": "P-1", "date": "2026-10-19"}
    ]
    return tables


def _result(monkeypatch, tables=None, summary=None, **filters):
    tables = tables or _minimal_tables()
    _memory_repository(monkeypatch, tables, summary)
    return dashboard.get_population_dashboard(**filters)


def test_dashboard_has_expected_sections_and_real_archive_totals():
    repository = VbcDatasetRepository()
    result = dashboard.get_population_dashboard()

    assert set(result) == {
        "metadata",
        "active_filters",
        "kpis",
        "cohort_distribution",
        "risk_distribution",
        "measure_performance",
        "gap_distribution",
        "priority_worklist",
        "data_quality_summary",
        "freshness_summary",
        "measure_history",
        "population_segment_distribution",
        "provider_distribution",
        "outreach_disposition_distribution",
        "warnings",
    }
    assert result["kpis"]["total_patients"] == 20
    assert result["cohort_distribution"] == {
        "Diabetes Controlled": 5,
        "Diabetes Uncontrolled": 5,
        "Low Risk Preventive": 3,
        "Preventive Screening Due": 4,
        "Recently Discharged": 3,
    }
    assert sum(result["risk_distribution"].values()) == 20
    assert result["kpis"]["open_actionable_care_gaps"] == sum(
        1
        for gap in repository.get_care_gaps()
        if gap["status"].casefold() not in {"verified", "closed"}
    )
    assert result["kpis"]["high_risk_patients"] == sum(
        row["risk_category"].casefold() == "high"
        for row in repository.get_risk_scores()
    )
    assert len(result["measure_performance"]) == 4
    assert result["metadata"]["measure_label"] == "HEDIS-inspired POC measures"
    assert result["metadata"]["official_reporting"] is False


def test_measure_exclusions_and_80_percent_ceiling_goal(monkeypatch):
    tables = _minimal_tables()
    result = _result(monkeypatch, tables)
    measure = result["measure_performance"][0]

    assert measure["eligible_count"] == 4
    assert measure["met_count"] == 3
    assert measure["poc_target_rate"] == 80.0
    assert measure["required_met_count"] == 4
    assert measure["gaps_to_goal"] == 1


def test_eligibility_source_controls_denominator_and_preserves_unknown_measures(
    monkeypatch,
):
    tables = _minimal_tables()
    tables["measure_results.csv"] = [
        {
            "patient_id": "P-1",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "state": "Met",
        },
        {
            "patient_id": "P-2",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "state": "Met",
        },
        {
            "patient_id": "P-1",
            "measure_id": "ZZ-NEW",
            "measure_name": "Future Measure",
            "state": "Not Met",
        },
    ]
    tables["measure_eligibility.csv"] = [
        {
            "patient_id": "P-1",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "initial_population": "Yes",
            "eligible": "Yes",
            "denominator_exclusion": "No",
        },
        {
            "patient_id": "P-2",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "initial_population": "Yes",
            "eligible": "No",
            "denominator_exclusion": "Yes",
        },
        {
            "patient_id": "P-1",
            "measure_id": "ZZ-NEW",
            "measure_name": "Future Measure",
            "initial_population": "Yes",
            "eligible": "Yes",
            "denominator_exclusion": "No",
        },
    ]

    result = _result(monkeypatch, tables)
    measures = {
        row["measure_id"]: row for row in result["measure_performance"]
    }

    assert list(measures) == ["M-1", "ZZ-NEW"]
    assert measures["M-1"]["initial_population_count"] == 2
    assert measures["M-1"]["eligible_count"] == 1
    assert measures["M-1"]["excluded_count"] == 1
    assert measures["M-1"]["met_count"] == 1
    assert measures["ZZ-NEW"]["eligible_count"] == 1


def test_eligibility_result_mismatch_is_warned_without_dropping_denominator(
    monkeypatch,
):
    tables = _minimal_tables()
    tables["measure_results.csv"] = [
        {
            "patient_id": "P-1",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "state": "Met",
        }
    ]
    tables["measure_eligibility.csv"] = [
        {
            "patient_id": patient_id,
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "initial_population": "Yes",
            "eligible": "Yes",
            "denominator_exclusion": "No",
        }
        for patient_id in ("P-1", "P-2")
    ]

    result = _result(monkeypatch, tables)

    assert result["measure_performance"][0]["eligible_count"] == 2
    assert result["measure_performance"][0]["met_count"] == 1
    assert result["kpis"]["eligible_evaluations"] == 2
    assert result["kpis"]["quality_performance_rate"] == 50.0
    assert any("1 missing result" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    ("states", "expected_eligible"),
    [
        (["Not Eligible"], 0),
        (["Excluded"], 0),
        (["Denominator Not Met"], 0),
        (["Met", "Not Eligible", "Excluded", "Denominator Not Met"], 1),
    ],
)
def test_eligible_denominator_excludes_all_noneligible_states(
    monkeypatch, states, expected_eligible
):
    tables = _minimal_tables()
    tables["measure_results.csv"] = [
        {
            "patient_id": "P-1",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "state": state,
        }
        for state in states
    ]

    result = _result(monkeypatch, tables)
    assert result["measure_performance"][0]["eligible_count"] == expected_eligible


def test_zero_eligible_population_is_safe_and_warned(monkeypatch):
    tables = _minimal_tables()
    tables["measure_results.csv"] = [
        {
            "patient_id": "P-1",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "state": "Not Eligible",
        }
    ]
    tables["documents.csv"] = []

    result = _result(monkeypatch, tables)

    assert result["kpis"]["quality_performance_rate"] == 0.0
    assert result["kpis"]["evidence_completeness_rate"] == 100.0
    assert result["measure_performance"][0]["current_performance_rate"] == 0.0
    assert result["measure_performance"][0]["status"] == "No Eligible Population"
    assert result["measure_performance"][0]["gaps_to_goal"] == 0
    assert result["warnings"]


@pytest.mark.parametrize(
    ("rate", "eligible", "expected"),
    [(80, 5, "On Track"), (70, 10, "Close"), (60, 10, "Watch"), (59.9, 10, "At Risk")],
)
def test_poc_measure_status_thresholds(rate, eligible, expected):
    assert dashboard._measure_status(rate, eligible) == expected


def test_gaps_to_goal_never_negative(monkeypatch):
    tables = _minimal_tables()
    tables["measure_results.csv"] = [
        {
            "patient_id": "P-1",
            "measure_id": "M-1",
            "measure_name": "Measure One",
            "state": "Met",
        }
        for _ in range(10)
    ]

    measure = _result(monkeypatch, tables)["measure_performance"][0]
    assert measure["required_met_count"] == 8
    assert measure["gaps_to_goal"] == 0


def test_exact_population_filters(monkeypatch):
    tables = _minimal_tables()
    assert _result(monkeypatch, copy.deepcopy(tables), cohort="Cohort A")[
        "kpis"
    ]["total_patients"] == 1
    assert _result(monkeypatch, copy.deepcopy(tables), provider_id="PR-2")[
        "kpis"
    ]["total_patients"] == 1
    assert _result(monkeypatch, copy.deepcopy(tables), risk_category="High")[
        "kpis"
    ]["total_patients"] == 1
    measure_result = _result(
        monkeypatch, copy.deepcopy(tables), measure_id="M-1"
    )
    assert [row["measure_id"] for row in measure_result["measure_performance"]] == [
        "M-1"
    ]
    assert _result(
        monkeypatch, copy.deepcopy(tables), gap_type="Outcome Management"
    )["kpis"]["open_actionable_care_gaps"] == 0
    assert _result(monkeypatch, copy.deepcopy(tables), owner="Care Manager")[
        "kpis"
    ]["open_actionable_care_gaps"] == 1
    assert _result(monkeypatch, copy.deepcopy(tables), priority="High")["kpis"][
        "open_actionable_care_gaps"
    ] == 1
    assert _result(monkeypatch, copy.deepcopy(tables), status="Verified")[
        "kpis"
    ]["open_actionable_care_gaps"] == 0


def test_unknown_filter_values_return_valid_empty_results(monkeypatch):
    result = _result(
        monkeypatch,
        cohort="unknown cohort",
        provider_id="unknown provider",
        risk_category="unknown risk",
        measure_id="unknown measure",
        gap_type="unknown gap",
        priority="unknown priority",
        owner="unknown owner",
        status="unknown status",
    )

    assert result["kpis"]["total_patients"] == 0
    assert result["kpis"]["eligible_evaluations"] == 0
    assert result["kpis"]["open_actionable_care_gaps"] == 0
    assert result["priority_worklist"] == []
    assert result["measure_performance"] == []


def test_worklist_is_aggregated_and_masks_direct_identifiers(monkeypatch):
    result = _result(monkeypatch)
    rows = result["priority_worklist"]

    assert len(rows) == len({row["patient_id"] for row in rows}) == 1
    worklist_row = rows[0]
    assert worklist_row["patient_id"] == "P-1"
    assert worklist_row["masked_display_name"] == "A. M****"
    assert worklist_row["measure_names"] == ["Measure One"]
    assert not {
        "first_name",
        "last_name",
        "dob",
        "phone",
        "email",
        "address",
    }.intersection(worklist_row)
    assert "Ada" not in worklist_row["masked_display_name"]
    assert "Miller" not in worklist_row["masked_display_name"]


@pytest.mark.parametrize(
    "contributing_factors",
    ["not-json", "", '["factor"]', '{"factor": "not-a-list"}'],
)
def test_malformed_or_supported_contributing_factors_are_safe(
    monkeypatch, contributing_factors
):
    tables = _minimal_tables()
    tables["risk_scores.csv"][0]["contributing_factors"] = contributing_factors
    result = _result(monkeypatch, tables)

    assert isinstance(result["priority_worklist"][0]["contributing_factors"], list)
    if contributing_factors in {"not-json", '{"factor": "not-a-list"}'}:
        assert any("contributing_factors" in warning for warning in result["warnings"])


def test_overdue_gaps_exclude_terminal_and_invalid_dates(monkeypatch):
    tables = _minimal_tables()
    tables["care_gaps.csv"] = [
        {
            "gap_id": "G-1",
            "patient_id": "P-1",
            "measure_id": "M-1",
            "gap_type": "Gap",
            "priority": "High",
            "owner": "Owner",
            "due_date": "2026-10-09",
            "status": "Detected",
        },
        {
            "gap_id": "G-2",
            "patient_id": "P-1",
            "measure_id": "M-1",
            "gap_type": "Gap",
            "priority": "Low",
            "owner": "Owner",
            "due_date": "2026-10-09",
            "status": "Verified",
        },
        {
            "gap_id": "G-3",
            "patient_id": "P-1",
            "measure_id": "M-1",
            "gap_type": "Gap",
            "priority": "Low",
            "owner": "Owner",
            "due_date": "bad-date",
            "status": "Detected",
        },
        {
            "gap_id": "G-4",
            "patient_id": "P-1",
            "measure_id": "M-1",
            "gap_type": "Gap",
            "priority": "Low",
            "owner": "Owner",
            "due_date": "",
            "status": "Closed",
        },
    ]
    result = _result(monkeypatch, tables, as_of_date="2026-10-10")

    assert result["kpis"]["open_actionable_care_gaps"] == 2
    assert result["data_quality_summary"]["malformed_due_dates"] == 2
    worklist_row = result["priority_worklist"][0]
    assert worklist_row["care_gap_count"] == 4
    assert worklist_row["overdue_gap_count"] == 1
    assert any("due date" in warning for warning in result["warnings"])


def test_freshness_statuses_and_future_dates(monkeypatch):
    tables = _minimal_tables()
    tables["encounters.csv"][0]["date"] = "2026-10-09"
    tables["observations.csv"] = [
        {"patient_id": "P-1", "date": "2026-09-01"},
        {"patient_id": "P-1", "date": "not-a-date"},
        {"patient_id": "P-1", "date": ""},
    ]
    tables["claims.csv"][0]["service_date"] = "2026-09-01"
    tables["documents.csv"][0]["document_date"] = "2026-10-11"
    tables["outreach.csv"][0]["date"] = "2026-10-10"
    result = _result(monkeypatch, tables, as_of_date="2026-10-10")
    freshness = {row["source_name"]: row for row in result["freshness_summary"]}

    assert freshness["encounters"]["status"] == "Current"
    assert freshness["observations"]["status"] == "Stale"
    assert freshness["observations"]["invalid_date_count"] == 1
    assert freshness["observations"]["missing_date_count"] == 1
    assert freshness["claims"]["status"] == "Stale"
    assert freshness["documents"]["status"] == "Future Dated"
    assert freshness["outreach"]["status"] == "Current"


def test_invalid_and_conflicting_bp_and_duplicate_labs_are_detected(monkeypatch):
    result = _result(monkeypatch)
    quality = result["data_quality_summary"]

    assert quality["invalid_bp_values"] == 1
    assert quality["conflicting_same_day_bp_readings"] == 1
    assert quality["duplicate_laboratory_results"] == 1
    assert quality["missing_observation_dates"] == 2


def test_pending_documents_and_missing_dob_are_counted(monkeypatch):
    result = _result(monkeypatch)
    quality = result["data_quality_summary"]

    assert quality["pending_document_verification"] == 1
    assert quality["documents_not_verified"] == 1
    assert quality["missing_dob"] == 1
    assert result["kpis"]["evidence_completeness_rate"] == 50.0


def test_fallback_as_of_uses_latest_dataset_date_not_clock(monkeypatch):
    tables = _minimal_tables()
    _memory_repository(monkeypatch, tables, summary={})

    result = dashboard.get_population_dashboard()

    assert result["metadata"]["as_of_date"] == "2026-10-30"
    assert any("latest valid date" in warning for warning in result["warnings"])


def test_last_resort_clock_is_warned(monkeypatch):
    tables = _empty_tables()
    _memory_repository(monkeypatch, tables, summary={})

    result = dashboard.get_population_dashboard()

    assert result["metadata"]["as_of_date"] == date.today().isoformat()
    assert any("machine date" in warning for warning in result["warnings"])


def test_invalid_target_rate_uses_default_and_warns(monkeypatch):
    tables = _minimal_tables()
    monkeypatch.setenv("VBC_POC_TARGET_RATE", "101")

    result = _result(monkeypatch, tables)

    assert result["metadata"]["poc_target_rate"] == 80.0
    assert any("between 0 and 100" in warning for warning in result["warnings"])


def test_source_archive_is_not_extracted_or_modified(monkeypatch):
    archive_hash = hashlib.sha256(DEFAULT_DATASET_PATH.read_bytes()).digest()
    original_extract = zipfile.ZipFile.extract
    original_extractall = zipfile.ZipFile.extractall

    def reject_extract(*args, **kwargs):
        del args, kwargs
        raise AssertionError("ZIP members must not be extracted.")

    monkeypatch.setattr(zipfile.ZipFile, "extract", reject_extract)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", reject_extract)
    result = dashboard.get_population_dashboard()

    assert result["kpis"]["total_patients"] == 20
    assert hashlib.sha256(DEFAULT_DATASET_PATH.read_bytes()).digest() == archive_hash
    assert zipfile.ZipFile.extract is reject_extract
    assert zipfile.ZipFile.extractall is reject_extract
    assert original_extract is not None
    assert original_extractall is not None
    assert not (DEFAULT_DATASET_PATH.parent / "patients.csv").exists()


def test_source_csv_rows_are_not_mutated(monkeypatch):
    repository = VbcDatasetRepository()
    before = repository.get_patients()
    monkeypatch.setattr(dashboard, "VbcDatasetRepository", lambda: repository)

    dashboard.get_population_dashboard(cohort=before[0]["cohort"])

    assert repository.get_patients() == before
