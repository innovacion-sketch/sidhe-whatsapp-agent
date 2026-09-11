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
# Crear calendarios y darles acceso necesita el alcance completo; se pide
# solo desde scripts/crear_calendarios.py, no en la operación diaria.
ALCANCES_ADMIN = ["https://www.googleapis.com/auth/calendar"]
DURACION_DEFAULT_MIN = 60


def sincronizacion_activa() -> bool:
    return bool(get_settings().google_credentials)


def _construir(alcances: list[str]) -> Any:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(get_settings().google_credentials)
    credenciales = service_account.Credentials.from_service_account_info(
        info, scopes=alcances
    )
    # cache_discovery=False evita warnings y escrituras a disco en el contenedor
    return build("calendar", "v3", credentials=credenciales, cache_discovery=False)


def _servicio() -> Any:
    return _construir(ALCANCES)


def _servicio_admin() -> Any:
    return _construir(ALCANCES_ADMIN)


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


# --- Alta de calendarios (setup, no operación diaria) -----------------------
#
# Estas funciones SÍ propagan el error: las usa un script interactivo que
# necesita saber exactamente qué sucursal falló y por qué.

def _crear_calendario_sync(nombre: str, zona: str) -> str:
    calendario = (
        _servicio_admin()
        .calendars()
        .insert(body={"summary": nombre, "timeZone": zona})
        .execute()
    )
    return calendario["id"]


def _compartir_sync(calendar_id: str, correo: str, rol: str) -> None:
    (
        _servicio_admin()
        .acl()
        .insert(
            calendarId=calendar_id,
            sendNotifications=True,
            body={"role": rol, "scope": {"type": "user", "value": correo}},
        )
        .execute()
    )


async def crear_calendario(nombre: str) -> str:
    """Crea un calendario nuevo, propiedad de la cuenta de servicio."""
    return await asyncio.to_thread(
        _crear_calendario_sync, nombre, get_settings().tz
    )


async def compartir_calendario(calendar_id: str, correo: str, rol: str) -> None:
    """Da acceso a un correo. rol: 'reader', 'writer' u 'owner'.

    Google le manda a ese correo una invitación con el enlace para añadir el
    calendario a su lista; ese clic lo tiene que dar la sucursal.
    """
    await asyncio.to_thread(_compartir_sync, calendar_id, correo, rol)
