"""Create the SQLite schema for local development only."""

from hermes_secure_sales.config import get_settings
from hermes_secure_sales.db import engine
from hermes_secure_sales.models import Base


def main() -> None:
    settings = get_settings()
    if settings.app_env != "development" or not settings.database_url.startswith("sqlite"):
        raise SystemExit("This helper is limited to development SQLite databases.")
    Base.metadata.create_all(engine)
    print("Development database is ready.")


if __name__ == "__main__":
    main()

