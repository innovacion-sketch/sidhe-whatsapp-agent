"""Entorno async de Alembic: usa DATABASE_URL y el metadata de los modelos."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from sidhe_agent.config import get_settings
from sidhe_agent.db.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(get_settings().sqlalchemy_url)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
