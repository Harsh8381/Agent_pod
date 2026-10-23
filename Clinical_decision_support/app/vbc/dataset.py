from __future__ import annotations

import copy
import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = PROJECT_ROOT / "data" / "synthetic_vbc_dataset.zip"

CSV_MEMBERS = (
    "patients.csv",
    "providers.csv",
    "encounters.csv",
    "conditions.csv",
    "observations.csv",
    "procedures.csv",
    "claims.csv",
    "documents.csv",
    "measure_results.csv",
    "care_gaps.csv",
    "risk_scores.csv",
    "care_plans.csv",
    "outreach.csv",
    "audit_trail.csv",
)
JSON_MEMBERS = ("patient_360_sample.json", "population_dashboard_summary.json")
REQUIRED_MEMBERS = CSV_MEMBERS + JSON_MEMBERS

REQUIRED_CSV_HEADERS = {
    "patients.csv": (
        "patient_id",
        "first_name",
        "last_name",
        "dob",
        "age",
        "sex",
        "phone",
        "email",
        "address",
        "coverage_start",
        "coverage_end",
        "attributed_provider_id",
        "risk_category",
        "cohort",
    ),
    "providers.csv": ("provider_id", "provider_name", "specialty", "npi"),
    "encounters.csv": (
        "encounter_id",
        "patient_id",
        "encounter_type",
        "provider_id",
        "facility",
        "date",
        "disposition",
    ),
    "conditions.csv": (
        "condition_id",
        "patient_id",
        "icd10_code",
        "description",
        "onset_date",
        "status",
    ),
    "observations.csv": (
        "observation_id",
        "patient_id",
        "type",
        "value",
        "unit",
        "date",
    ),
    "procedures.csv": (
        "procedure_id",
        "patient_id",
        "procedure_code",
        "description",
        "service_date",
        "status",
    ),
    "claims.csv": (
        "claim_id",
        "patient_id",
        "claim_type",
        "service_date",
        "primary_dx",
        "procedure_code",
        "status",
    ),
    "documents.csv": (
        "document_id",
        "patient_id",
        "document_type",
        "document_date",
        "linked_encounter",
        "verification_status",
    ),
    "measure_results.csv": (
        "result_id",
        "patient_id",
        "measure_id",
        "measure_name",
        "state",
        "reason",
    ),
    "care_gaps.csv": (
        "gap_id",
        "patient_id",
        "measure_id",
        "gap_type",
        "priority",
        "owner",
        "due_date",
        "status",
    ),
    "risk_scores.csv": (
        "patient_id",
        "risk_score",
        "risk_category",
        "contributing_factors",
    ),
    "care_plans.csv": (
        "plan_id",
        "patient_id",
        "goal",
        "recommended_action",
        "owner",
        "target_date",
    ),
    "outreach.csv": (
        "outreach_id",
        "patient_id",
        "channel",
        "date",
        "disposition",
    ),
    "audit_trail.csv": (
        "audit_id",
        "patient_id",
        "entity_type",
        "entity_id",
        "action",
        "old_status",
        "new_status",
        "event_timestamp",
        "actor",
    ),
}


class VbcDatasetError(Exception):
    """Base exception for VBC dataset loading and validation errors."""


class VbcDatasetNotFoundError(VbcDatasetError):
    """Raised when the configured dataset ZIP is unavailable."""


class VbcDatasetValidationError(VbcDatasetError):
    """Raised when the ZIP contents do not match the required dataset format."""


def _read_csv_member(
    member_name: str,
    content: bytes,
    required_headers: tuple[str, ...],
) -> list[dict[str, str]]:
    if not content.strip():
        raise VbcDatasetValidationError(
            f"Required CSV member is empty: {member_name}"
        )

    try:
        text = content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        headers = next(reader, None)
        if not headers or any(not header.strip() for header in headers):
            raise VbcDatasetValidationError(
                f"CSV member has an empty or invalid header: {member_name}"
            )
        if len(headers) != len(set(headers)):
            raise VbcDatasetValidationError(
                f"CSV member has duplicate headers: {member_name}"
            )

        missing_headers = set(required_headers).difference(headers)
        if missing_headers:
            missing = ", ".join(sorted(missing_headers))
            raise VbcDatasetValidationError(
                f"CSV member is missing required headers ({missing}): {member_name}"
            )

        records = []
        for line_number, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(headers):
                raise VbcDatasetValidationError(
                    f"CSV member has an invalid row width at line {line_number}: "
                    f"{member_name}"
                )
            records.append(dict(zip(headers, row)))
        return records
    except (UnicodeDecodeError, csv.Error) as error:
        raise VbcDatasetValidationError(
            f"CSV member is malformed: {member_name}"
        ) from error
    except StopIteration as error:
        raise VbcDatasetValidationError(
            f"CSV member has no header: {member_name}"
        ) from error


def _read_json_member(member_name: str, content: bytes) -> dict[str, Any]:
    if not content.strip():
        raise VbcDatasetValidationError(
            f"Required JSON member is empty: {member_name}"
        )
    try:
        value = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VbcDatasetValidationError(
            f"JSON member is malformed: {member_name}"
        ) from error
    if not isinstance(value, dict):
        raise VbcDatasetValidationError(
            f"JSON member must contain an object: {member_name}"
        )
    return value


class VbcDatasetRepository:
    """Load VBC CSV/JSON data directly from the ZIP without extracting it.

    A patient filter uses exact patient_id equality. Unknown or non-string
    patient IDs return an empty list rather than broadening the result.
    """

    def __init__(self, archive_path: str | Path | None = None) -> None:
        self.archive_path = (
            Path(archive_path) if archive_path is not None else DEFAULT_DATASET_PATH
        )
        self._tables, self._json_members = self._load_archive()

    def _load_archive(
        self,
    ) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, Any]]]:
        if not self.archive_path.is_file():
            raise VbcDatasetNotFoundError(
                f"VBC dataset ZIP was not found: {self.archive_path}"
            )

        try:
            with zipfile.ZipFile(self.archive_path, mode="r") as archive:
                names = archive.namelist()
                missing = sorted(set(REQUIRED_MEMBERS).difference(names))
                if missing:
                    raise VbcDatasetValidationError(
                        "VBC dataset ZIP is missing required members: "
                        + ", ".join(missing)
                    )
                duplicates = sorted(
                    name for name in REQUIRED_MEMBERS if names.count(name) > 1
                )
                if duplicates:
                    raise VbcDatasetValidationError(
                        "VBC dataset ZIP has duplicate required members: "
                        + ", ".join(duplicates)
                    )

                tables = {
                    name: _read_csv_member(
                        name,
                        archive.read(name),
                        REQUIRED_CSV_HEADERS[name],
                    )
                    for name in CSV_MEMBERS
                }
                json_members = {
                    name: _read_json_member(name, archive.read(name))
                    for name in JSON_MEMBERS
                }
        except VbcDatasetValidationError:
            raise
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise VbcDatasetValidationError(
                f"VBC dataset ZIP could not be read: {self.archive_path}"
            ) from error

        return tables, json_members

    def _known_patient(self, patient_id: Any) -> bool:
        return isinstance(patient_id, str) and any(
            row["patient_id"] == patient_id for row in self._tables["patients.csv"]
        )

    def _records(
        self, member_name: str, patient_id: str | None = None
    ) -> list[dict[str, str]]:
        records = self._tables[member_name]
        if patient_id is None:
            return copy.deepcopy(records)
        if not self._known_patient(patient_id):
            return []
        return copy.deepcopy(
            [row for row in records if row.get("patient_id") == patient_id]
        )

    def get_patients(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("patients.csv", patient_id)

    def get_providers(self, patient_id: str | None = None) -> list[dict[str, str]]:
        if patient_id is None:
            return self._records("providers.csv")
        if not self._known_patient(patient_id):
            return []

        provider_ids = {
            row["attributed_provider_id"]
            for row in self._tables["patients.csv"]
            if row["patient_id"] == patient_id and row.get("attributed_provider_id")
        }
        provider_ids.update(
            row["provider_id"]
            for row in self._tables["encounters.csv"]
            if row.get("patient_id") == patient_id and row.get("provider_id")
        )
        return copy.deepcopy(
            [
                row
                for row in self._tables["providers.csv"]
                if row.get("provider_id") in provider_ids
            ]
        )

    def get_encounters(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("encounters.csv", patient_id)

    def get_conditions(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("conditions.csv", patient_id)

    def get_observations(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("observations.csv", patient_id)

    def get_procedures(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("procedures.csv", patient_id)

    def get_claims(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("claims.csv", patient_id)

    def get_documents(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("documents.csv", patient_id)

    def get_measure_results(
        self, patient_id: str | None = None
    ) -> list[dict[str, str]]:
        return self._records("measure_results.csv", patient_id)

    def get_care_gaps(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("care_gaps.csv", patient_id)

    def get_risk_scores(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("risk_scores.csv", patient_id)

    def get_care_plans(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("care_plans.csv", patient_id)

    def get_outreach(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("outreach.csv", patient_id)

    def get_audit_trail(self, patient_id: str | None = None) -> list[dict[str, str]]:
        return self._records("audit_trail.csv", patient_id)

    def get_population_summary(self) -> dict[str, Any]:
        return copy.deepcopy(self._json_members["population_dashboard_summary.json"])
