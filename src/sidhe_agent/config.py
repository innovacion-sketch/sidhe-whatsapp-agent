"""Configuración central del servicio vía variables de entorno (pydantic-settings)."""

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

# Esquemas que puede traer el DATABASE_URL de un proveedor administrado
ESQUEMAS_POSTGRES = (
    "postgresql+asyncpg://",
    "postgresql+psycopg://",
    "postgresql://",
    "postgres://",
)

# Parámetros de libpq/psycopg que asyncpg rechaza
PARAMS_INCOMPATIBLES_ASYNCPG = frozenset(
    {"sslmode", "channel_binding", "target_session_attrs", "options", "gssencmode"}
)


def _normalizar_esquema(url: str, esquema_destino: str) -> str:
    for esquema in ESQUEMAS_POSTGRES:
        if url.startswith(esquema):
            return esquema_destino + url[len(esquema) :]
    return url


def _sin_parametros(url: str, excluidos: frozenset[str]) -> str:
    partes = urlsplit(url)
    if not partes.query:
        return url
    conservados = [
        (clave, valor)
        for clave, valor in parse_qsl(partes.query, keep_blank_values=True)
        if clave.lower() not in excluidos
    ]
    return urlunsplit(partes._replace(query=urlencode(conservados)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Base de datos (un solo DSN; cada driver ajusta su dialecto)
    database_url: str = "postgresql://postgres:postgres@localhost:5432/sidhe"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+5215638955164"
    twilio_validate_signature: bool = True
    # SID (HX...) del Content Template de recordatorio aprobado por WhatsApp
    # (se crea con scripts/setup_recordatorio_template.py)
    twilio_recordatorio_content_sid: str = ""
    # URL pública del servicio; detrás de un proxy la firma de Twilio se calcula
    # sobre esta URL y no sobre la interna que ve uvicorn.
    public_base_url: str = ""

    # Transcripción de voz
    groq_api_key: str = ""
    openai_api_key: str = ""

    # Embeddings para RAG: voyage | cohere | openai | bge-m3 (stub self-hosted)
    embeddings_provider: str = "openai"
    # Vacío = modelo default del proveedor (voyage-3.5 / embed-multilingual-v3.0
    # / text-embedding-3-small). La dimensión SIEMPRE es 1024 (columna vector).
    embeddings_model: str = ""
    voyage_api_key: str = ""
    cohere_api_key: str = ""
    # Para el stub BGE-M3 self-hosted (Fase futura)
    embeddings_base_url: str = ""

    # API interna (recordatorios vía n8n)
    internal_api_key: str = ""

    # Sistema
    tz: str = "America/Mexico_City"
    log_level: str = "INFO"

    @property
    def sqlalchemy_url(self) -> str:
        """DSN para SQLAlchemy async (tablas de negocio, driver asyncpg).

        Normaliza el DSN que dan los proveedores (Easypanel, Railway, etc.):
        acepta postgres:// y postgresql://, y descarta los parámetros de
        consulta que asyncpg no entiende (sslmode y similares son sintaxis de
        libpq/psycopg, no de asyncpg).
        """
        url = _normalizar_esquema(self.database_url, "postgresql+asyncpg://")
        return _sin_parametros(url, PARAMS_INCOMPATIBLES_ASYNCPG)

    @property
    def psycopg_url(self) -> str:
        """DSN para el checkpointer/store de LangGraph (driver psycopg 3)."""
        return _normalizar_esquema(self.database_url, "postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
