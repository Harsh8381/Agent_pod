"""HS256 bearer tokens (JWT-compatible), signed with a secret from the environment.

Production: HEDIS_API_SECRET comes from the secret store. Local development falls back to a random
secret generated once into data/.hedis_api_secret (git-ignored); the NCQA readiness check flags that.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

SECRET_ENV = "HEDIS_API_SECRET"
TTL_ENV = "HEDIS_TOKEN_TTL_SECONDS"
ISSUER = "nuucare-hedis"
DEFAULT_TTL = 30 * 60
SECRET_FILE = Path(__file__).resolve().parents[2] / "data" / ".hedis_api_secret"


class TokenError(ValueError):
    """The token is malformed, tampered with, expired or not ours. The message is safe to return."""


def _secret() -> bytes:
    configured = os.environ.get(SECRET_ENV, "").strip()
    if configured:
        if len(configured) < 32:
            raise RuntimeError(f"{SECRET_ENV} must be at least 32 characters.")
        return configured.encode("utf-8")
    if not SECRET_FILE.is_file():
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            os.chmod(SECRET_FILE, 0o600)
        except OSError:
            pass
    return SECRET_FILE.read_text(encoding="utf-8").strip().encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: str) -> str:
    return _b64(hmac.new(_secret(), signing_input.encode("ascii"), hashlib.sha256).digest())


def issue(username: str, role: str, ttl: int | None = None, now: float | None = None) -> str:
    now = int(now if now is not None else time.time())
    ttl = int(ttl or os.environ.get(TTL_ENV) or DEFAULT_TTL)
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"sub": username, "role": role, "iss": ISSUER, "iat": now, "exp": now + ttl,
                               "jti": secrets.token_hex(8)}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    return f"{signing_input}.{_sign(signing_input)}"


def verify(token: str, now: float | None = None) -> dict:
    try:
        header_b64, payload_b64, signature = token.split(".")
        header = json.loads(_unb64(header_b64))
        claims = json.loads(_unb64(payload_b64))
    except (ValueError, json.JSONDecodeError) as error:
        raise TokenError("Malformed token.") from error
    if header.get("alg") != "HS256":
        raise TokenError("Unsupported token algorithm.")
    if not hmac.compare_digest(signature, _sign(f"{header_b64}.{payload_b64}")):
        raise TokenError("Invalid token signature.")
    if claims.get("iss") != ISSUER:
        raise TokenError("Token was not issued by this service.")
    if int(claims.get("exp", 0)) <= int(now if now is not None else time.time()):
        raise TokenError("Token expired.")
    if not claims.get("sub"):
        raise TokenError("Token has no subject.")
    return claims
