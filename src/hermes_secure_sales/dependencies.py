from collections.abc import Generator
from typing import Annotated
import hashlib
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user
from .db import SessionLocal, enter_tenant_scope, platform_session, session_for_tenant
from .hermes import HermesClient


def get_user_db(user: Annotated[UserContext, Depends(get_current_user)]) -> Generator[Session, None, None]:
    yield from session_for_tenant(str(user.tenant_id))


def get_webhook_db(tenant_id: UUID) -> Generator[Session, None, None]:
    yield from session_for_tenant(str(tenant_id))


def get_platform_db() -> Generator[Session, None, None]:
    yield from platform_session()


def get_public_shop_db(slug: str) -> Generator[Session, None, None]:
    session = SessionLocal()
    session.info["public_shop_slug"] = slug
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.info.pop("public_shop_slug", None)
        session.info.pop("tenant_id", None)
        session.close()


def get_public_session_db(
    authorization: Annotated[str | None, Header()] = None,
) -> Generator[Session, None, None]:
    session = SessionLocal()
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    session.info["public_session_token_hash"] = hashlib.sha256(token.encode()).hexdigest() if token else ""
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.info.pop("public_session_token_hash", None)
        session.info.pop("tenant_id", None)
        session.close()


def get_hermes_client() -> HermesClient:
    return HermesClient()
