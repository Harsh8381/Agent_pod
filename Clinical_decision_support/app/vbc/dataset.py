from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


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
OPTIONAL_CSV_HEADERS = {
    "measure_eligibility.csv": (
        "eligibility_id",
        "patient_id",
        "measure_id",
        "measure_name",
        "measurement_period_start",
        "measurement_period_end",
        "initial_population",
        "denominator_eligible",
        "denominator_exclusion",
        "exclusion_reason",
        "eligible",
        "eligibility_reason",
    ),
    "measure_history.csv": (
        "snapshot_date",
        "measure_id",
        "measure_name",
        "eligible_count",
        "met_count",
        "open_gap_count",
        "uncontrolled_count",
        "evidence_review_count",
        "data_quality_count",
        "completed_pending_verification_count",
        "performance_rate",
        "poc_target_rate",
        "projected_performance_rate",
        "status",
        "rule_version",
    ),
    "patient_cross_reference.csv": (
        "vbc_patient_id",
        "ehr_patient_id",
        "mapping_status",
        "mapping_method",
        "reviewed_by",
        "reviewed_date",
        "notes",
    ),
}
OPTIONAL_JSON_MEMBERS = ("dataset_manifest.json", "data_dictionary.json")
PREFERRED_MEASURE_ORDER = ("GSD", "BPD-E", "KED", "BCS-E", "COL-E", "TRC")

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


def get_dataset_path(path: str | Path | None = None) -> Path:
    """Resolve a configured dataset path relative to the project root."""
    configured_path: str | Path
    if path is not None:
        configured_path = path
    elif "VBC_DATASET_PATH" in os.environ:
        configured_path = os.environ["VBC_DATASET_PATH"]
    else:
        return DEFAULT_DATASET_PATH.resolve()

    raw_path = str(configured_path).strip()
    if not raw_path:
        raise VbcDatasetValidationError("VBC_DATASET_PATH must not be empty.")
    is_windows_drive = bool(re.match(r"^[A-Za-z]:[\\/]", raw_path))
    parsed_path = urlsplit(raw_path)
    if (parsed_path.scheme and not is_windows_drive) or "://" in raw_path:
        raise VbcDatasetValidationError(
            "VBC_DATASET_PATH must be a local filesystem path, not a URL."
        )
    resolved_path = Path(raw_path).expanduser()
    if not resolved_path.is_absolute():
        resolved_path = PROJECT_ROOT / resolved_path
    return resolved_path.resolve()


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
        self.archive_path = get_dataset_path(archive_path)
        self._tables, self._json_members, self._manifest, self._data_dictionary = (
            self._load_archive()
        )

    def _load_archive(
        self,
    ) -> tuple[
        dict[str, list[dict[str, str]]],
        dict[str, dict[str, Any]],
        dict[str, Any],
        dict[str, Any],
    ]:
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
                if not (
                    {"cohort", "population_segment"}
                    & set(_csv_headers(archive.read("patients.csv"), "patients.csv"))
                ):
                    raise VbcDatasetValidationError(
                        "patients.csv must contain cohort or population_segment."
                    )
                tables.update(
                    {
                        name: _read_csv_member(
                            name, archive.read(name), headers
                        )
                        for name, headers in OPTIONAL_CSV_HEADERS.items()
                        if name in names
                    }
                )
                json_members = {
                    name: _read_json_member(name, archive.read(name))
                    for name in JSON_MEMBERS
                }
                optional_json = {
                    name: _read_json_member(name, archive.read(name))
                    for name in OPTIONAL_JSON_MEMBERS
                    if name in names
                }
        except VbcDatasetValidationError:
            raise
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise VbcDatasetValidationError(
                f"VBC dataset ZIP could not be read: {self.archive_path}"
            ) from error

        return (
            tables,
            json_members,
            optional_json.get("dataset_manifest.json", {}),
            optional_json.get("data_dictionary.json", {}),
        )

    def _known_patient(self, patient_id: Any) -> bool:
        return isinstance(patient_id, str) and any(
            row["patient_id"] == patient_id for row in self._tables["patients.csv"]
        )

    def _records(
        self, member_name: str, patient_id: str | None = None
    ) -> list[dict[str, str]]:
        records = self._tables.get(member_name, [])
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

    def get_dataset_metadata(self) -> dict[str, Any]:
        manifest = self._manifest
        summary = self.get_population_summary()
        measure_ids = self.list_supported_measures()
        patients = self._tables["patients.csv"]
        is_legacy = "measure_eligibility.csv" not in self._tables
        return {
            "dataset_name": manifest.get("dataset_name")
            or "Synthetic VBC population dataset",
            "dataset_version": manifest.get("dataset_version")
            or ("legacy-1" if is_legacy else "unknown"),
            "schema_version": manifest.get("schema_version")
            or ("legacy" if is_legacy else "unknown"),
            "rule_version": manifest.get("rule_version")
            or summary.get("rule_version")
            or "unknown",
            "patient_count": len(patients),
            "measure_count": len(measure_ids),
            "supported_features": self.get_dataset_capabilities(),
            "dataset_path": str(self.archive_path),
            "synthetic_data_notice": manifest.get("synthetic_data_notice")
            or summary.get("synthetic_data_notice")
            or (
                "Synthetic VBC POC data. HEDIS-inspired measures. Not for "
                "clinical care or official quality reporting."
            ),
            "as_of_date": manifest.get("as_of_date") or summary.get("as_of_date"),
            "measurement_period_start": manifest.get("measurement_period_start")
            or summary.get("measurement_period_start"),
            "measurement_period_end": manifest.get("measurement_period_end")
            or summary.get("measurement_period_end"),
            "is_legacy_dataset": is_legacy,
            "has_measure_eligibility": "measure_eligibility.csv" in self._tables,
            "has_measure_history": "measure_history.csv" in self._tables,
            "has_cross_reference": "patient_cross_reference.csv" in self._tables,
            "has_manifest": bool(self._manifest),
            "has_data_dictionary": bool(self._data_dictionary),
        }

    def get_dataset_capabilities(self) -> dict[str, bool]:
        return {
            "measure_eligibility": "measure_eligibility.csv" in self._tables,
            "measure_history": "measure_history.csv" in self._tables,
            "patient_cross_reference": "patient_cross_reference.csv" in self._tables,
            "manifest": bool(self._manifest),
            "data_dictionary": bool(self._data_dictionary),
        }

    def get_population_field(self, patient: dict[str, str]) -> str:
        """Return a display-compatible population segment without changing source data."""
        segment = str(patient.get("population_segment") or "").strip()
        if segment:
            return segment
        legacy_cohort = str(patient.get("cohort") or "").strip()
        return {
            "Diabetes Controlled": "Diabetes",
            "Diabetes Uncontrolled": "Diabetes",
            "Preventive Screening Due": "Adult Preventive",
            "Recently Discharged": "Post-Discharge",
            "Low Risk Preventive": "General Adult",
        }.get(legacy_cohort, legacy_cohort or "Unknown")

    def list_population_segments(self) -> list[str]:
        return sorted(
            {self.get_population_field(row) for row in self._tables["patients.csv"]},
            key=str.casefold,
        )

    def get_measure_eligibility(
        self, patient_id: str | None = None, measure_id: str | None = None
    ) -> list[dict[str, str]]:
        rows = self._records("measure_eligibility.csv", patient_id)
        if measure_id is not None:
            rows = [row for row in rows if row.get("measure_id") == measure_id]
        return rows

    def get_measure_history(
        self, measure_id: str | None = None
    ) -> list[dict[str, str]]:
        rows = self._records("measure_history.csv")
        if measure_id is not None:
            rows = [row for row in rows if row.get("measure_id") == measure_id]
        return rows

    def get_patient_cross_reference(
        self, patient_id: str | None = None
    ) -> list[dict[str, str]]:
        rows = self._records("patient_cross_reference.csv")
        if patient_id is not None:
            rows = [
                row for row in rows if row.get("vbc_patient_id") == patient_id
            ]
        return rows

    def get_dataset_manifest(self) -> dict[str, Any]:
        return copy.deepcopy(self._manifest)

    def get_data_dictionary(self) -> dict[str, Any]:
        return copy.deepcopy(self._data_dictionary)

    def list_supported_measures(self) -> list[str]:
        rows = self._tables["measure_results.csv"] + self._tables.get(
            "measure_eligibility.csv", []
        )
        identifiers = {row["measure_id"] for row in rows if row.get("measure_id")}
        return sorted(
            identifiers,
            key=lambda identifier: (
                PREFERRED_MEASURE_ORDER.index(identifier)
                if identifier in PREFERRED_MEASURE_ORDER
                else len(PREFERRED_MEASURE_ORDER),
                identifier,
            ),
        )

    def list_patients(self) -> list[dict[str, str]]:
        return self.get_patients()

    def get_patient(self, patient_id: str) -> dict[str, str] | None:
        rows = self.get_patients(patient_id)
        return rows[0] if rows else None

    def get_provider(self, provider_id: str) -> dict[str, str] | None:
        return next(
            (
                copy.deepcopy(row)
                for row in self._tables["providers.csv"]
                if row.get("provider_id") == provider_id
            ),
            None,
        )

    def get_risk_score(self, patient_id: str) -> dict[str, str] | None:
        rows = self.get_risk_scores(patient_id)
        return rows[0] if rows else None

    def get_patient_records(
        self, dataset_name: str, patient_id: str
    ) -> list[dict[str, str]]:
        member_name = dataset_name if dataset_name.endswith(".csv") else f"{dataset_name}.csv"
        if member_name not in self._tables:
            raise VbcDatasetValidationError(
                f"Unsupported VBC dataset record type: {dataset_name}"
            )
        return self._records(member_name, patient_id)


def _csv_headers(content: bytes, member_name: str) -> list[str]:
    try:
        text = content.decode("utf-8-sig")
        headers = next(csv.reader(io.StringIO(text, newline=""), strict=True), None)
    except (UnicodeDecodeError, csv.Error) as error:
        raise VbcDatasetValidationError(
            f"CSV member is malformed: {member_name}"
        ) from error
    return headers or []
