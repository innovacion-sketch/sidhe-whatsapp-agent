"""Tabla de pedidos (espejo de la hoja STATUS del Excel de operaciones).

Revision ID: 0003
Revises: 0002
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pedidos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fecha", sa.Date(), nullable=True),
        sa.Column("nombre", sa.String(200), nullable=False),
        sa.Column("nombre_normalizado", sa.String(200), nullable=False),
        sa.Column("sucursal", sa.String(80), nullable=False, server_default=""),
        sa.Column("telefono", sa.String(10), nullable=True),
        sa.Column("status_original", sa.String(120), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default=""),
        sa.Column("lugar_impresion", sa.String(80), nullable=True),
        sa.Column("localizacion_final", sa.String(80), nullable=True),
        sa.Column("envio", sa.String(120), nullable=True),
        sa.Column(
            "importado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_pedidos_telefono", "pedidos", ["telefono"])
    op.create_index(
        "ix_pedidos_nombre_sucursal", "pedidos", ["sucursal", "nombre_normalizado"]
    )


def downgrade() -> None:
    op.drop_index("ix_pedidos_nombre_sucursal", table_name="pedidos")
    op.drop_index("ix_pedidos_telefono", table_name="pedidos")
    op.drop_table("pedidos")
