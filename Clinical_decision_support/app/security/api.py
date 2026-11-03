"""FastAPI dependencies: every protected route declares the one permission it needs."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.hedis import store as hedis_store
from app.security import rbac, tokens

bearer = HTTPBearer(auto_error=False, description="Token from POST /hedis/auth/token")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def require(permission: str):
    """Dependency factory: authenticate the bearer token, then authorize one named permission."""
    if permission not in rbac.PERMISSIONS:
        raise KeyError(f"Unknown permission {permission}")

    def dependency(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> rbac.Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise _unauthorized("Bearer token required.")
        try:
            claims = tokens.verify(credentials.credentials)
        except tokens.TokenError as error:
            raise _unauthorized(str(error)) from error
        store = rbac.load_store()
        principal = rbac.principal_for(claims["sub"], store)
        if principal is None:
            raise _unauthorized("Account is unknown, deactivated or locked.")
        if principal.role != claims.get("role"):
            raise _unauthorized("Role changed since the token was issued; sign in again.")
        if not rbac.allowed(principal, permission, store):
            module, action = rbac.PERMISSIONS[permission]
            with hedis_store.transaction() as state:
                hedis_store.audit(state, principal.actor, "rbac.denied", "permission", permission, trigger="api",
                                  details=f"Requires {action} on {module}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Requires {action} on {module}.")
        return principal

    dependency.__name__ = f"require_{permission.replace(':', '_')}"
    dependency.__qualname__ = f"require.{dependency.__name__}"
    return dependency
