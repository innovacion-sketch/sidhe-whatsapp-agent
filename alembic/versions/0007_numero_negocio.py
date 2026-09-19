"""Número del negocio en cada mensaje, para operar con varios números de WhatsApp.

Con el 5164 y el 0202 activos a la vez, al cliente se le contesta desde el
número al que escribió; si no, recibiría la respuesta de un contacto que no
conoce. Los mensajes anteriores quedan en NULL: son del número de siempre.

Revision ID: 0007
Revises: 0006
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mensajes", sa.Column("numero_negocio", sa.String(40), nullable=True))
    # Cada respuesta busca a qué número escribió el cliente; sin índice sería
    # recorrer la tabla completa por cada mensaje que manda el bot.
    op.create_index("ix_mensajes_canal_user", "mensajes", ["canal", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_mensajes_canal_user", table_name="mensajes")
    op.drop_column("mensajes", "numero_negocio")
