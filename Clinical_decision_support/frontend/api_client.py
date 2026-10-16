import requests


class HealthcareApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, endpoint: str, *, json_body=None, params=None):
        try:
            response = requests.request(
                method,
                f"{self.base_url}{endpoint}",
                json=json_body,
                params=params,
                timeout=180,
            )
        except requests.exceptions.ConnectionError as error:
            raise RuntimeError(
                f"Cannot connect to the FastAPI backend at {self.base_url}. "
                "Start it with: python -m app"
            ) from error
        except requests.exceptions.Timeout as error:
            raise RuntimeError(f"The FastAPI backend timed out while calling {endpoint}.") from error

        response.raise_for_status()
        return response.json()

    def analyze_encounter(self, transcript: str) -> dict:
        return self._post_encounter("/encounters/analyze", transcript, include_cds=True)

    def regenerate_encounter(self, transcript: str) -> dict:
        return self._post_encounter("/encounters/analyze", transcript, include_cds=True)

    def regenerate_cds(self, encounter: dict) -> dict:
        return self.add_cds(encounter)

    def scribe_encounter(self, transcript: str) -> dict:
        return self._post_encounter("/encounters/scribe", transcript, include_cds=False)

    def add_cds(self, encounter: dict) -> dict:
        for attempt in range(2):
            try:
                response = requests.post(
                    f"{self.base_url}/encounters/cds",
                    json=encounter,
                    timeout=180,
                )
            except requests.exceptions.ConnectionError as error:
                raise RuntimeError(
                    f"Cannot connect to the FastAPI backend at {self.base_url}. "
                    "Start it with: python -m app"
                ) from error
            except requests.exceptions.Timeout as error:
                raise RuntimeError("The FastAPI backend timed out while running CDS.") from error

            response.raise_for_status()
            result = response.json()
            recommendation = result.get("recommendations")
            if recommendation and str(recommendation).strip():
                return result

            if attempt == 0:
                continue

        raise RuntimeError("The CDS service returned no recommendation after retrying.")

    def match_codes(self, conditions: list[str], procedures: list[str], documentation: str = "") -> dict:
        try:
            response = requests.post(
                f"{self.base_url}/codes",
                json={
                    "conditions": conditions,
                    "procedures": procedures,
                    "documentation": documentation,
                },
                timeout=60,
            )
        except requests.exceptions.ConnectionError as error:
            raise RuntimeError(
                f"Cannot connect to the FastAPI backend at {self.base_url}. "
                "Start it with: python -m app"
            ) from error
        except requests.exceptions.Timeout as error:
            raise RuntimeError("The FastAPI backend timed out while matching codes.") from error

        response.raise_for_status()
        return response.json()

    def retrieve_knowledge(self, query: str, top_k: int = 5) -> dict:
        return self._request("POST", "/retrieve", json_body={"query": query, "top_k": top_k})

    def export_patient_data(self, patient_id: str | None = None) -> dict:
        return self._request("GET", "/patients/export", params={"patient_id": patient_id} if patient_id else None)

    def list_patients(self) -> dict:
        return self._request("GET", "/patients")

    def get_patient_chart(self, patient_id: str) -> dict:
        return self._request("GET", f"/patients/{patient_id}/chart")

    def create_patient(self, payload: dict) -> dict:
        return self._request("POST", "/patients", json_body=payload)

    def create_patient_record(self, patient_id: str, sheet_name: str, payload: dict) -> dict:
        return self._request("POST", f"/patients/{patient_id}/records/{sheet_name}", json_body=payload)

    def add_patient_record(self, patient_id: str, record_type: str, payload: dict) -> dict:
        return self.create_patient_record(patient_id, record_type, payload)

    def update_patient(self, patient_id: str, payload: dict) -> dict:
        return self._request("PUT", f"/patients/{patient_id}", json_body=payload)

    def replace_patient_records(self, patient_id: str, sheet_name: str, records: list[dict]) -> list[dict]:
        return self._request("PUT", f"/patients/{patient_id}/records/{sheet_name}", json_body={"records": records})

    def delete_patient(self, patient_id: str) -> dict:
        return self._request("DELETE", f"/patients/{patient_id}")

    def list_patient_records(self, patient_id: str, record_type: str | None = None) -> dict:
        return self._request("GET", f"/ehr/patients/{patient_id}/records", params={"record_type": record_type} if record_type else None)

    def list_bills(self, patient_id: str | None = None) -> dict:
        return self._request("GET", "/ehr/bills", params={"patient_id": patient_id} if patient_id else None)

    def generate_patient_bill(
        self,
        patient_id: str,
        payload: dict | None = None,
        encounter_id: str | None = None,
        services: list[dict] | None = None,
    ) -> dict:
        if isinstance(payload, str):
            legacy_encounter_id = payload
            legacy_services = encounter_id if isinstance(encounter_id, list) else services
            payload = {"encounter_id": legacy_encounter_id, "services": legacy_services or []}
            encounter_id = None
            services = None
        bill_payload = dict(payload or {})
        bill_payload.setdefault("encounter_id", encounter_id)
        bill_payload.setdefault("services", services or [])
        encounter_type = str(bill_payload.get("encounter_type") or "OPD").upper()
        route_type = "ipd" if encounter_type in {"IPD", "INPATIENT"} else "opd"
        endpoint = f"/patients/{patient_id}/bills/{route_type}"
        return self._request(
            "POST",
            endpoint,
            json_body=bill_payload,
        )

    def _post_encounter(self, endpoint: str, transcript: str, include_cds: bool) -> dict:
        try:
            response = requests.post(
                f"{self.base_url}{endpoint}",
                json={"transcript": transcript, "include_cds": include_cds},
                timeout=180,
            )
        except requests.exceptions.ConnectionError as error:
            raise RuntimeError(
                f"Cannot connect to the FastAPI backend at {self.base_url}. "
                "Start it with: python -m app"
            ) from error
        except requests.exceptions.Timeout as error:
            raise RuntimeError("The FastAPI backend timed out while analyzing the encounter.") from error

        response.raise_for_status()
        return response.json()
