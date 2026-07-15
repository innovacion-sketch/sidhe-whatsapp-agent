"""Esquema inicial: negocio completo (sucursales, slots, citas, mensajes,
documentos, chunks, escalamientos) + extensiones vector y unaccent.

Revision ID: 0001
Revises:
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

estado_cita = sa.Enum(
    "confirmada", "cancelada", "completada", "no_asistio", name="estado_cita"
)
estado_escalamiento = sa.Enum("pendiente", "atendido", name="estado_escalamiento")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    op.create_table(
        "sucursales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("alias", JSONB(), nullable=False, server_default="[]"),
        sa.Column("ciudad", sa.String(80), nullable=False),
        sa.Column("estado", sa.String(80), nullable=False),
        sa.Column("zona", sa.String(80), nullable=False),
        sa.Column("direccion", sa.Text(), nullable=False),
        sa.Column("horario_apertura", sa.Time(), nullable=False),
        sa.Column("horario_cierre", sa.Time(), nullable=False),
        sa.Column("dias_operacion", JSONB(), nullable=False, server_default="[]"),
        sa.Column("telefono", sa.String(20)),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=False
        ),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("hora_inicio", sa.Time(), nullable=False),
        sa.Column("hora_fin", sa.Time(), nullable=False),
        sa.Column("capacidad", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reservados", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_slots_sucursal_fecha", "slots", ["sucursal_id", "fecha"])

    op.create_table(
        "citas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slot_id", sa.Integer(), sa.ForeignKey("slots.id"), nullable=False),
        sa.Column(
            "sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=False
        ),
        sa.Column("cliente_telefono", sa.String(20), nullable=False),
        sa.Column("cliente_nombre", sa.String(120), nullable=False),
        sa.Column("estado", estado_cita, nullable=False, server_default="confirmada"),
        sa.Column("canal", sa.String(30), nullable=False),
        sa.Column(
            "creada_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "actualizada_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("slot_id", "cliente_telefono", name="uq_citas_slot_telefono"),
    )

    op.create_table(
        "mensajes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canal", sa.String(30), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("direccion", sa.String(3), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("contenido", sa.Text(), nullable=False, server_default=""),
        sa.Column("item_id_seleccionado", sa.String(80)),
        sa.Column("twilio_sid", sa.String(64)),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_mensajes_twilio_sid", "mensajes", ["twilio_sid"])

    op.create_table(
        "documentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("fuente", sa.String(300)),
        sa.Column("metadatos", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "documento_id", sa.Integer(), sa.ForeignKey("documentos.id"), nullable=False
        ),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024)),
        sa.Column("metadatos", JSONB(), nullable=False, server_default="{}"),
    )
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "escalamientos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canal", sa.String(30), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("contexto_resumen", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "estado", estado_escalamiento, nullable=False, server_default="pendiente"
        ),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("escalamientos")
    op.drop_table("chunks")
    op.drop_table("documentos")
    op.drop_table("mensajes")
    op.drop_table("citas")
    op.drop_table("slots")
    op.drop_table("sucursales")
    estado_cita.drop(op.get_bind(), checkfirst=True)
    estado_escalamiento.drop(op.get_bind(), checkfirst=True)
