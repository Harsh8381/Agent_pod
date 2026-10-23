import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from app.vbc.dataset import (
    CSV_MEMBERS,
    DEFAULT_DATASET_PATH,
    JSON_MEMBERS,
    REQUIRED_CSV_HEADERS,
    REQUIRED_MEMBERS,
    VbcDatasetNotFoundError,
    VbcDatasetRepository,
    VbcDatasetValidationError,
)


def _archive_bytes() -> dict[str, bytes]:
    with zipfile.ZipFile(DEFAULT_DATASET_PATH, mode="r") as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }


def _write_test_archive(
    directory: Path,
    members: dict[str, bytes] | None = None,
    *,
    archive_name: str = "synthetic_vbc_dataset.zip",
) -> Path:
    if members is None:
        members = _archive_bytes()
    path = directory / archive_name
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def test_default_archive_path_is_project_root_based():
    assert DEFAULT_DATASET_PATH == (
        Path(__file__).resolve().parents[2]
        / "data"
        / "synthetic_vbc_dataset.zip"
    )


def test_archive_contains_all_required_members_and_expected_row_counts():
    repository = VbcDatasetRepository()

    assert set(REQUIRED_MEMBERS) == set(CSV_MEMBERS + JSON_MEMBERS)
    assert len(repository.get_patients()) == 20
    assert len(repository.get_providers()) == 9
    assert len(repository.get_encounters()) == 94
    assert len(repository.get_measure_results()) == 80
    assert len(repository.get_care_gaps()) == 16
    assert repository.get_population_summary()["population_size"] == 20


def test_cohort_distribution_is_exact():
    repository = VbcDatasetRepository()
    distribution = Counter(
        patient["cohort"] for patient in repository.get_patients()
    )

    assert distribution == {
        "Diabetes Controlled": 5,
        "Diabetes Uncontrolled": 5,
        "Low Risk Preventive": 3,
        "Preventive Screening Due": 4,
        "Recently Discharged": 3,
    }


def test_all_patient_references_resolve():
    repository = VbcDatasetRepository()
    patient_ids = {patient["patient_id"] for patient in repository.get_patients()}
    encounter_ids = {
        encounter["encounter_id"] for encounter in repository.get_encounters()
    }

    for member_name in CSV_MEMBERS:
        if member_name in {"patients.csv", "providers.csv"}:
            continue
        getter_name = f"get_{member_name.removesuffix('.csv')}"
        getter = getattr(repository, getter_name)
        assert all(
            record.get("patient_id") in patient_ids
            for record in getter()
        ), member_name

    assert all(
        document["linked_encounter"] in encounter_ids
        for document in repository.get_documents()
        if document["linked_encounter"]
    )


def test_patient_filter_uses_exact_ids_and_unknown_ids_return_no_records():
    repository = VbcDatasetRepository()
    patient = repository.get_patients()[0]
    patient_id = patient["patient_id"]

    assert repository.get_patients(patient_id) == [patient]
    assert all(
        record["patient_id"] == patient_id
        for record in repository.get_encounters(patient_id)
    )
    assert repository.get_patients(f" {patient_id}") == []
    assert repository.get_encounters("unknown-patient-id") == []
    assert repository.get_providers("unknown-patient-id") == []
    assert repository.get_measure_results("unknown-patient-id") == []


def test_unknown_patient_id_returns_empty_lists_for_all_patient_scoped_data():
    repository = VbcDatasetRepository()
    unknown_id = "not-a-known-patient"
    getters = (
        repository.get_patients,
        repository.get_providers,
        repository.get_encounters,
        repository.get_conditions,
        repository.get_observations,
        repository.get_procedures,
        repository.get_claims,
        repository.get_documents,
        repository.get_measure_results,
        repository.get_care_gaps,
        repository.get_risk_scores,
        repository.get_care_plans,
        repository.get_outreach,
        repository.get_audit_trail,
    )

    assert all(getter(unknown_id) == [] for getter in getters)


def test_returned_rows_do_not_mutate_repository_data():
    repository = VbcDatasetRepository()
    rows = repository.get_patients()
    rows[0]["cohort"] = "changed"

    assert repository.get_patients()[0]["cohort"] != "changed"


def test_zip_is_neither_extracted_nor_modified(monkeypatch):
    archive_path = DEFAULT_DATASET_PATH
    before_hash = hashlib.sha256(archive_path.read_bytes()).digest()

    def extraction_is_forbidden(*args, **kwargs):
        argument_count = len(args) + len(kwargs)
        raise AssertionError(
            f"Dataset loading must not extract ZIP members ({argument_count})."
        )

    monkeypatch.setattr(zipfile.ZipFile, "extract", extraction_is_forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", extraction_is_forbidden)
    repository = VbcDatasetRepository()

    assert len(repository.get_patients()) == 20
    assert hashlib.sha256(archive_path.read_bytes()).digest() == before_hash


def test_missing_zip_raises_clear_error(tmp_path):
    with pytest.raises(VbcDatasetNotFoundError, match="was not found"):
        VbcDatasetRepository(tmp_path / "missing.zip")


def test_missing_required_member_is_reported(tmp_path):
    members = _archive_bytes()
    members.pop("claims.csv")
    archive_path = _write_test_archive(tmp_path, members)

    with pytest.raises(VbcDatasetValidationError, match="claims.csv"):
        VbcDatasetRepository(archive_path)


@pytest.mark.parametrize("member_name", CSV_MEMBERS)
def test_required_csv_headers_are_validated(tmp_path, member_name):
    members = _archive_bytes()
    headers = REQUIRED_CSV_HEADERS[member_name]
    members[member_name] = (
        ",".join(headers[:-1]) + "\n"
    ).encode("utf-8")
    archive_path = _write_test_archive(tmp_path, members)

    with pytest.raises(VbcDatasetValidationError, match="required headers"):
        VbcDatasetRepository(archive_path)


@pytest.mark.parametrize(
    ("member_name", "empty_content"),
    [
        ("patients.csv", b""),
        ("population_dashboard_summary.json", b" \n"),
    ],
)
def test_empty_members_are_rejected(tmp_path, member_name, empty_content):
    members = _archive_bytes()
    members[member_name] = empty_content
    archive_path = _write_test_archive(tmp_path, members)

    with pytest.raises(VbcDatasetValidationError, match="empty"):
        VbcDatasetRepository(archive_path)


@pytest.mark.parametrize(
    ("member_name", "malformed_content"),
    [
        ("population_dashboard_summary.json", b'{"invalid":'),
        ("patient_360_sample.json", b"\xff"),
    ],
)
def test_malformed_json_members_are_rejected(
    tmp_path, member_name, malformed_content
):
    members = _archive_bytes()
    members[member_name] = malformed_content
    archive_path = _write_test_archive(tmp_path, members)

    with pytest.raises(VbcDatasetValidationError):
        VbcDatasetRepository(archive_path)


def test_malformed_csv_is_rejected(tmp_path):
    members = _archive_bytes()
    headers = ",".join(REQUIRED_CSV_HEADERS["patients.csv"])
    members["patients.csv"] = f'{headers}\n"unterminated'.encode("utf-8")
    archive_path = _write_test_archive(tmp_path, members)

    with pytest.raises(VbcDatasetValidationError, match="malformed"):
        VbcDatasetRepository(archive_path)


def test_csv_with_inconsistent_row_width_is_rejected(tmp_path):
    members = _archive_bytes()
    headers = ",".join(REQUIRED_CSV_HEADERS["patients.csv"])
    row = ",".join("value" for _ in REQUIRED_CSV_HEADERS["patients.csv"])
    members["patients.csv"] = f"{headers}\n{row},extra\n".encode("utf-8")
    archive_path = _write_test_archive(tmp_path, members)

    with pytest.raises(VbcDatasetValidationError, match="row width"):
        VbcDatasetRepository(archive_path)


def test_json_member_must_contain_an_object(tmp_path):
    members = _archive_bytes()
    members["population_dashboard_summary.json"] = json.dumps([]).encode("utf-8")
    archive_path = _write_test_archive(tmp_path, members)

    with pytest.raises(VbcDatasetValidationError, match="must contain an object"):
        VbcDatasetRepository(archive_path)


def test_non_zip_file_is_reported_as_invalid(tmp_path):
    path = tmp_path / "not-a-zip.zip"
    path.write_bytes(b"not a zip archive")

    with pytest.raises(VbcDatasetValidationError, match="could not be read"):
        VbcDatasetRepository(path)
