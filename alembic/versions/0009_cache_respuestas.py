"""Caché de respuestas de catálogo.

Guarda preguntas ya contestadas SIN consultar herramientas ni datos del
cliente, para reusarlas cuando alguien vuelve a preguntar lo mismo y
ahorrarse la llamada al modelo.

Revision ID: 0009
Revises: 0008
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.create_table(
        "respuestas_cacheadas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pregunta", sa.Text(), nullable=False),
        sa.Column("respuesta", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("prompt_hash", sa.String(32), nullable=False),
        sa.Column("usos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_cache_hash", "respuestas_cacheadas", ["prompt_hash"])


def downgrade() -> None:
    op.drop_index("ix_cache_hash", table_name="respuestas_cacheadas")
    op.drop_table("respuestas_cacheadas")
