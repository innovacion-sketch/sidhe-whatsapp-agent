"""Tokens gastados por día y por modelo.

El contador vivía en memoria y se reiniciaba en cada Deploy, así que el
panel mostraba un gasto mucho menor que la factura real. Con esta tabla el
costo se puede comparar contra el recibo de Anthropic y, sobre todo, se ve
si el caché está pegando.

Revision ID: 0008
Revises: 0007
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uso_modelo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("modelo", sa.String(60), nullable=False),
        sa.Column("llamadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entrada", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("salida", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_lectura", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "cache_escritura", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.UniqueConstraint("fecha", "modelo", name="uq_uso_fecha_modelo"),
    )


def downgrade() -> None:
    op.drop_table("uso_modelo")
