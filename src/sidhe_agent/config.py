"""Configuración central del servicio vía variables de entorno (pydantic-settings)."""

import base64
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

    # Si nadie del equipo contesta un escalamiento en estas horas, el bot
    # retoma la conversación: un cliente en silencio es peor que un bot.
    horas_reactivar_bot: int = 4

    # Google Calendar: credenciales de la cuenta de servicio. Acepta el JSON
    # en una sola línea O el mismo JSON en base64 (ver `google_credentials`).
    # Vacío = sincronización desactivada.
    google_credentials_json: str = ""
    # Minutos de recordatorio del evento (popup) en el calendario del stand
    google_calendar_recordatorio_min: int = 60

    # Agenda: cuántos días hacia adelante se mantienen horarios abiertos. El
    # servicio los rellena solo; sin esto la agenda se va acabando día a día.
    # Debe ser mayor que la ventana que ofrece el bot (14 días). 0 = apagado.
    agenda_dias_adelante: int = 21
    # Duración de cada cita. NO cambiar con horarios ya generados: los
    # bloques nuevos se encimarían con los viejos.
    agenda_minutos_por_cita: int = 60

    # Google Sheets: hoja de operaciones con el estado de los pedidos. Se lee
    # con la MISMA cuenta de servicio (compartir el Sheet con ese correo).
    # El id es lo que va entre /d/ y /edit en la URL. Vacío = sin sincronizar.
    google_sheets_pedidos_id: str = ""
    google_sheets_pedidos_hoja: str = "STATUS"
    # Solo se guardan los pedidos de estos meses; lo anterior es historial.
    pedidos_meses_historial: int = 3
    # Cada cuántas horas relee la hoja el propio servicio (0 = nunca, y
    # entonces hay que sincronizar desde fuera con /internal/pedidos/sincronizar)
    pedidos_sincronizar_cada_horas: int = 2

    # Meta: Instagram DM y Facebook Messenger. Cada red se activa sola al
    # poner su token; sin token, ese canal simplemente no existe.
    # El app secret valida la firma de los webhooks y el verify token es el
    # que se teclea al dar de alta la URL en el panel de Meta.
    meta_app_secret: str = ""
    meta_verify_token: str = ""
    meta_token_instagram: str = ""
    meta_token_messenger: str = ""

    # n8n: webhook al que se avisa cada cita creada/cancelada (para Sheets).
    # Vacío = no se envía nada.
    n8n_webhook_citas: str = ""

    # Sistema
    tz: str = "America/Mexico_City"
    log_level: str = "INFO"

    @property
    def google_credentials(self) -> str:
        """JSON de la cuenta de servicio, venga como JSON o como base64.

        Los paneles de despliegue guardan cada variable en un solo renglón y
        rompen un JSON multilínea; base64 evita comillas, llaves y saltos de
        línea sin necesidad de otra variable.
        """
        valor = self.google_credentials_json.strip()
        if not valor or valor.startswith("{"):
            return valor
        try:
            return base64.b64decode(valor, validate=True).decode("utf-8")
        except Exception:
            return valor

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
