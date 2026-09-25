"""Stable ICD-10 coding surface preserved from the original matcher."""

# The active ICD implementation remains untouched in code_matcher.py so its
# thresholds, cache, parsing, family filters, and output behavior stay stable.
from code_matcher import (  # noqa: F401
    get_icd10_candidate_sets,
    get_icd10_codes,
)

__all__ = ["get_icd10_candidate_sets", "get_icd10_codes"]
