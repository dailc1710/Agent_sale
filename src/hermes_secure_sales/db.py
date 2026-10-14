from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class TenantSession(Session):
    pass


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=TenantSession)


@event.listens_for(TenantSession, "after_begin")
def apply_tenant_context(session: TenantSession, transaction, connection) -> None:
    tenant_id = session.info.get("tenant_id")
    public_shop_slug = session.info.get("public_shop_slug")
    public_session_token_hash = session.info.get("public_session_token_hash")
    if public_shop_slug and connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('app.public_shop_slug', :slug, true)"),
            {"slug": public_shop_slug},
        )
    if public_session_token_hash and connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('app.public_session_token_hash', :token_hash, true)"),
            {"token_hash": public_session_token_hash},
        )
    if tenant_id and connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
    if session.info.get("platform_admin") and connection.dialect.name == "postgresql":
        connection.execute(text("SELECT set_config('app.is_platform_admin', 'true', true)"))


def session_for_tenant(tenant_id: str) -> Generator[Session, None, None]:
    session = SessionLocal()
    session.info["tenant_id"] = tenant_id
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.info.pop("tenant_id", None)
        session.close()


def platform_session() -> Generator[Session, None, None]:
    """Session for server-side platform administration after admin authentication."""
    session = SessionLocal()
    session.info["platform_admin"] = True
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.info.pop("platform_admin", None)
        session.close()


def enter_tenant_scope(session: Session, tenant_id: str) -> None:
    """Narrow a public slug/token lookup session to one tenant for the rest of its work."""
    session.info["tenant_id"] = tenant_id
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.connection().execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
