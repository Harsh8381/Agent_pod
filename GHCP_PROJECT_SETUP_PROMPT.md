# GitHub Copilot Chat: Local Project Setup Prompt

Copy the prompt below into GitHub Copilot Chat after cloning this repository and opening the repository root folder in VS Code.

---

You are helping me set up this repository locally in VS Code. First read the root `README.md`; treat it as the primary project guide. The active application is under `Clinical_decision_support/`. Do not use nested snapshots or files with backup/alternate names as application entry points.

## Project context

This is a local clinical documentation and decision-support proof of concept:

- `Clinical_decision_support/frontend/streamlit_app.py` is the canonical Streamlit UI.
- `Clinical_decision_support/app/` contains the FastAPI backend, encounter agents, LLM client, EHR demo API, and retrieval/RAG code.
- The normal user workflow is browser audio capture, speech transcription, ambient SOAP generation, optional cited CDS, review, and local draft/finalization.
- The Codes workspace suggests ICD-10-CM and CPT/HCPCS codes, lets the user choose CPT/HCPCS lines for an OPD/IPD estimate, looks up chargemaster prices, and can create downloadable draft 837P (`SV1`) or 837I (`SV2`) EDI text.
- A separate IPD Billing page reads the inpatient demo workbook, validates charge lines, calculates estimates, and can export a PDF bill.
- The app uses synthetic demo EHR and billing workbooks. It is not a clinical production system and the draft EDI must not be submitted as-is.

## Setup and verification task

1. Inspect the README, `.gitignore`, and `Clinical_decision_support/requirements.txt`. Confirm the current Python version and available workspace interpreters; prefer Python 3.12 if available.
2. Use the repository's `.venv` at the root. If it does not exist, create it with the selected Python interpreter. Install dependencies using that environment's interpreter and the project's requirements file.
3. Never overwrite `.env`, workbooks, local records, runtime databases, or user changes. Do not read, print, or send credentials to chat or external services. If `Clinical_decision_support/.env` is missing, explain that I must create it locally and enter the required Coforge API settings from an approved secret source. The expected setting names are `COFORGE_API_KEY`, `COFORGE_API_URL`, and `COFORGE_MODEL`; optional code-model overrides are `COFORGE_CPT_API_KEY`, `COFORGE_CPT_API_URL`, and `COFORGE_CPT_MODEL`.
4. Run the automated tests from `Clinical_decision_support/` with the root virtual-environment interpreter:

   ```powershell
   & "..\.venv\Scripts\python.exe" -m pytest app\tests -q
   ```

5. If the local RAG artifacts are missing and CDS setup is requested, explain that building the index writes to `Clinical_decision_support/chroma_db/` and resets the local `medical_knowledge` collection. For an offline setup, use `EMBEDDING_BACKEND=tfidf` before running:

   ```powershell
   & "..\.venv\Scripts\python.exe" -m app.scripts.build_kb
   ```

   Do not rebuild an existing database unless I approve it.

6. Start the backend and frontend in separate VS Code terminals, both with the working directory `Clinical_decision_support/`:

   ```powershell
   & "..\.venv\Scripts\python.exe" -m app
   ```

   ```powershell
   & "..\.venv\Scripts\python.exe" -m streamlit run frontend\streamlit_app.py
   ```

7. Verify the API at `http://127.0.0.1:8000/health` and open the Streamlit URL printed in the terminal. The frontend uses the local backend at `http://127.0.0.1:8000`.
8. Do not change application behavior, alter clinical/billing data, add dependencies, delete files, commit, or push as part of setup unless I explicitly ask. If setup fails, report the exact command and error with secrets redacted, then suggest the smallest safe fix.

At the end, summarize the selected interpreter, dependency installation status, test result, whether the API health check succeeded, and any local configuration I still need to provide. Clearly distinguish checks actually completed from steps I need to run.

---

## Local configuration reference

Create `Clinical_decision_support/.env` yourself using an approved credential source. Never commit this file or paste its real values into Copilot Chat.

```dotenv
COFORGE_API_KEY=your-local-secret
COFORGE_API_URL=your-approved-chat-completions-endpoint
COFORGE_MODEL=your-enabled-model
```

For local chargemaster selection, `CHARGEMASTER_PATH` is optional; by default the app looks for the Athena Care Gap chargemaster workbook and then supported fallback files.
