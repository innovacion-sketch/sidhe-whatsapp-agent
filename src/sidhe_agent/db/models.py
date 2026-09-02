"""Tablas de negocio (SQLAlchemy 2.0). El checkpointer y el store de LangGraph
crean sus propias tablas con .setup(); aquí solo vive el dominio de Sidhe.

Todas las tablas se definen desde la Fase 1 (incluidas las de citas y RAG) para
que las fases siguientes no requieran cambios de esquema.
"""

import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024

EstadoCita = Enum(
    "confirmada", "cancelada", "completada", "no_asistio", name="estado_cita"
)
EstadoEscalamiento = Enum("pendiente", "atendido", name="estado_escalamiento")


class Base(DeclarativeBase):
    pass


class Sucursal(Base):
    __tablename__ = "sucursales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    # Nombres alternativos para matching por texto libre ("Perisur", "peri sur", ...)
    alias: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    ciudad: Mapped[str] = mapped_column(String(80), nullable=False)
    estado: Mapped[str] = mapped_column(String(80), nullable=False)
    # Agrupa sucursales en regiones de máx. 10 para el primer list-picker
    zona: Mapped[str] = mapped_column(String(80), nullable=False)
    direccion: Mapped[str] = mapped_column(Text, nullable=False)
    horario_apertura: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    horario_cierre: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    dias_operacion: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    telefono: Mapped[str | None] = mapped_column(String(20))
    # Correo del calendario de Google de la sucursal (para Gmail, el
    # calendar_id ES el correo). Vacio = no se sincroniza esa sucursal.
    calendar_id: Mapped[str | None] = mapped_column(String(200))
    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (Index("ix_slots_sucursal_fecha", "sucursal_id", "fecha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("sucursales.id"), nullable=False)
    fecha: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    hora_inicio: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    hora_fin: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    capacidad: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reservados: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Cita(Base):
    __tablename__ = "citas"
    __table_args__ = (
        # Evita dobles reservas del mismo cliente en el mismo slot
        UniqueConstraint("slot_id", "cliente_telefono", name="uq_citas_slot_telefono"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"), nullable=False)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("sucursales.id"), nullable=False)
    cliente_telefono: Mapped[str] = mapped_column(String(20), nullable=False)
    cliente_nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    estado: Mapped[str] = mapped_column(EstadoCita, default="confirmada", nullable=False)
    canal: Mapped[str] = mapped_column(String(30), nullable=False)
    # Evento espejo en Google Calendar (para poder borrarlo al cancelar)
    google_event_id: Mapped[str | None] = mapped_column(String(200))
    creada_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actualizada_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Mensaje(Base):
    """Auditoría/analytics. La memoria conversacional vive en los checkpoints."""

    __tablename__ = "mensajes"
    __table_args__ = (Index("ix_mensajes_twilio_sid", "twilio_sid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canal: Mapped[str] = mapped_column(String(30), nullable=False)
    user_id: Mapped[str] = mapped_column(String(40), nullable=False)
    direccion: Mapped[str] = mapped_column(String(3), nullable=False)  # in | out
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    contenido: Mapped[str] = mapped_column(Text, default="", nullable=False)
    item_id_seleccionado: Mapped[str | None] = mapped_column(String(80))
    twilio_sid: Mapped[str | None] = mapped_column(String(64))
    creado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Documento(Base):
    """Fuente documental para RAG (Fase 4)."""

    __tablename__ = "documentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    fuente: Mapped[str | None] = mapped_column(String(300))
    metadatos: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    creado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos.id"), nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIM))
    metadatos: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # El índice HNSW sobre embedding se crea en la migración inicial.


class Escalamiento(Base):
    __tablename__ = "escalamientos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canal: Mapped[str] = mapped_column(String(30), nullable=False)
    user_id: Mapped[str] = mapped_column(String(40), nullable=False)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    contexto_resumen: Mapped[str] = mapped_column(Text, default="", nullable=False)
    estado: Mapped[str] = mapped_column(
        EstadoEscalamiento, default="pendiente", nullable=False
    )
    creado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
