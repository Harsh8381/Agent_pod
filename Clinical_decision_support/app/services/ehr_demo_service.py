from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "nuucare_ehr_demo.db"
WORKBOOK_PATH = PROJECT_ROOT / "Epic_Inspired_USA_50_Patient_Demo.xlsx"

_PATIENT_SHEETS = {"Patient_Master"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_scalar(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
    return value


def _normalize_key_name(key: Any) -> str:
    return str(key).strip() if key is not None else ""


def _make_patient_id(raw_value: Any = None) -> str:
    if raw_value is not None:
        value = str(raw_value).strip()
        if value:
            return value
    return f"PT-{uuid.uuid4().hex[:8].upper()}"


def _make_mrn(raw_value: Any = None) -> str:
    if raw_value is not None:
        value = str(raw_value).strip()
        if value:
            return value
    return f"MRN-{uuid.uuid4().hex[:10].upper()}"


def _make_record_id(prefix: str, raw_value: Any = None) -> str:
    if raw_value is not None:
        value = str(raw_value).strip()
        if value:
            return value
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_ehr_demo_db() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS patients (
                patient_id TEXT PRIMARY KEY,
                mrn TEXT,
                first_name TEXT,
                last_name TEXT,
                legal_name TEXT,
                dob TEXT,
                age INTEGER,
                sex_at_birth TEXT,
                pronouns TEXT,
                marital_status TEXT,
                race TEXT,
                ethnicity TEXT,
                preferred_language TEXT,
                secondary_language TEXT,
                pcp TEXT,
                record_status TEXT,
                height_cm REAL,
                weight_kg REAL,
                bmi REAL,
                city TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS patient_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                source_record_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_patient_records_patient_type ON patient_records(patient_id, record_type)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bills (
                bill_id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                encounter_id TEXT,
                claim_id TEXT,
                service_date TEXT,
                cpt_code TEXT,
                description TEXT,
                amount_usd REAL,
                status TEXT,
                payer TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_patient ON bills(patient_id)"
        )


def _read_excel_workbook(file_path: Path) -> dict[str, pd.DataFrame]:
    if not file_path.exists():
        return {}
    workbook = pd.ExcelFile(file_path, engine="openpyxl")
    frames: dict[str, pd.DataFrame] = {}
    for sheet_name in workbook.sheet_names:
        try:
            data = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
        except Exception:
            data = pd.DataFrame()
        frames[sheet_name] = data
    return frames


def _seed_patient_master() -> None:
    if not WORKBOOK_PATH.exists():
        return
    with _connect() as connection:
        patient_count = connection.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        if patient_count > 0:
            return

        workbook = _read_excel_workbook(WORKBOOK_PATH)
        patient_frame = workbook.get("Patient_Master")
        if patient_frame is None or patient_frame.empty:
            return

        for _, row in patient_frame.iterrows():
            payload = {
                _normalize_key_name(key): _normalize_scalar(value)
                for key, value in row.items()
            }
            patient_id = _make_patient_id(payload.get("Patient_ID") or payload.get("patient_id") or payload.get("MRN"))
            mrn = _make_mrn(payload.get("MRN") or payload.get("mrn") or patient_id)
            first_name = payload.get("First_Name") or payload.get("first_name") or "Synthetic"
            last_name = payload.get("Last_Name") or payload.get("last_name") or "Patient"
            legal_name = payload.get("Legal_Name") or payload.get("legal_name") or f"{first_name} {last_name}"
            dob = payload.get("DOB") or payload.get("dob")
            age = payload.get("Age") or payload.get("age")
            sex = payload.get("Sex_at_Birth") or payload.get("sex_at_birth") or "Unknown"
            city = payload.get("City") or payload.get("city")
            record = {
                "patient_id": patient_id,
                "mrn": mrn,
                "first_name": first_name,
                "last_name": last_name,
                "legal_name": legal_name,
                "dob": dob,
                "age": age,
                "sex_at_birth": sex,
                "pronouns": payload.get("Pronouns") or payload.get("pronouns"),
                "marital_status": payload.get("Marital_Status") or payload.get("marital_status"),
                "race": payload.get("Race") or payload.get("race"),
                "ethnicity": payload.get("Ethnicity") or payload.get("ethnicity"),
                "preferred_language": payload.get("Preferred_Language") or payload.get("preferred_language"),
                "secondary_language": payload.get("Secondary_Language") or payload.get("secondary_language"),
                "pcp": payload.get("PCP") or payload.get("pcp"),
                "record_status": payload.get("Record_Status") or payload.get("record_status"),
                "height_cm": payload.get("Height_cm") or payload.get("height_cm"),
                "weight_kg": payload.get("Weight_kg") or payload.get("weight_kg"),
                "bmi": payload.get("BMI") or payload.get("bmi"),
                "city": city,
                "source": "Epic_Inspired_USA_50_Patient_Demo.xlsx",
            }
            created_at = _utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO patients (
                    patient_id, mrn, first_name, last_name, legal_name, dob, age, sex_at_birth, pronouns,
                    marital_status, race, ethnicity, preferred_language, secondary_language, pcp,
                    record_status, height_cm, weight_kg, bmi, city, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    patient_id,
                    mrn,
                    first_name,
                    last_name,
                    legal_name,
                    str(dob) if dob is not None else None,
                    age,
                    sex,
                    record.get("pronouns"),
                    record.get("marital_status"),
                    record.get("race"),
                    record.get("ethnicity"),
                    record.get("preferred_language"),
                    record.get("secondary_language"),
                    record.get("pcp"),
                    record.get("record_status"),
                    record.get("height_cm"),
                    record.get("weight_kg"),
                    record.get("bmi"),
                    record.get("city"),
                    json.dumps(record, sort_keys=True),
                    created_at,
                    created_at,
                ),
            )

        for sheet_name, frame in workbook.items():
            if sheet_name == "Patient_Master":
                continue
            if frame is None or frame.empty:
                continue
            for _, row in frame.iterrows():
                payload = {
                    _normalize_key_name(key): _normalize_scalar(value)
                    for key, value in row.items()
                }
                patient_id = None
                for candidate_key in ("Patient_ID", "patient_id", "MRN", "mrn"):
                    if candidate_key in payload and payload.get(candidate_key) not in (None, ""):
                        patient_id = str(payload[candidate_key])
                        break
                if patient_id is None:
                    continue
                source_record_id = None
                for candidate_key in (
                    "Encounter_ID",
                    "Problem_ID",
                    "Medication_ID",
                    "Note_ID",
                    "Claim_ID",
                    "Bill_ID",
                    "Order_ID",
                    "Appointment_ID",
                    "Care_Team_ID",
                    "Insurance_ID",
                    "Coverage_ID",
                    "Immunization_ID",
                    "Allergy_ID",
                    "Lab_ID",
                    "Vital_ID",
                    "Consent_Type",
                ):
                    if candidate_key in payload and payload.get(candidate_key) not in (None, ""):
                        source_record_id = str(payload[candidate_key])
                        break
                source_record_id = source_record_id or _make_record_id(sheet_name.upper(), patient_id)
                timestamp = _utc_now()
                connection.execute(
                    """
                    INSERT INTO patient_records (patient_id, record_type, source_record_id, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(patient_id), sheet_name, source_record_id, json.dumps(payload, sort_keys=True), timestamp, timestamp),
                )
                if sheet_name == "Billing_Claims":
                    bill_id = _make_record_id("BILL", payload.get("Claim_ID") or payload.get("Claim") or patient_id)
                    amount = payload.get("Charge_USD") or payload.get("charge_usd") or payload.get("Payment_USD") or 0
                    claim_id = payload.get("Claim_ID") or payload.get("claim_id")
                    encounter_id = payload.get("Encounter_ID") or payload.get("encounter_id")
                    service_date = payload.get("Service_Date") or payload.get("service_date")
                    cpt_code = payload.get("CPT_HCPCS") or payload.get("cpt_hcpcs")
                    description = payload.get("Description") or payload.get("description")
                    payer = payload.get("Payer") or payload.get("payer")
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO bills (
                            bill_id, patient_id, encounter_id, claim_id, service_date, cpt_code,
                            description, amount_usd, status, payer, payload_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            bill_id,
                            str(patient_id),
                            str(encounter_id) if encounter_id is not None else None,
                            str(claim_id) if claim_id is not None else None,
                            str(service_date) if service_date is not None else None,
                            str(cpt_code) if cpt_code is not None else None,
                            str(description) if description is not None else None,
                            float(amount) if amount is not None else 0.0,
                            payload.get("Claim_Status") or payload.get("claim_status") or "draft",
                            str(payer) if payer is not None else None,
                            json.dumps(payload, sort_keys=True),
                            timestamp,
                            timestamp,
                        ),
                    )


def ensure_seeded() -> None:
    init_ehr_demo_db()
    _seed_patient_master()


def list_patients() -> list[dict[str, Any]]:
    ensure_seeded()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT patient_id, mrn, first_name, last_name, legal_name, dob, age, sex_at_birth, pronouns, marital_status, race, ethnicity, preferred_language, secondary_language, pcp, record_status, height_cm, weight_kg, bmi, city, payload_json FROM patients ORDER BY last_name, first_name"
        ).fetchall()
    patients: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        patients.append({
            "patient_id": row["patient_id"],
            "mrn": row["mrn"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "legal_name": row["legal_name"],
            "display_name": payload.get("legal_name") or f"{row['first_name']} {row['last_name']}",
            "dob": row["dob"],
            "age": row["age"],
            "sex_at_birth": row["sex_at_birth"],
            "city": row["city"],
            "pcp": row["pcp"],
            "record_status": row["record_status"],
            "payload": payload,
        })
    return patients


def get_patient_by_id(patient_id: str) -> dict[str, Any]:
    ensure_seeded()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM patients WHERE patient_id = ?",
            (str(patient_id),),
        ).fetchone()
    if row is None:
        raise KeyError(f"Patient not found: {patient_id}")
    payload = json.loads(row["payload_json"])
    payload.update({
        "patient_id": row["patient_id"],
        "mrn": row["mrn"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "legal_name": row["legal_name"],
        "dob": row["dob"],
        "age": row["age"],
        "sex_at_birth": row["sex_at_birth"],
        "city": row["city"],
    })
    return payload


def get_patient_chart(patient_id: str) -> dict[str, Any]:
    patient = get_patient_by_id(patient_id)
    with _connect() as connection:
        records = connection.execute(
            "SELECT record_type, source_record_id, payload_json FROM patient_records WHERE patient_id = ? ORDER BY record_type, source_record_id",
            (str(patient_id),),
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["record_type"], []).append(json.loads(record["payload_json"]))
    return {
        "patient": patient,
        "records": grouped,
        "record_counts": {key: len(value) for key, value in grouped.items()},
    }


def create_patient(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_seeded()
    patient_input = {str(key): value for key, value in (payload or {}).items()}
    patient_id = _make_patient_id(patient_input.get("patient_id") or patient_input.get("Patient_ID"))
    mrn = _make_mrn(patient_input.get("mrn") or patient_input.get("MRN") or patient_id)
    first_name = str(patient_input.get("first_name") or patient_input.get("First_Name") or "Synthetic").strip() or "Synthetic"
    last_name = str(patient_input.get("last_name") or patient_input.get("Last_Name") or "Patient").strip() or "Patient"
    legal_name = str(patient_input.get("legal_name") or patient_input.get("Legal_Name") or f"{first_name} {last_name}").strip()
    normalized = {
        "patient_id": patient_id,
        "mrn": mrn,
        "first_name": first_name,
        "last_name": last_name,
        "legal_name": legal_name,
        "dob": patient_input.get("dob") or patient_input.get("DOB"),
        "age": patient_input.get("age") or patient_input.get("Age"),
        "sex_at_birth": patient_input.get("sex_at_birth") or patient_input.get("Sex_at_Birth") or "Unknown",
        "pronouns": patient_input.get("pronouns") or patient_input.get("Pronouns"),
        "marital_status": patient_input.get("marital_status") or patient_input.get("Marital_Status"),
        "race": patient_input.get("race") or patient_input.get("Race"),
        "ethnicity": patient_input.get("ethnicity") or patient_input.get("Ethnicity"),
        "preferred_language": patient_input.get("preferred_language") or patient_input.get("Preferred_Language"),
        "secondary_language": patient_input.get("secondary_language") or patient_input.get("Secondary_Language"),
        "pcp": patient_input.get("pcp") or patient_input.get("PCP"),
        "record_status": patient_input.get("record_status") or patient_input.get("Record_Status") or "active",
        "height_cm": patient_input.get("height_cm") or patient_input.get("Height_cm"),
        "weight_kg": patient_input.get("weight_kg") or patient_input.get("Weight_kg"),
        "bmi": patient_input.get("bmi") or patient_input.get("BMI"),
        "city": patient_input.get("city") or patient_input.get("City"),
        "source": "synthetic_demo",
    }
    timestamp = _utc_now()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO patients (
                patient_id, mrn, first_name, last_name, legal_name, dob, age, sex_at_birth, pronouns,
                marital_status, race, ethnicity, preferred_language, secondary_language, pcp,
                record_status, height_cm, weight_kg, bmi, city, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_id,
                mrn,
                first_name,
                last_name,
                legal_name,
                normalized.get("dob"),
                normalized.get("age"),
                normalized.get("sex_at_birth"),
                normalized.get("pronouns"),
                normalized.get("marital_status"),
                normalized.get("race"),
                normalized.get("ethnicity"),
                normalized.get("preferred_language"),
                normalized.get("secondary_language"),
                normalized.get("pcp"),
                normalized.get("record_status"),
                normalized.get("height_cm"),
                normalized.get("weight_kg"),
                normalized.get("bmi"),
                normalized.get("city"),
                json.dumps(normalized, sort_keys=True),
                timestamp,
                timestamp,
            ),
        )
    return get_patient_by_id(patient_id)


def update_patient(patient_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_seeded()
    existing = get_patient_by_id(patient_id)
    merged = {**existing, **{str(k): v for k, v in (payload or {}).items()}}
    if "patient_id" not in merged:
        merged["patient_id"] = patient_id
    if "mrn" not in merged:
        merged["mrn"] = existing.get("mrn") or _make_mrn(patient_id)
    if "first_name" not in merged:
        merged["first_name"] = existing.get("first_name") or "Synthetic"
    if "last_name" not in merged:
        merged["last_name"] = existing.get("last_name") or "Patient"
    if "legal_name" not in merged or not merged["legal_name"]:
        merged["legal_name"] = f"{merged['first_name']} {merged['last_name']}"
    merged["updated_at"] = _utc_now()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE patients SET
                mrn = ?, first_name = ?, last_name = ?, legal_name = ?, dob = ?, age = ?, sex_at_birth = ?,
                pronouns = ?, marital_status = ?, race = ?, ethnicity = ?, preferred_language = ?, secondary_language = ?,
                pcp = ?, record_status = ?, height_cm = ?, weight_kg = ?, bmi = ?, city = ?, payload_json = ?, updated_at = ?
            WHERE patient_id = ?
            """,
            (
                merged.get("mrn"),
                merged.get("first_name"),
                merged.get("last_name"),
                merged.get("legal_name"),
                merged.get("dob"),
                merged.get("age"),
                merged.get("sex_at_birth"),
                merged.get("pronouns"),
                merged.get("marital_status"),
                merged.get("race"),
                merged.get("ethnicity"),
                merged.get("preferred_language"),
                merged.get("secondary_language"),
                merged.get("pcp"),
                merged.get("record_status"),
                merged.get("height_cm"),
                merged.get("weight_kg"),
                merged.get("bmi"),
                merged.get("city"),
                json.dumps(merged, sort_keys=True),
                merged["updated_at"],
                patient_id,
            ),
        )
    return get_patient_by_id(patient_id)


def delete_patient(patient_id: str) -> bool:
    ensure_seeded()
    with _connect() as connection:
        connection.execute("DELETE FROM patient_records WHERE patient_id = ?", (str(patient_id),))
        connection.execute("DELETE FROM bills WHERE patient_id = ?", (str(patient_id),))
        cursor = connection.execute("DELETE FROM patients WHERE patient_id = ?", (str(patient_id),))
    return cursor.rowcount > 0


def list_patient_records(patient_id: str, record_type: str | None = None) -> list[dict[str, Any]]:
    ensure_seeded()
    query = "SELECT * FROM patient_records WHERE patient_id = ?"
    params: list[Any] = [str(patient_id)]
    if record_type:
        query += " AND record_type = ?"
        params.append(str(record_type))
    query += " ORDER BY record_type, source_record_id"
    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def add_patient_record(patient_id: str, record_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_seeded()
    record_data = payload or {}
    record_id = _make_record_id(str(record_type).upper(), record_data.get("id") or record_data.get("record_id") or record_data.get("Encounter_ID") or record_data.get("Claim_ID") or record_data.get("Note_ID"))
    timestamp = _utc_now()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO patient_records (patient_id, record_type, source_record_id, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(patient_id), str(record_type), record_id, json.dumps(record_data, sort_keys=True), timestamp, timestamp),
        )
    return {"patient_id": patient_id, "record_type": record_type, "record_id": record_id, "payload": record_data}


def replace_patient_records(patient_id: str, record_type: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ensure_seeded()
    with _connect() as connection:
        connection.execute("DELETE FROM patient_records WHERE patient_id = ? AND record_type = ?", (str(patient_id), str(record_type)))
        inserted: list[dict[str, Any]] = []
        for item in records or []:
            record = dict(item)
            record_id = _make_record_id(str(record_type).upper(), record.get("id") or record.get("record_id") or record.get("Encounter_ID") or record.get("Claim_ID") or record.get("Note_ID") or record.get("patient_id"))
            timestamp = _utc_now()
            connection.execute(
                "INSERT INTO patient_records (patient_id, record_type, source_record_id, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(patient_id), str(record_type), record_id, json.dumps(record, sort_keys=True), timestamp, timestamp),
            )
            inserted.append(record)
    return inserted


def list_bills(patient_id: str | None = None) -> list[dict[str, Any]]:
    ensure_seeded()
    query = "SELECT * FROM bills"
    params: list[Any] = []
    if patient_id is not None:
        query += " WHERE patient_id = ?"
        params.append(str(patient_id))
    query += " ORDER BY service_date DESC, created_at DESC"
    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [json.loads(row["payload_json"]) | {"bill_id": row["bill_id"], "patient_id": row["patient_id"], "encounter_id": row["encounter_id"]} for row in rows]


def generate_patient_bill(patient_id: str, encounter_id: str | None, services: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_seeded()
    get_patient_by_id(patient_id)
    if not services:
        raise ValueError("No services were provided to generate a bill.")
    total = 0.0
    bill_lines: list[dict[str, Any]] = []
    for service in services:
        amount = service.get("amount") or service.get("Amount") or service.get("charge_usd") or service.get("Charge_USD") or 0
        try:
            total += float(amount)
        except (TypeError, ValueError):
            continue
        bill_lines.append({
            "cpt_code": service.get("cpt_code") or service.get("CPT_HCPCS") or service.get("code"),
            "description": service.get("description") or service.get("Description") or service.get("name"),
            "amount_usd": float(amount),
        })
    bill_id = _make_record_id("BILL", encounter_id or patient_id)
    payload = {
        "bill_id": bill_id,
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "service_date": str(datetime.now().date().isoformat()),
        "total_amount_usd": round(total, 2),
        "status": "draft",
        "services": bill_lines,
        "notes": "Amounts are gross chargemaster-based estimates and are not finalized claims, payer-allowed amounts, or patient responsibility amounts.",
    }
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO bills (bill_id, patient_id, encounter_id, claim_id, service_date, cpt_code, description, amount_usd, status, payer, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bill_id,
                str(patient_id),
                str(encounter_id) if encounter_id else None,
                None,
                payload["service_date"],
                (bill_lines[0].get("cpt_code") if bill_lines else None),
                (bill_lines[0].get("description") if bill_lines else None),
                total,
                "draft",
                None,
                json.dumps(payload, sort_keys=True),
                _utc_now(),
                _utc_now(),
            ),
        )
    return payload


def export_patient_data(patient_id: str | None = None) -> dict[str, Any]:
    ensure_seeded()
    if patient_id is not None:
        chart = get_patient_chart(patient_id)
        patient_payload = chart["patient"]
        return {
            "patient": patient_payload,
            "records": chart["records"],
            "sheet_count": len(chart["records"]),
        }

    patient_rows = list_patients()
    export_by_sheet: dict[str, dict[str, list[dict[str, Any]]]] = {"Patient_Master": {"records": patient_rows}}
    for record_type in sorted({item["record_type"] for item in [
        {"record_type": record_type} for record_type in list_patient_records("", record_type=None) if False
    ]}):
        pass
    with _connect() as connection:
        rows = connection.execute(
            "SELECT record_type, payload_json FROM patient_records ORDER BY record_type, id"
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["record_type"], []).append(json.loads(row["payload_json"]))
    for record_type, records in grouped.items():
        export_by_sheet.setdefault(record_type, {"records": []})
        export_by_sheet[record_type]["records"] = records
    return {"sheets": export_by_sheet, "patient_count": len(patient_rows)}
