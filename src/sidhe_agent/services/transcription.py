"""Transcripción de notas de voz: Whisper vía Groq, con fallback a OpenAI.

El media de Twilio requiere autenticación básica (account_sid, auth_token)
para descargarse.
"""

import httpx
import structlog

from ..config import get_settings

logger = structlog.get_logger(__name__)

MODELO_GROQ = "whisper-large-v3-turbo"
MODELO_OPENAI = "whisper-1"


class TranscripcionError(Exception):
    pass


def _nombre_archivo(content_type: str | None) -> str:
    extension = {
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "mp4",
        "audio/amr": "amr",
        "audio/wav": "wav",
    }.get((content_type or "").split(";")[0].strip(), "ogg")
    return f"nota_voz.{extension}"


async def _descargar_media(media_url: str) -> bytes:
    settings = get_settings()
    async with httpx.AsyncClient(
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        respuesta = await client.get(media_url)
        respuesta.raise_for_status()
        return respuesta.content


async def _via_groq(datos: bytes, nombre: str) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=get_settings().groq_api_key)
    resultado = await client.audio.transcriptions.create(
        file=(nombre, datos),
        model=MODELO_GROQ,
        language="es",
    )
    return resultado.text.strip()


async def _via_openai(datos: bytes, nombre: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=get_settings().openai_api_key)
    resultado = await client.audio.transcriptions.create(
        file=(nombre, datos),
        model=MODELO_OPENAI,
        language="es",
    )
    return resultado.text.strip()


async def transcribir_audio(media_url: str, content_type: str | None = None) -> str:
    """Descarga el audio de Twilio y lo transcribe a texto en español."""
    settings = get_settings()
    datos = await _descargar_media(media_url)
    nombre = _nombre_archivo(content_type)

    if settings.groq_api_key:
        try:
            return await _via_groq(datos, nombre)
        except Exception:
            logger.exception("groq_transcripcion_fallo")
            if not settings.openai_api_key:
                raise
    if settings.openai_api_key:
        return await _via_openai(datos, nombre)

    raise TranscripcionError(
        "Sin proveedor de transcripción: define GROQ_API_KEY u OPENAI_API_KEY"
    )
