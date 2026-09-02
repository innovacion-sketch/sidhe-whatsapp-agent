"""Espejo de las citas en el Google Calendar de cada sucursal.

Autenticacion con cuenta de servicio (GOOGLE_CREDENTIALS_JSON): cada sucursal
comparte su calendario de Gmail con el correo de la cuenta de servicio y
guarda ese correo en sucursales.calendar_id.

Regla de oro: la agenda real vive en Postgres. Si Google falla, la cita NO se
pierde ni se bloquea; solo se registra el error y el evento queda sin espejo.
"""

import asyncio
import datetime
import json
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from ..config import get_settings

logger = structlog.get_logger(__name__)

ALCANCES = ["https://www.googleapis.com/auth/calendar.events"]
DURACION_DEFAULT_MIN = 60


def sincronizacion_activa() -> bool:
    return bool(get_settings().google_credentials_json.strip())


def _servicio() -> Any:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(get_settings().google_credentials_json)
    credenciales = service_account.Credentials.from_service_account_info(
        info, scopes=ALCANCES
    )
    # cache_discovery=False evita warnings y escrituras a disco en el contenedor
    return build("calendar", "v3", credentials=credenciales, cache_discovery=False)


def _cuerpo_evento(
    nombre_cliente: str,
    telefono: str,
    folio: int,
    fecha: datetime.date,
    hora_inicio: datetime.time,
    hora_fin: datetime.time,
    direccion: str,
) -> dict:
    tz = get_settings().tz
    inicio = datetime.datetime.combine(fecha, hora_inicio)
    fin = datetime.datetime.combine(fecha, hora_fin)
    minutos = get_settings().google_calendar_recordatorio_min
    return {
        "summary": f"Estudio de pisada - {nombre_cliente}",
        "description": (
            f"Cliente: {nombre_cliente}\n"
            f"Telefono: {telefono}\n"
            f"Folio: {folio}\n"
            "Agendado por el asistente de WhatsApp."
        ),
        "location": direccion,
        "start": {"dateTime": inicio.isoformat(), "timeZone": tz},
        "end": {"dateTime": fin.isoformat(), "timeZone": tz},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": minutos}],
        },
    }


def _crear_sync(calendar_id: str, cuerpo: dict) -> str:
    evento = (
        _servicio().events().insert(calendarId=calendar_id, body=cuerpo).execute()
    )
    return evento["id"]


def _borrar_sync(calendar_id: str, event_id: str) -> None:
    _servicio().events().delete(calendarId=calendar_id, eventId=event_id).execute()


async def crear_evento(
    calendar_id: str | None,
    *,
    nombre_cliente: str,
    telefono: str,
    folio: int,
    fecha: datetime.date,
    hora_inicio: datetime.time,
    hora_fin: datetime.time,
    direccion: str,
) -> str | None:
    """Crea el evento espejo. Devuelve su id, o None si no aplica o falla."""
    if not calendar_id or not sincronizacion_activa():
        return None
    cuerpo = _cuerpo_evento(
        nombre_cliente, telefono, folio, fecha, hora_inicio, hora_fin, direccion
    )
    try:
        event_id = await asyncio.to_thread(_crear_sync, calendar_id, cuerpo)
        logger.info("evento_calendario_creado", folio=folio, calendar_id=calendar_id)
        return event_id
    except Exception:
        logger.exception(
            "error_creando_evento_calendario", folio=folio, calendar_id=calendar_id
        )
        return None


async def borrar_evento(calendar_id: str | None, event_id: str | None) -> bool:
    if not calendar_id or not event_id or not sincronizacion_activa():
        return False
    try:
        await asyncio.to_thread(_borrar_sync, calendar_id, event_id)
        logger.info("evento_calendario_borrado", event_id=event_id)
        return True
    except Exception:
        logger.exception("error_borrando_evento_calendario", event_id=event_id)
        return False
