"""Aviso a n8n de cada cita creada o cancelada (para Google Sheets, etc.).

Push en vez de polling: n8n recibe el evento en su webhook y lo agrega a la
hoja. Como toda integracion externa, nunca bloquea ni rompe el agendado: si
el webhook falla, solo se registra el error.
"""

import httpx
import structlog

from ..config import get_settings

logger = structlog.get_logger(__name__)

TIMEOUT = 10.0


def webhook_activo() -> bool:
    return bool(get_settings().n8n_webhook_citas.strip())


async def avisar_cita(evento: str, datos: dict) -> bool:
    """Envia {evento, cita} al webhook de n8n. evento: creada | cancelada."""
    url = get_settings().n8n_webhook_citas.strip()
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            respuesta = await client.post(
                url,
                json={"evento": evento, "cita": datos},
                headers={"X-API-Key": get_settings().internal_api_key},
            )
            respuesta.raise_for_status()
        logger.info("n8n_avisado", evento=evento, folio=datos.get("folio"))
        return True
    except Exception:
        logger.exception("error_avisando_n8n", evento=evento, folio=datos.get("folio"))
        return False
