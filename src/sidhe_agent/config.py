"""Configuración central del servicio vía variables de entorno (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
        """DSN para SQLAlchemy async (tablas de negocio, driver asyncpg)."""
        return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    @property
    def psycopg_url(self) -> str:
        """DSN para el checkpointer/store de LangGraph (driver psycopg 3)."""
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
