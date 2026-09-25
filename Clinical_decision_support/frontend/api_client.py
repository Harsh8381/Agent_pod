import requests


class HealthcareApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

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
