from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

bearer = HTTPBearer(auto_error=False)
ROLES = {"tenant_admin", "sales_agent", "reviewer", "viewer"}


@dataclass(frozen=True)
class UserContext:
    subject: str
    tenant_id: UUID
    role: str
    customer_ids: frozenset[UUID]

    def can_access_customer(self, customer_id: UUID) -> bool:
        return self.role == "tenant_admin" or customer_id in self.customer_ids


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UserContext:
    secret = get_settings().jwt_secret
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    if credentials is None:
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        claims = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=["HS256"],
            options={"require": ["sub", "tenant_id", "role", "exp"]},
        )
        tenant_id = UUID(str(claims["tenant_id"]))
        role = str(claims["role"])
        if role not in ROLES:
            raise ValueError("unknown role")
        raw_customer_ids = claims.get("customer_ids", [])
        if not isinstance(raw_customer_ids, list):
            raise ValueError("invalid customer scope")
        customer_ids = frozenset(UUID(str(item)) for item in raw_customer_ids)
        return UserContext(str(claims["sub"]), tenant_id, role, customer_ids)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid bearer token") from None


def require_roles(*roles: str) -> Callable:
    def dependency(user: Annotated[UserContext, Depends(get_current_user)]) -> UserContext:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return dependency

