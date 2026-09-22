"""El caché de respuestas funciona aunque no haya embeddings.

Con la pregunta normalizada guardada, una pregunta escrita igual se
encuentra con una comparación de texto, sin llamar a ningún proveedor. Los
embeddings quedan como mejora: sirven para reconocer la misma pregunta
escrita distinto, pero ya no son un requisito para que el caché exista.

Revision ID: 0010
Revises: 0009
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "respuestas_cacheadas",
        sa.Column(
            "pregunta_normalizada",
            sa.String(200),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index(
        "ix_cache_clave",
        "respuestas_cacheadas",
        ["prompt_hash", "pregunta_normalizada"],
    )


def downgrade() -> None:
    op.drop_index("ix_cache_clave", table_name="respuestas_cacheadas")
    op.drop_column("respuestas_cacheadas", "pregunta_normalizada")
