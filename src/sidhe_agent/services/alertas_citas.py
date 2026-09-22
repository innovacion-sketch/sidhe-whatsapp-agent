"""Aviso cuando hay citas hoy en una sucursal donde nadie abrió.

El sistema de asistencias ya avisa que un stand está descubierto. Lo que no
sabe es que además hay clientes con cita para ese día: eso solo lo sabe el
bot. Este aviso junta las dos mitades y manda la lista con nombre y
teléfono, para que alguien pueda hablarles antes de que hagan el viaje.

Se manda **una vez por sucursal y por día**: la idea es que alguien actúe,
no llenar la bandeja cada media hora.
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..db.models import Cita, Slot, Sucursal
from ..db.session import get_session
from ..tools.citas import fecha_legible
from . import asistencias, correo

logger = structlog.get_logger(__name__)

# (fecha, sucursal) ya avisados, para no repetir el mismo día
_avisadas: set[tuple[datetime.date, str]] = set()


def redactar(sucursal: str, fecha: datetime.date, citas: list[dict], hora: str) -> tuple[str, str]:
    """El asunto y el cuerpo del correo. Separado para poder probarlo."""
    asunto = f"⚠️ {len(citas)} cita(s) hoy en {sucursal} y el stand no ha abierto"
    lineas = [
        f"A las {hora} nadie ha marcado entrada en {sucursal},",
        f"y hay {len(citas)} cita(s) agendadas para hoy {fecha_legible(fecha)}:",
        "",
    ]
    for cita in citas:
        lineas.append(
            f"  {cita['hora']}  {cita['cliente']}  {cita['telefono']}  "
            f"(folio {cita['folio']})"
        )
    lineas += [
        "",
        "Conviene hablarles antes de que lleguen a una tienda cerrada.",
        "",
        "-- Bot de WhatsApp de Sidhe",
    ]
    return asunto, "\n".join(lineas)


async def citas_de_hoy(hoy: datetime.date) -> dict[str, list[dict]]:
    """Citas confirmadas de hoy, agrupadas por nombre de sucursal."""
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Cita, Slot, Sucursal)
                .join(Slot, Cita.slot_id == Slot.id)
                .join(Sucursal, Cita.sucursal_id == Sucursal.id)
                .where(Cita.estado == "confirmada", Slot.fecha == hoy)
                .order_by(Slot.hora_inicio)
            )
        ).all()
    por_sucursal: dict[str, list[dict]] = {}
    for cita, slot, sucursal in filas:
        por_sucursal.setdefault(sucursal.nombre, []).append(
            {
                "hora": slot.hora_inicio.strftime("%H:%M"),
                "cliente": cita.cliente_nombre,
                "telefono": cita.cliente_telefono,
                "folio": cita.id,
            }
        )
    return por_sucursal


async def revisar() -> int:
    """Revisa y avisa. Devuelve cuántos correos mandó."""
    ajustes = get_settings()
    ahora = datetime.datetime.now(ZoneInfo(ajustes.tz))
    citas = await citas_de_hoy(ahora.date())
    if not citas:
        return 0
    descubiertas = await asistencias.sucursales_sin_checada(
        ahora.date(), ahora.time()
    )
    if not descubiertas:
        # None (no se pudo consultar) o vacío (todo cubierto): no se inventa
        return 0

    enviados = 0
    for sucursal in sorted(descubiertas & citas.keys()):
        if (ahora.date(), sucursal) in _avisadas:
            continue
        asunto, cuerpo = redactar(
            sucursal, ahora.date(), citas[sucursal], ahora.strftime("%H:%M")
        )
        if await correo.enviar(asunto, cuerpo):
            _avisadas.add((ahora.date(), sucursal))
            enviados += 1
            logger.warning(
                "citas_sin_personal_avisadas",
                sucursal=sucursal,
                citas=len(citas[sucursal]),
            )
    return enviados


async def vigilar(cada_minutos: int = 30, espera_inicial: float = 60.0) -> None:
    """Revisa cada tanto durante el horario de tiendas. No muere por errores."""
    await asyncio.sleep(espera_inicial)
    ajustes = get_settings()
    while True:
        try:
            hora = datetime.datetime.now(ZoneInfo(ajustes.tz)).hour
            if ajustes.alerta_citas_desde <= hora <= ajustes.alerta_citas_hasta:
                await revisar()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error_vigilando_citas_sin_personal")
        await asyncio.sleep(cada_minutos * 60)
