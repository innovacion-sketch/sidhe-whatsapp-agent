"""Configuración de entorno para tests: sin Postgres ni credenciales reales.

El fixture `db` (Postgres real, base sidhe_test) se comparte entre las suites
de tools de citas y de RAG; si no hay servidor Postgres, esos tests se saltan.
"""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/sidhe_test"
)
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "token_de_prueba")
os.environ.setdefault("TWILIO_VALIDATE_SIGNATURE", "false")

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from sidhe_agent.config import get_settings  # noqa: E402
from sidhe_agent.db import session as db_session  # noqa: E402
from sidhe_agent.db.models import Base  # noqa: E402


async def _asegurar_base_de_datos() -> None:
    """Crea la base sidhe_test si no existe; pytest.skip si no hay servidor."""
    settings = get_settings()
    url_admin = settings.sqlalchemy_url.rsplit("/", 1)[0] + "/postgres"
    nombre_db = settings.sqlalchemy_url.rsplit("/", 1)[1]
    try:
        engine = create_async_engine(url_admin, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            existe = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": nombre_db}
            )
            if not existe.first():
                await conn.execute(text(f'CREATE DATABASE "{nombre_db}"'))
        await engine.dispose()
    except Exception as exc:
        pytest.skip(f"Postgres no disponible: {exc}")


@pytest.fixture
async def db():
    await _asegurar_base_de_datos()
    engine = db_session.get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db_session.dispose_engine()
