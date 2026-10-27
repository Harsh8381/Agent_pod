# NuuCare — Clinical Decision & Value-Based Care Platform

NuuCare is a full-stack healthcare prototype. It follows a patient visit from the conversation in the exam room to a priced bill and a draft insurance claim, and it tracks population quality for value-based care contracts (HEDIS / Medicare Stars).

It combines an **AI ambient scribe**, **retrieval-augmented clinical decision support (RAG)**, **automated medical coding (ICD-10-CM, CPT/HCPCS)**, **hospital chargemaster billing**, **837P/837I claim drafting with validation**, and a **HEDIS care-gap engine**, all behind a role-based, audited web application.

> **Disclaimer.** This is an educational proof of concept built on synthetic data. It is not a medical device. It must not be used for real patient care, coding, or billing.

---

## Contents

1. [The problem it solves](#1-the-problem-it-solves)
2. [What it does — end to end](#2-what-it-does--end-to-end)
3. [Feature tour](#3-feature-tour)
4. [Architecture](#4-architecture)
5. [Technology stack](#5-technology-stack)
6. [How the billing pipeline works](#6-how-the-billing-pipeline-works)
7. [Value-based care engine](#7-value-based-care-engine)
8. [Security and access control](#8-security-and-access-control)
9. [Data](#9-data)
10. [Project structure](#10-project-structure)
11. [Running it locally](#11-running-it-locally)
12. [Configuration](#12-configuration)
13. [Testing](#13-testing)
14. [API reference](#14-api-reference)
15. [Limitations](#15-limitations)
16. [Project highlights for a CV](#16-project-highlights-for-a-cv)

---

## 1. The problem it solves

After every visit, a clinic has to:

1. **Document** the visit as a SOAP note. Physicians spend hours a day on this.
2. **Code** it: map diagnoses to ICD-10-CM and services to CPT/HCPCS codes.
3. **Bill** it: price each service from the hospital's chargemaster.
4. **Claim** it: build an 837P (professional) or 837I (facility) claim that passes payer edits.
5. **Close quality gaps**: for value-based contracts, prove that each eligible patient got the screenings and tests that HEDIS measures require.

Each hand-off is a source of lost revenue and compliance risk. A test that was only *recommended* but gets billed is over-billing. A test that was *done* but never coded is lost revenue.

NuuCare automates the chain and keeps a human approving every step that matters.

## 2. What it does — end to end

```text
Patient visit (voice)
   │  browser microphone → speech-to-text
   ▼
Ambient scribe (LLM) ──► SOAP note, summary, highlights
   │
   ▼
Clinical decision support (RAG over a medical textbook) ──► cited recommendations
   │  clinician reviews and signs the note
   ▼
Medical coding ──► ICD-10-CM + CPT/HCPCS suggestions, each linked to evidence in the note
   │  coder accepts / rejects; pre- and post-coding validation
   ▼
Billing guardrail ──► only services documented as PERFORMED are billable
   │  priced from the hospital chargemaster (Excel / JSON)
   ▼
Claim generation ──► draft 837P / 837I, supporting-document checks, claim scrubbing score
```

A second workspace, **Value-Based Care**, evaluates HEDIS measures across a patient population. It opens care gaps, ranks them, drives outreach, and closes a gap only when accepted evidence arrives.

## 3. Feature tour

The web app has two workspaces, switched from the top bar.

### Clinical workspace

| Page | What it does |
|---|---|
| **Clinical Overview** | KPIs, activity chart and the consultation queue. |
| **Consultations** | Pick a patient from the synthetic EHR (or enter one), then record the visit in the browser. The audio is transcribed. |
| **Clinical Review & CDS** | An LLM drafts the SOAP note. A RAG agent retrieves evidence from the knowledge base and writes cited recommendations. The clinician edits, accepts or rejects them, and signs the note. |
| **Medication & RTBC** | Active medications, allergy cross-checks and coverage context (a real-time benefit check view). |
| **Prior Authorization** | Prior-authorization queue for services that need payer approval. |
| **Coding & HCC** | ICD-10-CM and CPT/HCPCS suggestions with the matching excerpt from the note, HCC (V28) flags, coder accept / reject / query decisions, and pre- and post-coding validation scores. |
| **Billing & RCM** | The performed-service guardrail, a draft bill priced from the chargemaster, held lines with their prices, coder override, attestation, and hand-off to claims. |
| **IPD Billing** | Inpatient bills built from a billing workbook: arithmetic, duplicate and total checks, payer / patient shares, PDF export. |
| **Claim Generation** | Draft 837P and 837I claims (outpatient visits split into professional and facility parts), supporting-document repository checks, and a validation / scrubbing report (sample NCCI, MUE and LCD rules). Downloads as JSON or text. |
| **Knowledge Base** | Search the RAG index directly. |
| **EHR Records** | Browse and edit the synthetic EHR (patients, encounters, coverage, labs, claims) through the API. |
| **Oversight** | Every patient's status across documentation, coding and billing. |
| **User Access** | Accounts, role-permission matrix, out-of-office fallback coverage, and the audit log. |

**OCR.** Scanned lab reports, insurance cards and paper superbills can be uploaded. Text is extracted locally with RapidOCR, and codes on a superbill are pulled straight into the coding workspace.

### Value-based care workspace

| Area | Screens |
|---|---|
| **Command** | Stars dashboard with projected star ratings, measure explorer, measure-version center, operating playbook. |
| **Work the gaps** | Gap workbench with explainable ranking, point-of-care card, person profile, outreach console. |
| **Prove it** | Evidence panel (eight deterministic evidence checks), exclusion review, supplemental data, payer reconciliation, chart abstraction. |
| **Care transitions** | Transitions of care, plan all-cause readmission risk, follow-up after ED visit. |
| **Value & trust** | Survey insights, utilization and value, governance with a hash-chained audit log. |
| **Quality loop** | A longitudinal proof of concept: population dashboard, Patient 360, care-gap worklist from detection to closure, governance, and golden tests. |

HEDIS measures covered: **GSD** (glycemic status), **CBP** (blood pressure control), **KED** (kidney evaluation), **EED** (eye exam), **BCS-E** (breast cancer screening), **COL-E** (colorectal screening), **SPD** (statin therapy), **COA-FSA**, **AIS-E** (immunizations), **TRC** (transitions of care), **FMC** (follow-up after ED visit), **PCR** (readmissions).

## 4. Architecture

```mermaid
flowchart LR
    User[Clinician / Coder / Analyst] --> UI[Streamlit web app<br/>role-based pages]
    UI -->|HTTP| API[FastAPI backend]
    UI --> OCR[Local OCR<br/>RapidOCR / ONNX]
    UI --> Bill[Coding & billing engine<br/>cpt_coder, chargemaster, guardrail]
    Bill --> CDM[(Hospital chargemaster<br/>Excel / JSON)]
    API --> Scribe[Ambient scribe agent]
    API --> CDS[CDS agent]
    API --> Coding[/codes endpoint]
    API --> EHR[(Synthetic EHR<br/>JSON store)]
    API --> VBC[VBC quality-loop engine]
    Scribe --> LLM[LLM router<br/>Coforge]
    CDS --> RAG[Retriever]
    RAG --> KB[(Chroma vectors or<br/>TF-IDF index)]
    CDS --> LLM
    UI --> Claims[Claim builder + validator<br/>837P / 837I]
```

Design principles:

- **AI drafts, humans decide.** The LLM writes the note and recommendations, the coder accepts codes, and the biller attests to the bill. Nothing is billed or claimed automatically.
- **Deterministic where it matters.** Code matching, the performed-service guardrail, pricing, claim edits and HEDIS evaluation are rule-based, testable and explainable. The LLM is used only for language tasks.
- **Every decision is traceable.** Code suggestions carry evidence excerpts, guardrail decisions carry reasons, and overrides and permission changes are written to an audit log.
- **No fabricated numbers.** A service with no chargemaster price is shown as *Unpriced* and left out of the total; it is never guessed.

## 5. Technology stack

| Layer | Technologies |
|---|---|
| Frontend | Python, Streamlit, Altair, custom CSS design system |
| Backend | FastAPI, Uvicorn, Pydantic |
| AI / LLM | Coforge LLM router (chat completions) for the scribe and CDS |
| Retrieval (RAG) | ChromaDB + Sentence-Transformers (`all-MiniLM-L6-v2`), offline TF-IDF fallback (scikit-learn), python-docx loader, overlapping chunking |
| Speech | SpeechRecognition (Google Web Speech) |
| OCR | RapidOCR on ONNX Runtime, pypdfium2, Pillow, all local |
| Coding | rapidfuzz fuzzy matching over ICD-10-CM 2022 (44,811 codes) and CPT/HCPCS references; rule-based performed-status classifier |
| Data | pandas, openpyxl (Excel chargemaster and EHR workbooks), JSON stores |
| Documents | ReportLab (PDF bills) |
| Security | PBKDF2-SHA256 password hashing, role-based access control, lockout, audit log |
| Testing | pytest, Streamlit AppTest: **504 automated tests** |

About 33,000 lines of Python across the backend, frontend and tests.

## 6. How the billing pipeline works

This is the core revenue-cycle logic.

**1. Candidate discovery.** The coder (`cpt_coder.py`) scans the note for every service in the hospital chargemaster. It understands the way clinicians actually write: "head CT", "EGD", "EKG", "CMP", "PT/INR", "mammogram", "level 4 office visit", "cefazolin given". Specific services take priority over generic mentions, so "CT head" is billed as 70450 and not also as a generic CT. Drugs map to HCPCS J-codes only when documented as administered, never from a home medication list.

**2. Performed-status classification.** Each mention is classified from the words around it:

| Status | Example | Billable? |
|---|---|---|
| performed | "HbA1c was 8.2%", "EGD performed", "cefazolin given" | Yes |
| recommended / planned | "I recommend an HbA1c test", "plan MRI next week" | No (held) |
| ordered / pending / declined | "stress test ordered, pending" | No (held) |
| historical | "colonoscopy 3 years ago" | No (held) |
| negated | "no chest X-ray today" | No (held) |

The classifier looks at a window of words around each mention, so a cue elsewhere in a long, unpunctuated voice transcript can't set the status.

**3. Pricing.** Billable codes are priced from the chargemaster workbook (`Hospital_IPD_OPD_Charges_Athena_Care_Gap.xlsx`, or the JSON version):
- the OPD sheet for outpatient visits and the IPD sheet for inpatient stays;
- each code is validated against the CDM Master (active status and effective dates);
- ambiguous or missing rates are reported, never guessed.

**4. Coder review.** The Billing page shows every coded service with its price: billable lines in the gross, held lines in a separate "held value". If a service really was done but the note doesn't say so, a coder can confirm it. That override is saved with their username and time, logged, and honored by post-coding validation.

**5. Claim.** After attestation, the bill goes to Claim Generation:
- outpatient bills split into a professional 837P and a facility 837I (with revenue codes);
- required supporting documents are checked against a document repository;
- a scrubbing report scores claim quality (0–100).

All 45 coded services in the chargemaster are covered by an automated test that reads the real workbook. If a service is added to the Excel without coder support, the test fails.

## 7. Value-based care engine

- **Measure engine.** A generic eleven-stage evaluation model: eligibility, denominator, exclusions, evidence normalization, de-duplication, validity, numerator and outcome. It is deterministic and fully traced.
- **Gap lifecycle.** Gaps are opened, updated, closed and reopened only by the engine after re-evaluation. People move the work forward (assign, outreach, schedule, attach evidence, verify). An order or outreach attempt never closes a gap; only accepted evidence does. Each patient, measure, period and line of work has exactly one gap.
- **Ten measure states:** `NOT_ELIGIBLE`, `DENOMINATOR_NOT_MET`, `EXCLUDED`, `MET`, `OPEN_GAP`, `OUTCOME_NOT_CONTROLLED`, `EVIDENCE_REVIEW`, `COMPLETED_PENDING_VERIFICATION`, `DATA_QUALITY_ISSUE`, `FOLLOW_UP_REQUIRED`.
- **Prioritization.** Transparent workload ranking. It is not a clinical prediction model, and every rank is explained.
- **Care plans and outreach.** Template-based drafts, each of which needs human approval before sending.
- **Metrics.** Compliance rates, before/after comparison, monthly trends and star projections.
- **Datasets.** A 20-patient, 12-month longitudinal dataset with deliberate edge cases and golden expected results, plus a configurable 300-patient HEDIS-aligned dataset (patients, encounters, conditions, observations, procedures, claims, documents, eligibility, results, gaps, risk scores, care plans, outreach, audit trail).
- **Audit.** An append-only, hash-chained audit log: each event stores the hash of the previous one, so tampering is detectable.

## 8. Security and access control

- **Accounts:** first-run bootstrap of a Super Admin, PBKDF2-SHA256 password hashes (never plain text), forced change of temporary passwords, lockout for 15 minutes after 5 failed sign-ins, and a session timeout.
- **Roles:** 14 roles. There are 5 clinical roles (Coding Specialist, RCM Analyst, Clinical Reviewer, Operations Admin, Super Admin) and 9 VBC roles (Physician/APP, Care Manager, HEDIS Analyst, HEDIS Lead, Abstractor, Pharmacist, Stars/Contract, Executive, Auditor).
- **Permissions:** a role × module matrix with *View / Edit / Approve / Admin* actions, enforced on every page and on sensitive buttons. For example, only roles with RCM *Approve* can release a claim.
- **Audit log:** sign-ins, password changes, permission edits, code decisions, billing overrides and claim releases.
- **Privacy:** OCR runs locally. Only transcript-derived text is sent to the LLM. All data in the repository is synthetic.

## 9. Data

All data is synthetic.

| File | Contents |
|---|---|
| `Hospital_IPD_OPD_Charges_Athena_Care_Gap.xlsx` / `.json` | Hospital chargemaster: OPD and IPD charge lines priced for two payers (Athena Health Insurance, Care Gap Health), CDM Master, payer contracts, claim edits, price transparency, router rules |
| `Epic_Inspired_USA_50_Patient_Demo.xlsx` / `.json` | Synthetic EHR: patients, encounters, coverage, problems, medications, allergies, labs, billing claims |
| `IPD_25_Patient_Billing_Demo.xlsx` | Inpatient billing encounters |
| `ICD10CM_2022_Codes.json`, `CPT_CODES.json` | Code reference data |
| `knowledge_base/knowledge_base_harrison.docx` | Medical knowledge source for RAG (about 6,500 chunks) |
| `data/vbc_300/synthetic_vbc_dataset_300_hedis.zip` | 300-patient HEDIS dataset |
| `clinical_records.json` | Consultations saved by the app (runtime, git-ignored) |
| `data/access_control.json` | Users, hashed passwords, role permissions, audit log (runtime) |

## 10. Project structure

```text
Clinical_decision_support/
├── app/                         FastAPI backend
│   ├── main.py, __main__.py     app factory; `python -m app` starts Uvicorn on :8000
│   ├── api/                     routes.py (scribe, CDS, RAG, coding, EHR, bills), vbc_routes.py
│   ├── agent/                   ambient scribe, CDS agent, encounter orchestrator, context model
│   ├── rag/                     Word/PDF loaders, chunker, embeddings, Chroma/TF-IDF store, retriever
│   ├── services/                EHR JSON store, transcription, SOAP helpers
│   ├── vbc/                     300-patient HEDIS dataset loader and population dashboard
│   ├── vbc_poc/                 quality-loop engine: catalog, engine, evidence, metrics, risk, service
│   ├── scripts/build_kb.py      builds the RAG index
│   └── tests/                   504 pytest tests
├── frontend/                    Streamlit app
│   ├── streamlit_app.py         entry point
│   ├── views/                   one module per page (+ views/vbc, views/vbc_poc)
│   ├── ui/                      theme, shell (navigation), components, session state
│   ├── encounter_pipeline.py    coding → guardrail → bill (no UI)
│   ├── chargemaster.py          pricing, CDM validation, revenue codes
│   ├── claim_generation_page.py 837P/837I claim drafting
│   ├── claim_validator.py       claim scrubbing / quality score
│   ├── billing_scrubber.py      over-coding and E/M level checks
│   ├── coding_validation.py     pre- and post-coding checks
│   ├── supporting_documents.py  document repository matching
│   ├── ocr_service.py           local OCR
│   ├── access_control.py        users, roles, permissions, audit
│   └── vbc/                     VBC catalog, engine, evidence feeds, stars, store
├── cpt_coder.py                 CPT/HCPCS coder and performed-status classifier
├── icd10_coder.py, code_matcher.py   ICD-10-CM matching
└── requirements.txt
```

## 11. Running it locally

**Prerequisites:** Python 3.10+ (developed on 3.12). Coforge LLM credentials are needed only for the scribe and CDS; coding, billing, claims and VBC work offline.

```powershell
cd Clinical_decision_support
python -m venv ..\.venv
..\.venv\Scripts\python -m pip install -r requirements.txt

# one-time: build the RAG index (TF-IDF needs no model download)
$env:EMBEDDING_BACKEND = "tfidf"
..\.venv\Scripts\python -m app.scripts.build_kb
```

Start the two services in separate terminals, both from `Clinical_decision_support/`:

```powershell
# Terminal 1 — backend on http://localhost:8000 (API docs at /docs)
..\.venv\Scripts\python -m app

# Terminal 2 — frontend on http://localhost:8501
..\.venv\Scripts\python -m streamlit run frontend/streamlit_app.py
```

On first launch the app asks you to create the Super Admin account.

**Try the billing flow.** Start a consultation and say something like *"HbA1c was 8.1 percent today, head CT was done, EKG obtained"*. Then go to **Coding & HCC → Billing & RCM**: each service appears priced from the chargemaster. Attest, send to RCM, and generate the claim.

## 12. Configuration

Create `Clinical_decision_support/.env` and never commit it.

| Variable | Purpose | Default |
|---|---|---|
| `COFORGE_API_KEY`, `COFORGE_API_URL`, `COFORGE_MODEL` | LLM for scribe and CDS | required for LLM features |
| `COFORGE_CPT_API_KEY`, `COFORGE_CPT_API_URL`, `COFORGE_CPT_MODEL` | Optional separate LLM for coding | falls back to the above |
| `EMBEDDING_BACKEND` | `auto`, `tfidf` or `sentence_transformer` | `auto` |
| `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE` | Embedding model and batch size | `all-MiniLM-L6-v2`, `64` |
| `RETRIEVAL_CANDIDATES`, `RETRIEVAL_RESULTS`, `MAX_CONTEXT_CHARS` | RAG tuning | `20`, `5`, `12000` |
| `CHARGEMASTER_PATH` | Chargemaster file (CSV, XLSX or JSON) | the Athena / Care Gap workbook |
| `NUUCARE_ENCOUNTER_TYPE` | Default chargemaster sheet (`OPD`/`IPD`) | `OPD` |
| `VBC_DATASET_PATH` | VBC dataset zip | bundled synthetic dataset |
| `VBC_POC_ENABLED`, `VBC_POC_TARGET_RATE` | Quality-loop feature flag and target compliance rate | see `app/vbc_poc` |

## 13. Testing

```powershell
cd Clinical_decision_support
..\.venv\Scripts\python -m pytest app/tests -q
```

The 504 tests cover:
- API routes and mocked agent flows, retrieval and the Word loader;
- the performed-status classifier and coding guardrails;
- chargemaster pricing against the real workbook, revenue codes, billing scrubbing;
- claim building and validation, supporting documents, IPD billing;
- EHR persistence, OCR parsing, access control;
- the VBC engines, including golden-patient expectations.

Real LLM calls, microphone capture and payer acceptance are not covered by automated tests.

## 14. API reference

Interactive docs: `http://localhost:8000/docs`.

| Area | Endpoints |
|---|---|
| Health | `GET /`, `GET /health`, `GET /dev/diagnostics` |
| Scribe & CDS | `POST /encounters/scribe`, `POST /encounters/cds`, `POST /encounters/analyze`, `POST /encounters/analyze-audio`, `POST /analyze` |
| RAG | `POST /retrieve` |
| Coding | `POST /codes` (ICD-10-CM + CPT/HCPCS from conditions, procedures and note text) |
| EHR | `GET/POST /ehr/patients`, `GET/PUT/DELETE /ehr/patients/{id}`, `GET /ehr/patients/{id}/chart`, records CRUD, `GET /ehr/export` |
| Bills | `GET /ehr/bills`, `POST /patients/{id}/bills/opd`, `POST /patients/{id}/bills/ipd` |
| VBC quality loop (`/vbc`) | `GET /meta`, `POST /evaluate/{patient_id}`, `POST /evaluate-population`, `GET /patients`, `GET /patients/{id}/360`, gaps, evidence verification, outreach (create / approve / send / outcome), care plans, `GET /dashboard`, `GET /audit`, `POST /reset` |

## 15. Limitations

- Proof of concept on synthetic data, with no clinical validation.
- Claims are draft JSON/TXT records, not standards-compliant X12 EDI, and are not submitted to payers. Generated claims live in the browser session.
- NCCI, MUE and LCD edits use small local sample rules, not payer rule sets.
- Prices come from the encounter's own chargemaster sheet. A code that exists only on the other sheet shows as *Unpriced*.
- Quantities default to 1. Time-based codes and drug units are not derived from the note.
- E/M level selection is not automated; a level must be documented.
- Speech recognition and the LLM need network access.
- `app/rules/rule_engine.py` exists but is not wired into CDS.
- No Docker or CI/CD configuration yet.

## 16. Project highlights for a CV

> **NuuCare — AI Clinical Documentation, Coding & Value-Based Care Platform** (Python, FastAPI, Streamlit, RAG, LLMs)
>
> - Built a full-stack healthcare platform that covers the patient encounter → SOAP note → ICD-10/CPT coding → chargemaster billing → 837P/837I claim pipeline, plus a HEDIS value-based care workspace (about 33k lines of Python, 504 automated tests).
> - Developed an LLM ambient scribe and a retrieval-augmented clinical decision support agent (ChromaDB + Sentence-Transformers with an offline TF-IDF fallback) that produce evidence-cited recommendations from a medical textbook.
> - Engineered a rule-based performed-status classifier and billing guardrail. It separates performed, recommended, ordered, historical and negated services in free-text notes, so only documented services are billed.
> - Mapped all 45 coded services in a hospital chargemaster (labs, imaging, cardiology, endoscopy, therapy, drugs, implants, E/M levels) to CPT/HCPCS codes from natural clinical phrasing, validated against the live workbook in tests.
> - Implemented claim drafting with professional/facility split, supporting-document validation and claim scrubbing with a 0–100 quality score.
> - Designed a deterministic HEDIS measure engine (GSD, CBP, BCS-E, COL-E, KED, EED, TRC, FMC, PCR and more) with a care-gap lifecycle, explainable prioritization, human-approved outreach and a hash-chained, tamper-evident audit log.
> - Secured the app with role-based access control (14 roles, View/Edit/Approve/Admin), PBKDF2 password hashing, account lockout and full audit logging. Added local, privacy-preserving OCR for scanned documents and superbills.

**Skills demonstrated:** LLM application design, RAG, NLP and rule-based clinical text classification, healthcare revenue-cycle domain knowledge (ICD-10, CPT/HCPCS, CDM, 837P/837I, NCCI), HEDIS / value-based care, REST API design, RBAC and security, data engineering with pandas and Excel, automated testing.
