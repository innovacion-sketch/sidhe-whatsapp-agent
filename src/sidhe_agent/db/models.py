"""Tablas de negocio (SQLAlchemy 2.0). El checkpointer y el store de LangGraph
crean sus propias tablas con .setup(); aquí solo vive el dominio de Sidhe.

Todas las tablas se definen desde la Fase 1 (incluidas las de citas y RAG) para
que las fases siguientes no requieran cambios de esquema.
"""

import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
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
    __table_args__ = (
        Index("ix_mensajes_twilio_sid", "twilio_sid"),
        # Cada respuesta busca a qué número escribió el cliente
        Index("ix_mensajes_canal_user", "canal", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canal: Mapped[str] = mapped_column(String(30), nullable=False)
    user_id: Mapped[str] = mapped_column(String(40), nullable=False)
    direccion: Mapped[str] = mapped_column(String(3), nullable=False)  # in | out
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    contenido: Mapped[str] = mapped_column(Text, default="", nullable=False)
    item_id_seleccionado: Mapped[str | None] = mapped_column(String(80))
    twilio_sid: Mapped[str | None] = mapped_column(String(64))
    # Número del negocio al que escribió el cliente (WhatsApp con varios
    # números): la respuesta tiene que salir de ese mismo número.
    numero_negocio: Mapped[str | None] = mapped_column(String(40))
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


class Pedido(Base):
    """Estado de fabricación de las plantillas de cada paciente.

    Es un espejo de la hoja STATUS del Excel de operaciones, no la fuente de
    verdad: se reemplaza completa en cada sincronización. Se busca por
    teléfono (el bot ya conoce el del cliente) y, como respaldo, por nombre
    normalizado + sucursal.
    """

    __tablename__ = "pedidos"
    __table_args__ = (
        Index("ix_pedidos_telefono", "telefono"),
        Index("ix_pedidos_nombre_sucursal", "sucursal", "nombre_normalizado"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha: Mapped[datetime.date | None] = mapped_column(Date)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    # Sin acentos, en mayúsculas y con espacios colapsados, para buscar
    nombre_normalizado: Mapped[str] = mapped_column(String(200), nullable=False)
    sucursal: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    # Últimos 10 dígitos, como vienen en la hoja
    telefono: Mapped[str | None] = mapped_column(String(10))
    status_original: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    # Categoría estable con la que el agente decide qué decir
    status: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    lugar_impresion: Mapped[str | None] = mapped_column(String(80))
    localizacion_final: Mapped[str | None] = mapped_column(String(80))
    envio: Mapped[str | None] = mapped_column(String(120))
    importado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RespuestaRapida(Base):
    """Mensaje ya redactado que el asesor inserta escribiendo /atajo."""

    __tablename__ = "respuestas_rapidas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    atajo: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    creado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actualizado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RespuestaCacheada(Base):
    """Preguntas de catálogo ya contestadas, para no volver a pagarlas.

    Solo entran respuestas que el bot dio SIN consultar ninguna herramienta
    ni datos del cliente: esas valen para cualquiera. Nunca entra nada que
    dependa de quién pregunta (su pedido, su cita, su perfil).

    `prompt_hash` es la huella del system prompt con el que se generó: si
    cambia un precio o un horario en las FAQs, la huella cambia y las
    respuestas viejas dejan de servirse solas, sin tener que acordarse de
    limpiar nada.
    """

    __tablename__ = "respuestas_cacheadas"
    __table_args__ = (
        Index("ix_cache_hash", "prompt_hash"),
        # Para encontrar la pregunta idéntica sin depender de embeddings
        Index("ix_cache_clave", "prompt_hash", "pregunta_normalizada"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pregunta: Mapped[str] = mapped_column(Text, nullable=False)
    # Minúsculas, sin acentos ni signos: "¿A qué hora abren?" -> "a que hora abren"
    pregunta_normalizada: Mapped[str] = mapped_column(
        String(200), default="", nullable=False
    )
    respuesta: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIM))
    prompt_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    usos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    creado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UsoModelo(Base):
    """Tokens gastados por día y por modelo: la factura, medida en casa.

    Una fila por (día, modelo) que se va sumando. En memoria no sirve: se
    reinicia en cada Deploy y entonces el panel muestra un gasto ridículo
    comparado con lo que cobra Anthropic a fin de mes.
    """

    __tablename__ = "uso_modelo"
    __table_args__ = (UniqueConstraint("fecha", "modelo", name="uq_uso_fecha_modelo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    modelo: Mapped[str] = mapped_column(String(60), nullable=False)
    llamadas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Entrada TOTAL, con lo cacheado incluido (así lo reporta LangChain)
    entrada: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    salida: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cache_lectura: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cache_escritura: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class CierreConversacion(Base):
    """Un asesor dio la conversación por resuelta.

    No se guarda un estado "cerrada" en ningún lado: una conversación está
    cerrada si su último cierre es posterior al último mensaje del cliente.
    Así, cuando el cliente vuelve a escribir, se reabre sola sin que nadie
    tenga que acordarse de cambiar nada.
    """

    __tablename__ = "cierres_conversacion"
    __table_args__ = (Index("ix_cierres_canal_user", "canal", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canal: Mapped[str] = mapped_column(String(30), nullable=False)
    user_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cerrada_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
