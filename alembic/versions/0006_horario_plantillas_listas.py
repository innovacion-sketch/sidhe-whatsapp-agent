"""Corrige el horario de /plantillas-listas: 11am a 9pm, no a 8pm.

El documento del equipo decía "de 11am a 8pm" solo en esta respuesta; /cita,
/horario y las 28 sucursales dicen 11:00 a 21:00 todos los días, y el negocio
confirmó que ese es el correcto.

Solo cambia el texto si sigue siendo el original de la 0005: si un asesor ya
la corrigió o la reescribió desde el panel, se respeta.

Revision ID: 0006
Revises: 0005
"""

import importlib.util
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ATAJO = "plantillas-listas"
HORARIO_VIEJO = "de 11am a 8pm"
HORARIO_NUEVO = "de 11am a 9pm"


def _texto_original() -> str:
    ruta = Path(__file__).with_name("0005_respuestas_del_equipo.py")
    spec = importlib.util.spec_from_file_location("migracion_0005", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return dict(modulo.RESPUESTAS_EQUIPO)[ATAJO]


def texto_corregido() -> str:
    original = _texto_original()
    assert HORARIO_VIEJO in original, "el texto de la 0005 ya no trae el horario viejo"
    return original.replace(HORARIO_VIEJO, HORARIO_NUEVO)


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE respuestas_rapidas SET texto = :nuevo, actualizado_en = now() "
            "WHERE atajo = :a AND texto = :viejo"
        ),
        {"a": ATAJO, "viejo": _texto_original(), "nuevo": texto_corregido()},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE respuestas_rapidas SET texto = :viejo "
            "WHERE atajo = :a AND texto = :nuevo"
        ),
        {"a": ATAJO, "viejo": _texto_original(), "nuevo": texto_corregido()},
    )
