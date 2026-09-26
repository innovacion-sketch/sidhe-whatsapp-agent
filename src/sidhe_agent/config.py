"""Configuración central del servicio vía variables de entorno (pydantic-settings)."""

import base64
from functools import lru_cache
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

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
    # Base del sistema de asistencias, SOLO LECTURA. Vacío = el bot agenda
    # sin consultar el rol de personal (comportamiento de siempre).
    # Dos formas: el DSN completo, o los datos por separado —que es lo
    # recomendado, porque una contraseña con @ o # rompe el DSN si no se
    # codifica, y ese error solo se ve como "password authentication failed".
    asistencias_database_url: str = ""
    asistencias_db_host: str = ""
    asistencias_db_port: int = 5432
    asistencias_db_user: str = ""
    asistencias_db_password: str = ""
    asistencias_db_name: str = "asistencias"

    # Alertas por correo (mismas variables que usa el sistema de asistencias,
    # para no dar de alta otra cuenta: GMAIL_USER, GMAIL_APP_PASSWORD,
    # EMAIL_ALERTAS_TO, EMAIL_ALERTAS_CC)
    gmail_user: str = ""
    gmail_app_password: str = ""
    email_alertas_to: str = ""
    email_alertas_cc: str = ""
    # A qué hora se revisa si las citas de hoy tienen quién las atienda
    alerta_citas_desde: int = 11
    alerta_citas_hasta: int = 19
    # Repaso completo de las citas de las próximas semanas: 0 = lunes
    revision_semanal_dia: int = 0
    revision_semanal_hora: int = 9
    revision_semanal_dias: int = 21

    # Anthropic
    anthropic_api_key: str = ""
    # Modelo de todos los días. La comparación con 30 preguntas reales
    # (scripts/comparar_modelos.py) dio empate con Sonnet y 31% más rápido.
    anthropic_model: str = "claude-haiku-4-5"
    # El flujo de citas son seis o siete pasos encadenados y es lo único que
    # esa comparación NO midió; ahí no se arriesga. Vacío = usar el de arriba.
    anthropic_model_agenda: str = "claude-sonnet-5"
    # Modelo de las tareas internas que el cliente nunca ve (extracción de
    # perfil y resúmenes). Corre en CADA mensaje, así que con el modelo
    # grande se lleva casi un tercio del gasto sin mejorar la respuesta.
    anthropic_model_utilitario: str = "claude-haiku-4-5"

    # Cuánto vive el caché de prompt de Anthropic. "5m" es el default de la
    # API y es corto para WhatsApp: el cliente tarda minutos en contestar y
    # para entonces el prompt ya caducó, así que cada mensaje se vuelve a
    # escribir a 1.25x en vez de leerse a 0.1x. "1h" cuesta 2x escribir y
    # conviene en cuanto una conversación pasa del par de mensajes; se
    # decide con datos, no a ojo (scripts/tasa_cache.py).
    anthropic_cache_ttl: str = "5m"

    # Caché de respuestas de catálogo (ver services/cache_respuestas.py).
    # Apagarlo es poner CACHE_RESPUESTAS_ACTIVO=false y desplegar.
    cache_respuestas_activo: bool = True
    # Qué tan parecida tiene que ser la pregunta para reusar la respuesta.
    # Alto a propósito: 0.97 es "la misma pregunta escrita distinto".
    cache_respuestas_similitud: float = 0.97
    # Una respuesta guardada caduca sola, por si cambió algo fuera del prompt
    cache_respuestas_dias: int = 30

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

    # WhatsApp directo con Meta (Cloud API), sin Twilio. Se migra número por
    # número: aquí van SOLO los que ya están en nuestra WABA, con su
    # phone_number_id. Los demás siguen saliendo por Twilio.
    #   WHATSAPP_CLOUD_NUMEROS="+5215638950202=123456789012345"
    # El webhook usa META_APP_SECRET y META_VERIFY_TOKEN (la misma app).
    whatsapp_cloud_token: str = ""
    whatsapp_cloud_numeros: str = ""
    # La plantilla de recordatorio, creada con scripts/migrar_a_meta.py. Otro
    # nombre que la de Twilio ("sidhe_recordatorio_cita"), que ya existe en
    # la WABA SIDHE GRAPHICS: con el mismo, Meta rechaza el alta.
    whatsapp_cloud_plantilla_recordatorio: str = "sidhe_recordatorio_cita_botones"
    whatsapp_cloud_idioma_plantilla: str = "es_MX"

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

    # Segundos de silencio que se esperan antes de contestar un texto, por si
    # el cliente manda otro enseguida (services/rafagas.py). 0 = contestar al
    # instante, uno por uno. El tope evita que quien escribe sin parar espere
    # de más.
    espera_rafaga_segundos: float = 4.0
    espera_rafaga_maximo_segundos: float = 15.0

    # Cuándo hay asesores para contestar un escalamiento, por día. Fuera de
    # este horario el bot ya no promete "pronto": dice cuándo le contestan
    # y ofrece el teléfono de la sucursal (services/horario_asesores.py).
    # Reglas separadas por ";", días en rango o con coma, horas HH o HH:MM.
    asesores_horario: str = "lunes-viernes 10-18; sabado-domingo 10-13"

    # Si nadie del equipo contesta un escalamiento en estas horas, el bot
    # retoma la conversación: un cliente en silencio es peor que un bot.
    # Son horas DE ATENCIÓN (asesores_horario), no de reloj: de noche no
    # corren, porque de noche nadie podía contestar.
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

    # Excel de citas: las sucursales agendan desde una pestaña y el bot lo
    # mete a la misma base (services/agenda_excel.py). Vacío = apagado. La
    # cuenta de servicio necesita ser EDITORA del archivo.
    citas_sheet_id: str = ""
    citas_sheet_pestana: str = "Agendar"
    citas_sheet_cada_segundos: int = 60

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
    def whatsapp_cloud_mapa(self) -> dict[str, str]:
        """{"+5215638950202": "123456789012345"} a partir de WHATSAPP_CLOUD_NUMEROS.

        Un par mal escrito revienta: si se leyera a medias, las respuestas
        de ese número saldrían por Twilio, que ya no lo tiene.
        """
        mapa = {}
        for par in filter(None, (p.strip() for p in self.whatsapp_cloud_numeros.split(";"))):
            numero, _, phone_id = par.partition("=")
            digitos = "".join(c for c in numero if c.isdigit())
            if not digitos or not phone_id.strip().isdigit():
                raise ValueError(f"WHATSAPP_CLOUD_NUMEROS mal escrito: {par!r}")
            mapa[f"+{digitos}"] = phone_id.strip()
        return mapa

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
    def asistencias_url(self) -> str:
        """DSN del sistema de asistencias, o "" si no está configurado.

        Arma la URL a partir de los datos sueltos codificando usuario y
        contraseña, así se pueden copiar tal cual vienen del otro servicio.
        """
        if self.asistencias_database_url:
            return _normalizar_esquema(
                self.asistencias_database_url, "postgresql+asyncpg://"
            )
        if not (self.asistencias_db_host and self.asistencias_db_user):
            return ""
        usuario = quote_plus(self.asistencias_db_user)
        clave = quote_plus(self.asistencias_db_password)
        return (
            f"postgresql+asyncpg://{usuario}:{clave}"
            f"@{self.asistencias_db_host}:{self.asistencias_db_port}"
            f"/{self.asistencias_db_name}"
        )

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
