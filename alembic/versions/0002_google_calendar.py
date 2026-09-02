"""Columnas para la sincronizacion con Google Calendar.

Revision ID: 0002
Revises: 0001
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sucursales", sa.Column("calendar_id", sa.String(200), nullable=True))
    op.add_column("citas", sa.Column("google_event_id", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("citas", "google_event_id")
    op.drop_column("sucursales", "calendar_id")
