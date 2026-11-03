"""Source dataset for the population workstreams: the 300-member HEDIS-aligned synthetic extract.

Read-only access to each source table, cached per file version. HEDIS_SOURCE_DATASET overrides the path.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import zipfile
from datetime import date
from functools import lru_cache
from pathlib import Path

DATASET_ENV = "HEDIS_SOURCE_DATASET"
DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "vbc_300" / "synthetic_vbc_dataset_300_hedis.zip"
TABLES = ("patients", "providers", "encounters", "conditions", "observations", "procedures", "claims", "documents",
          "measure_eligibility", "measure_results", "care_gaps")


def dataset_path() -> Path:
    return Path(os.environ.get(DATASET_ENV) or DEFAULT_DATASET)


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    with zipfile.ZipFile(path) as archive:
        tables ={name: list(csv.DictReader(io.StringIO(archive.read(f"{name}.csv").decode("utf-8")))) for name in TABLES}
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return {"tables": tables, "sha256": digest}


def dataset() -> dict:
    path = dataset_path()
    if not path.is_file():
        from app.hedis.errors import NotFound

        raise NotFound(f"Source dataset not found: {path}")
    return _load(str(path), path.stat().st_mtime)


def table(name: str) -> list[dict]:
    return dataset()["tables"][name]


def dataset_sha256() -> str:
    return dataset()["sha256"]


def period() -> tuple[date, date]:
    """Measurement period as supplied with the extract."""
    first = table("measure_results")[0]
    return date.fromisoformat(first["measurement_period_start"]), date.fromisoformat(first["measurement_period_end"])
