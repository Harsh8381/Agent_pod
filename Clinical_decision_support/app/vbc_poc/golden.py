"""Golden patient expectations: the baseline result every patient-measure pair must produce.

Used by the unit tests and shown live on the governance page, so a rule change that alters any
expected outcome is visible immediately.
"""

from __future__ import annotations

from datetime import date

from app.vbc_poc import engine
from app.vbc_poc.dataset import EVENTS, PATIENTS

GOLDEN_AS_OF = date(2026, 10, 26)
NE, DNM, EX, MET = "NOT_ELIGIBLE", "DENOMINATOR_NOT_MET", "EXCLUDED", "MET"
OPEN, NC, REV, DQ, FU = "OPEN_GAP", "OUTCOME_NOT_CONTROLLED", "EVIDENCE_REVIEW", "DATA_QUALITY_ISSUE", "FOLLOW_UP_REQUIRED"

# patient: (GSD-P, BPD-P, BCS-P, COL-P), scenario under test
EXPECTED = {
    "VBC-001": ((MET, MET, MET, MET), "Duplicate lab + claim HbA1c counted once; all current"),
    "VBC-002": ((MET, MET, DQ, MET), "Impossible 850/40 excluded; mammogram with mismatched DOB"),
    "VBC-003": ((MET, MET, NE, MET), "Same-day BP lowest-reading rule; future-dated HbA1c rejected"),
    "VBC-004": ((DNM, DNM, DNM, DNM), "75-day enrollment gap"),
    "VBC-005": ((MET, MET, NE, MET), "Device-transmitted home BP; sDNA-FIT within 3 years"),
    "VBC-006": ((OPEN, NC, NE, OPEN), "Outdated HbA1c; BP 152/94; no colorectal screening"),
    "VBC-007": ((NC, MET, OPEN, OPEN), "HbA1c 9.4; ultrasound is not mammography; incomplete colonoscopy"),
    "VBC-008": ((DQ, DQ, NE, MET), "HbA1c without date; systolic-only BP"),
    "VBC-009": ((REV, NC, REV, FU), "External HbA1c pending; narrative mammogram; positive FIT"),
    "VBC-010": ((REV, MET, NE, REV), "Conflicting same-day HbA1c; three same-day BPs; outside colonoscopy pending"),
    "VBC-011": ((NE, NE, OPEN, OPEN), "No mammogram; colonoscopy ordered, not completed"),
    "VBC-012": ((NE, NE, OPEN, MET), "Mammogram ordered only; duplicate colonoscopy records"),
    "VBC-013": ((NE, NE, NE, REV), "Colonoscopy only in narrative"),
    "VBC-014": ((NE, NE, EX, OPEN), "Bilateral mastectomy exclusion; colonoscopy outside 10-year lookback"),
    "VBC-015": ((NC, OPEN, NE, MET), "Inpatient-only BP readings; HbA1c 8.6"),
    "VBC-016": ((NE, NE, MET, MET), "High utilization, no quality gaps"),
    "VBC-017": ((EX, EX, NE, EX), "Hospice exclusion"),
    "VBC-018": ((NE, NE, MET, MET), "Low-risk, all current"),
    "VBC-019": ((NE, NE, NE, MET), "Negative FIT this period"),
    "VBC-020": ((NE, NE, NE, NE), "Not eligible for any measure"),
}
MEASURE_ORDER = ("GSD-P", "BPD-P", "BCS-P", "COL-P")


def run_golden(as_of: date = GOLDEN_AS_OF) -> list[dict]:
    """Evaluate the untouched synthetic dataset and compare with the expectations."""
    rows = []
    for patient in PATIENTS:
        expected, scenario = EXPECTED[patient.patient_id]
        results = engine.evaluate_patient(patient, [e for e in EVENTS if e.patient_id == patient.patient_id], as_of, "golden")
        for code, want in zip(MEASURE_ORDER, expected, strict=True):
            got = results[code]["state"]
            rows.append({"patient_id": patient.patient_id, "measure_id": code, "expected": want, "actual": got, "passed": got == want,
                         "scenario": scenario})
    return rows
