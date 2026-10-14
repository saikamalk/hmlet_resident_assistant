"""OIDC JWT authentication. Never trust actor IDs supplied in request bodies."""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()
import os
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)


def get_current_actor(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    """Resolve the current actor; bypass auth only in explicit local dev mode."""

    if (
            os.getenv("APP_ENV") == "development"
            and os.getenv("AUTH_DISABLED", "").lower() == "true"
    ):
        return {
            "actor_id": "local-dev-user",
            "claims": {"sub": "local-dev-user"},
        }

    # Keep the existing OIDC/JWT validation below this point.
    jwks_url = os.getenv("AUTH_JWKS_URL")
    issuer = os.getenv("AUTH_ISSUER")
    audience = os.getenv("AUTH_AUDIENCE")

    if not (jwks_url and issuer and audience):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Authentication provider is not configured.")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Bearer token required.", headers={"WWW-Authenticate": "Bearer"})
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(credentials.credentials).key
        claims = jwt.decode(
            credentials.credentials,
            signing_key,
            algorithms=["RS256", "ES256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            leeway=30,
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 256:
            raise jwt.InvalidTokenError("Invalid subject")
        return {"actor_id": subject, "claims": claims}
    except (jwt.PyJWTError, Exception) as exc:
        # Do not return token, claim or cryptographic details to callers.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or expired access token.",
                            headers={"WWW-Authenticate": "Bearer"}) from exc
