import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import (
    COFORGE_API_KEY,
    COFORGE_API_URL,
    COFORGE_CPT_API_KEY,
    COFORGE_CPT_API_URL,
    COFORGE_CPT_MODEL,
    COFORGE_MODEL,
)


def _call_llm_with_config(prompt, system_prompt=None, temperature=0.3, api_key=None, api_url=None, model=None, key_name="COFORGE_API_KEY"):
    """Call the Coforge LLM Router API using the supplied settings."""

    clean_api_key = (api_key or "").strip()
    clean_api_url = (api_url or "").strip().replace('"', '').replace("%22", "")
    clean_model = (model or "").strip()

    if not clean_api_key:
        raise ValueError(f"{key_name} not found in .env file.")

    if not clean_api_url:
        raise ValueError(f"{key_name.replace('_API_KEY', '_API_URL')} not found in .env file.")

    if not clean_model:
        raise ValueError(
            f"{key_name.replace('_API_KEY', '_MODEL')} is empty. Set it in .env to the exact model name enabled for your Coforge AI Studio account."
        )

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": clean_api_key,
    }

    messages = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.append({"role": "user", "content": prompt})

    body = {
        "model": clean_model,
        "messages": messages,
        "temperature": temperature,
    }

    retry_policy = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=[502, 503, 504],
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry_policy))
    session.mount("http://", HTTPAdapter(max_retries=retry_policy))

    try:
        response = session.post(
            clean_api_url,
            headers=headers,
            json=body,
            timeout=(15, 120),
        )
    except requests.exceptions.RequestException as error:
        raise ConnectionError(
            "Unable to connect to the configured Coforge LLM service after retries."
        ) from error

    if response.status_code != 200:
        raise RuntimeError(
            f"Coforge API error: {response.status_code} - {response.text}"
        )

    data = response.json()
    return data["choices"][0]["message"]["content"]


def call_llm(prompt, system_prompt=None, temperature=0.3):
    """Call the standard Coforge LLM Router API."""
    return _call_llm_with_config(
        prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        api_key=COFORGE_API_KEY,
        api_url=COFORGE_API_URL,
        model=COFORGE_MODEL,
        key_name="COFORGE_API_KEY",
    )


def call_llm_for_cpt(prompt, system_prompt=None, temperature=0.3):
    """Call the dedicated CPT generation LLM configuration if provided."""
    return _call_llm_with_config(
        prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        api_key=COFORGE_CPT_API_KEY,
        api_url=COFORGE_CPT_API_URL,
        model=COFORGE_CPT_MODEL,
        key_name="COFORGE_CPT_API_KEY",
    )
