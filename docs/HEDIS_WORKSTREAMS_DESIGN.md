# HEDIS engine — workstream design

This document covers the work breakdown below. For each L3 activity it gives the design, the rules that make it safe, how to check it works, and where it is built.

| L1 workstream | L2 capability | L3 activity | Built in |
|---|---|---|---|
| Evidence and quality | Evidence normalizer | Design cross-source evidence normalization | `app/hedis/normalizer.py` · page *Evidence normalizer* |
| | Result comparison | Design supplied-versus-calculated QA comparison | `app/hedis/calculator.py`, `app/hedis/qa.py` · page *Supplied vs calculated QA* |
| Care gap management | Writable workflow, workflow specialization | Design review-to-closure workflow | `app/hedis/workflow.py` · page *Review-to-closure workflow* |
| Governance | Measure versioning | Design HEDIS registry and version control center | `app/hedis/registry.py`, `app/hedis/versions.py` · pages *HEDIS registry*, *Version control center* |
| | Payer reconciliation | Design payer-versus-internal result comparison | `app/hedis/reconciliation.py` · page *Payer reconciliation* |
| | Supplemental submission | Design supplemental evidence submission | `app/hedis/supplemental.py` · page *Supplemental submission* |

**Shared foundations.** Every write goes through one file-locked store with a hash-chained audit trail (`app/hedis/store.py`). Every person-facing action is checked against the role matrix that the User Access page edits, and every API route needs a signed token plus a named permission (`app/security/`).

**Data.** The evidence, QA, workflow and version-control activities run on the 300-member HEDIS-aligned extract (`data/vbc_300/`, which can be overridden with `HEDIS_SOURCE_DATASET`). The registry, reconciliation and supplemental activities run on the quality-loop measure engine.

---

## L1 · Evidence and quality

### L2 · Evidence normalizer — L3 · Design cross-source evidence normalization

**Problem.** The same clinical event reaches us several times, in different shapes:
- an HbA1c arrives as a lab observation, a CPT procedure, a claim line and a scanned report;
- blood pressure arrives as the text `132/84`;
- FIT results arrive as free text.

Counting these as they come either double-counts events or misses them.

**Design.** Five stages, applied to every feed: observations, procedures, claims, conditions, encounters, documents, plus evidence captured in the care-gap workflow.

1. **Map.** Each source code maps to a canonical concept and a standard code: LOINC for results, CPT/HCPCS for services, ICD-10-CM for diagnoses. Reason-for-encounter Z codes (Z00.00, Z12.11) are *recognised but never evidence*: a screening reason code doesn't prove the screening happened.
2. **Parse.** Values go into canonical form:
   - blood pressure → systolic and diastolic;
   - HbA1c in mmol/mol → % using the NGSP master equation (flagged `UNIT_CONVERTED`);
   - FIT text → positive or negative.
3. **Check.** Every problem becomes a flag; nothing is silently dropped. Records flagged `MISSING_DATE`, `FUTURE_DATE`, `INVALID_VALUE`, `IMPLAUSIBLE`, `NOT_COMPLETED` or `CLAIM_DENIED` are kept and shown but never counted. Members with no date of birth get their age from the enrollment file and the flag `DOB_MISSING_AGE_FROM_ENROLLMENT`.
4. **Merge.** Records in one concept family, for one member, on one date, become one *golden record*.
   - The record that carries the result wins. Order of precedence: lab observation → workflow capture → procedure → claim → encounter → document.
   - Reports attach to their service up to 14 days later and pass on their verification status.
   - Two *different* same-day results stay separate, so the measure's own same-day rule decides.
   - Every golden record keeps the lineage of every source row behind it.
5. **Report.** For each source the run reports the mapping rate, unmapped codes, flags and merges, so normalization itself is auditable (readiness control IS1-3).

**Results on the extract.** 9,797 source records map at 100% into 4,383 golden records, with 5,414 cross-source merges. The checks catch 4 undated HbA1c results, 3 implausible blood pressures, 73 denied claims, 1 external mammogram pending verification and 5 members without a date of birth.

**Acceptance.**
- 100% of records map, or every unmapped code is listed.
- No flagged record contributes to a numerator.
- Every golden record traces back to its source rows.
- A unit conversion is applied and flagged.

---

### L2 · Result comparison — L3 · Design supplied-versus-calculated QA comparison

**Problem.** The extract comes with measure results the supplier already computed. Reporting them unchecked would publish someone else's errors.

**Design.**
- **Recalculate.** Every (member, measure) result is recalculated from the normalized evidence with the *active measure version*. The calculator interprets a versioned specification; it has no thresholds in code.
- **Classify.** Each pair is classified as:
  - `AGREE`;
  - `DENOMINATOR_MISMATCH`: eligibility differs;
  - `SUPPLIED_UNSUPPORTED`: supplied compliant, but the evidence doesn't support it;
  - `SUPPLIED_UNDERCOUNT`: the evidence is compliant, but the supplied result isn't;
  - `STATUS_DIFFERENCE`: both non-compliant, for different reasons.
- **Explain.** Each difference shows the supplier's reason, the calculated reason and the evidence used, with its source rows.
- **Tolerance.** A measure passes when state agreement is at least 95% and its rate differs by no more than 2 points.
- **Sign-off.** A run is recorded with the dataset hash and the measure version. A *different person* signs it off. A measure outside tolerance can only be **rejected back to the supplier**, never accepted.

**Findings on the extract.** 79.1% overall agreement; 2 of 6 measures within tolerance.

| Measure | Supplied | Calculated | Main difference |
|---|---|---|---|
| GSD, BPD-E | 50.0% | 50.0% | Within tolerance |
| KED | 62.5% | 0.0% | 75 supplied "Met" results: no eGFR or uACR exists in any feed |
| BCS-E | 7.7% | 80.3% | 70 women with a verified mammogram supplied as open |
| COL-E | 24.5% | 81.2% | 155 members with a negative FIT this period supplied as open |
| TRC | 76.0% | 46.0% | 22 supplied "Met" results with no follow-up visit within 30 days |

**Acceptance.**
- Every supplied result is compared.
- Every difference carries both reasons and the evidence.
- The runner can't sign off their own run.
- An out-of-tolerance measure can't be accepted (readiness control IS7-5).

---

## L1 · Care gap management

### L2 · Writable workflow, workflow specialization — L3 · Design review-to-closure workflow

**Problem.** The extract's 505 care gaps were read-only, and a single generic workflow doesn't fit three different kinds of gap.

**Design.** Each gap type has its own workflow definition: states, transitions, the role allowed to make each move, required fields and service-level targets (SLAs).

| Specialization | Path | Special rules |
|---|---|---|
| **Clinical Opportunity**: service not done | Detected → Reviewed → Assigned → Outreach → Scheduled → Completed → Verified → Closed | Each outreach attempt is counted |
| **Outcome Management**: result out of target | Detected → Reviewed → Assigned → Clinical review → Plan updated → Follow-up scheduled → Recheck resulted → Verified → Closed | Only a Physician/APP records the plan; an uncontrolled recheck loops back to clinical review |
| **Documentation Reconciliation**: record missing | Detected → Reviewed → Assigned → Record requested → Record received → Abstracted → Verified → Closed | Only abstractors abstract; verification is primary-source verification by a second person |

**Rules shared by all three:**
- **Evidence capture.** Completion evidence (result value, service date, document reference) is non-standard supplemental data. It counts only after a second person verifies it, and then it flows through the evidence normalizer like any other source.
- **Closure.** A gap closes only when the measure, *recalculated with that verified evidence*, is compliant. The system refuses otherwise and states the reason, for example "Recalculated as Outcome Not Controlled: latest HbA1c 8.9%".
- **Dispositions.** Declined, unable to contact (needs three documented attempts) or exclusion candidate. These need Approve permission and written documentation.
- **Timing.** Time in each state is measured against that state's SLA, and late gaps are flagged.
- **People only.** Agents can't move gaps, and every transition goes on the audit trail.

**Acceptance.**
- An invalid move is refused.
- A role-restricted move is refused for other roles.
- The capturer can't verify their own evidence.
- An uncontrolled recheck can't close the gap.
- A KED gap with only eGFR can't close (uACR is missing).
- An "unable to contact" closure without three attempts is refused (readiness control IS4-3).

---

## L1 · Governance

### L2 · Measure versioning — L3 · Design HEDIS registry and version control center

**Registry.**
- **One row per (period, member, measure),** rebuilt from the latest versioned measure result and never edited by hand. Each row records denominator, exclusion and compliance status, the accepted records behind the numerator, and their NCQA source class (administrative, standard supplemental, non-standard supplemental).
- **Snapshots** freeze the registry with a SHA-256 content hash and a diff against the previous snapshot.
- **The period lock** needs a second person (publisher ≠ locker), no drift since the snapshot, and no blocking integrity issues.

**Version control center.** Measure logic is a *versioned specification*: thresholds, age bands, look-backs and same-day rules are data.
- **Lifecycle:** `DRAFT → IN_REVIEW → APPROVED → ACTIVE → SUPERSEDED` (or `REJECTED`).
- **Validated changes:** each change names a governed parameter, and its type and range are checked, so `GSD.numerator.target_lt = 30` is refused.
- **Impact analysis:** the candidate and active versions run over the same normalized evidence, giving the rate change per measure and every member whose result changes. For example, moving the GSD target from 8.0 to 9.0 turns a set of *Outcome Not Controlled* results into *Met*.
- **Four eyes:** the author can't approve. Approval needs a current impact analysis against the version that's active now.
- **Activation and rollback:** activation carries an effective date and supersedes the old version. Rollback re-activates a superseded version and needs a written reason.
- **Traceability:** every QA run and calculation is stamped with the version that produced it. The quality-loop engine's code-level rule history is shown alongside.

**Acceptance.**
- No version goes live without an impact analysis and a second person's approval (readiness control HD-4).
- Rollback restores the earlier specification exactly.

### L2 · Payer reconciliation — L3 · Design payer-versus-internal result comparison

**Design.**
- **Input.** The payer's member-level file (CSV: member, measure, payer status, numerator source, last service date) is checked for columns, payer member-ID format, known measures, valid statuses and duplicates.
- **Classification.** Each (member, measure) pair is one of: agree, compliant internally only, compliant at the payer only, exclusion mismatch, denominator mismatch, not attributed by the payer, or unknown member.
- **Root cause.** Each difference is traced through the supplemental ledger: never submitted, in an unsigned draft, awaiting acknowledgement, rejected (with the payer's reason), or accepted but not applied. It also gets an owner role, an action and its effect on the payer-reported rate ("payer rate if resolved").
- **Disputes** are approval-gated and opened only for causes the payer must fix (accepted record not applied, claim not applied, attribution, eligibility). Internal causes become routed tasks.

**Acceptance.**
- Agreement rises after supplemental submission.
- Internal root causes can't be disputed.
- Agents can draft disputes but can't open them.

### L2 · Supplemental submission — L3 · Design supplemental evidence submission

**Design.**
- **Records.** Numerators supported only by data the payer can't see in claims become supplemental records.
- **Validation** against the payer's file spec: member-ID format, service date inside the measure window, allowed code systems, result values for outcome measures, NPI check digit, and primary-source verification for non-standard data. A warning is raised if the NPI isn't on the payer's roster.
- **Idempotency key.** A key built from payer, member, measure, code, date and value means the same evidence is never in flight twice. Accepted records are never resent.
- **Batch lifecycle:** `DRAFT → SIGNED → SUBMITTED → ACKNOWLEDGED | PARTIAL | REJECTED`. The signer can't be the builder, and agents never sign or transmit.
- **Payer files** are pipe-delimited (HDR/DTL/TRL) or CSV, with control totals (record count and SHA-256 over the detail lines) and a signature checked again before transmission.
- **Acknowledgements** are ingested per record; records that aren't in the batch are refused. Rejected records are corrected (for example, billed under the group NPI on the payer's roster) and resubmitted, linked to the rejection.

**Acceptance.**
- Builder ≠ signer.
- File totals match the detail lines.
- A forged acknowledgement is refused.
- Corrected rejections are resubmitted and accepted (readiness controls IS5-1 to IS5-4).

---

## How the workstreams connect

```text
source feeds ─┐
workflow      ├─► evidence normalizer ─► calculator (active version) ─► supplied-vs-calculated QA ─► sign-off
captures ─────┘          ▲                        │
                         │                        └─► care-gap closure guard (review-to-closure workflow)
       verified evidence ┘
version control center ─► active specification ─► calculator · impact analysis · QA stamp
measure engine ─► HEDIS registry ─► supplemental submission ─► payer acknowledgement
                         └────────────► payer reconciliation ◄─ payer member file
everything ─► hash-chained audit ─► NCQA readiness controls (HD, IS 1.0–7.0, SEC)
```

## Limits

- The measures are HEDIS-inspired specifications, not licensed NCQA specifications. Readiness control HD-1 fails until licensed specs are loaded.
- Payer member files and acknowledgements are simulated from the registry. Real layouts come from each payer's companion guide.
- State lives in a locked JSON file, which suits a pilot. A multi-site rollout needs a database behind the same store interface.
