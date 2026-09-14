"""Respuestas rápidas y cierres de conversación para la bandeja de asesores.

Revision ID: 0004
Revises: 0003
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Punto de partida para el equipo: frases de asesor y respuestas copiadas de
# las FAQs aprobadas (data/faqs.json). A propósito NO van precios: cambian, y
# tenerlos en dos lugares garantiza que algún día uno quede desactualizado.
RESPUESTAS_INICIALES = [
    ("saludo", "¡Hola! Soy asesor de Sidhe Group, con gusto te atiendo. ¿En qué te puedo ayudar?"),
    ("revisando", "Déjame revisar tu caso con el equipo y en un momento te confirmo."),
    ("datos", "Para ubicar tu pedido, ¿me compartes tu nombre completo y la sucursal donde te hiciste el estudio?"),
    ("listas", "¡Buenas noticias! Tus plantillas ya están en la sucursal, puedes pasar a recogerlas en el horario del stand."),
    ("fabricacion", "Tus plantillas siguen en fabricación. En cuanto lleguen a la sucursal te avisamos por este medio."),
    ("entrega", "El tiempo de entrega es de aproximadamente 10 días hábiles en Ciudad de México y hasta 15 días hábiles en sucursales foráneas."),
    ("agendar", "Con gusto te agendo tu estudio de pisada. ¿Qué sucursal o zona te queda mejor?"),
    ("estudio", "El estudio de pisada mide la distribución de presión de tu pie al estar de pie y al caminar, con baropodómetro y escaneo 3D. Con eso se diseñan tus plantillas a la medida."),
    ("adaptacion", "Puede haber un periodo de adaptación de 3 semanas mientras el cuerpo se acostumbra, o en personas sensibles hasta 6 semanas."),
    ("ajustes", "Si no te sientes bien con tus plantillas, se pueden realizar ajustes para mejorar la adaptación durante el periodo inicial."),
    ("garantia", "Tus plantillas incluyen garantía por defectos de fabricación y ajustes iniciales."),
    ("duracion", "En promedio duran entre 8 meses y 1 año, según el uso, peso y actividad. Recomendamos evaluarlas cada 8 a 12 meses."),
    ("limpieza", "Se limpian con un paño húmedo y jabón suave. Evita el calor excesivo y no las sumerjas completamente."),
    ("calzado", "Funcionan principalmente en calzado cerrado como tenis, botas o zapatos casuales."),
    ("receta", "No necesitas receta médica, pero si tienes un diagnóstico previo lo podemos considerar en el diseño."),
    ("deporte", "Sí, también son para deporte: mejoran la estabilidad, reducen el impacto y ayudan a prevenir lesiones."),
    ("sucursal", "Para atención directa puedes comunicarte con tu sucursal. ¿En cuál te atendieron para pasarte el teléfono?"),
    ("gracias", "¡Gracias por escribirnos! Cualquier otra duda, aquí estamos para ayudarte."),
    ("cierre", "¿Hay algo más en lo que te pueda ayudar? Si no, doy por resuelta tu consulta. ¡Que tengas excelente día!"),
]


def upgrade() -> None:
    tabla = op.create_table(
        "respuestas_rapidas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("atajo", sa.String(30), nullable=False, unique=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.bulk_insert(
        tabla, [{"atajo": a, "texto": t} for a, t in RESPUESTAS_INICIALES]
    )

    op.create_table(
        "cierres_conversacion",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canal", sa.String(30), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("cerrada_en", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_cierres_canal_user", "cierres_conversacion", ["canal", "user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_cierres_canal_user", table_name="cierres_conversacion")
    op.drop_table("cierres_conversacion")
    op.drop_table("respuestas_rapidas")
