"""Mantiene abierta la agenda: que siempre haya horarios por delante.

Los horarios de cita no se calculan al vuelo, son filas de la tabla `slots`
generadas por adelantado. Si nadie genera más, la agenda se va acabando sola
día con día hasta que el bot solo ofrece "lunes y martes" y el cliente no
puede agendar la semana siguiente. Eso ya pasó en producción.

Por eso el servicio rellena la ventana hacia adelante por su cuenta, igual
que sincroniza la hoja de pedidos. Es idempotente: un horario que ya existe
—esté libre o reservado— nunca se duplica ni se toca.
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete, select

from ..config import get_settings
from ..db.models import Cita, Slot, Sucursal
from ..db.session import get_session
from . import asistencias

logger = structlog.get_logger(__name__)

DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def horas_del_dia(
    apertura: datetime.time, cierre: datetime.time, minutos: int
) -> list[tuple[datetime.time, datetime.time]]:
    """Bloques de `minutos` que caben completos dentro del horario del stand."""
    bloques = []
    cursor = datetime.datetime.combine(datetime.date.min, apertura)
    fin_dia = datetime.datetime.combine(datetime.date.min, cierre)
    paso = datetime.timedelta(minutes=minutos)
    while cursor + paso <= fin_dia:
        bloques.append((cursor.time(), (cursor + paso).time()))
        cursor += paso
    return bloques


def slots_faltantes(
    *,
    sucursal_id: int,
    apertura: datetime.time,
    cierre: datetime.time,
    dias_operacion: list[str] | None,
    fechas: list[datetime.date],
    existentes: set[tuple[datetime.date, datetime.time]],
    minutos: int,
) -> list[dict]:
    """Los horarios que deberían existir en esas fechas y todavía no existen.

    Se compara por (fecha, hora de inicio). Ojo: si algún día se cambia la
    duración de las citas, los bloques nuevos no coinciden con los viejos y
    se encimarían; en ese caso hay que limpiar los slots futuros libres antes.
    """
    abiertos = dias_operacion or DIAS_SEMANA
    bloques = horas_del_dia(apertura, cierre, minutos)
    nuevos = []
    for fecha in fechas:
        if DIAS_SEMANA[fecha.weekday()] not in abiertos:
            continue
        for hora_inicio, hora_fin in bloques:
            if (fecha, hora_inicio) in existentes:
                continue
            nuevos.append({
                "sucursal_id": sucursal_id,
                "fecha": fecha,
                "hora_inicio": hora_inicio,
                "hora_fin": hora_fin,
                "capacidad": 1,
                "reservados": 0,
            })
    return nuevos


def dias_a_retirar(
    nombre: str,
    fechas: list[datetime.date],
    sin_personal: set[tuple[str, datetime.date]],
    dias_operacion: list | None,
) -> list[datetime.date]:
    """Fechas que ya no deberían ofrecerse en esa sucursal.

    Son las que el rol de personal cerró y las de descanso fijo. Ojo con la
    lista vacía: igual que al generar, significa "abre todos los días", no
    "no abre ninguno" — lo contrario borraría la agenda entera.
    """
    abiertos = set(dias_operacion or DIAS_SEMANA)
    return [
        fecha
        for fecha in fechas
        if (nombre, fecha) in sin_personal
        or DIAS_SEMANA[fecha.weekday()] not in abiertos
    ]


async def asegurar_slots(dias: int, minutos: int = 60) -> int:
    """Rellena hasta `dias` hacia adelante para todas las sucursales activas.

    Devuelve cuántos horarios creó (0 si la agenda ya estaba completa).
    """
    hoy = datetime.datetime.now(ZoneInfo(get_settings().tz)).date()
    fechas = [hoy + datetime.timedelta(days=n) for n in range(0, dias + 1)]
    # Días que el rol de personal da por cerrados. Vacío si no hay sistema de
    # asistencias configurado o si no se pudo consultar: entonces se agenda
    # como siempre, que es mejor que quedarse sin citas por falta de datos.
    sin_personal = await asistencias.dias_sin_personal(hoy, fechas[-1])

    creados = borrados = 0
    async with get_session() as session:
        sucursales = (
            await session.execute(select(Sucursal).where(Sucursal.activa.is_(True)))
        ).scalars().all()

        for sucursal in sucursales:
            # Solo lo de hoy en adelante: la historia de slots crece sin fin
            # y no hace falta leerla para saber qué falta.
            existentes = set(
                (
                    await session.execute(
                        select(Slot.fecha, Slot.hora_inicio).where(
                            Slot.sucursal_id == sucursal.id, Slot.fecha >= hoy
                        )
                    )
                ).all()
            )
            nuevos = slots_faltantes(
                sucursal_id=sucursal.id,
                apertura=sucursal.horario_apertura,
                cierre=sucursal.horario_cierre,
                dias_operacion=sucursal.dias_operacion,
                fechas=[f for f in fechas if (sucursal.nombre, f) not in sin_personal],
                existentes=existentes,
                minutos=minutos,
            )
            session.add_all(Slot(**n) for n in nuevos)
            creados += len(nuevos)

            # Días que ya no deberían ofrecerse: los que el rol cerró y los
            # de descanso fijo de la sucursal. Pueden tener horarios creados
            # antes del cambio. Se quitan los que nadie reservó; los que ya
            # tienen cita NO se tocan: esa cita hay que atenderla o reubicarla
            # a mano, y para eso está la alerta por correo.
            cerrados = dias_a_retirar(
                sucursal.nombre, fechas, sin_personal, sucursal.dias_operacion
            )
            if cerrados:
                borrados += (
                    await session.execute(
                        delete(Slot).where(
                            Slot.sucursal_id == sucursal.id,
                            Slot.fecha.in_(cerrados),
                            Slot.reservados == 0,
                            ~select(Cita.id)
                            .where(Cita.slot_id == Slot.id)
                            .exists(),
                        )
                    )
                ).rowcount

        await session.commit()
    if borrados:
        logger.info("horarios_retirados_por_falta_de_personal", horarios=borrados)
    return creados


async def mantener_agenda_abierta(
    dias: int, minutos: int, cada_horas: float = 6, espera_inicial: float = 20.0
) -> None:
    """Rellena la agenda al arrancar y luego cada pocas horas, para siempre.

    Ningún fallo la detiene: si la base no responde, se registra y se
    reintenta al siguiente ciclo.
    """
    if dias <= 0:
        logger.info("agenda_automatica_apagada")
        return

    logger.info("agenda_automatica_programada", dias_adelante=dias, cada_horas=cada_horas)
    await asyncio.sleep(espera_inicial)
    while True:
        try:
            creados = await asegurar_slots(dias, minutos)
            logger.info("agenda_rellenada", horarios_creados=creados, dias_adelante=dias)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("agenda_error_al_rellenar")
        await asyncio.sleep(cada_horas * 3600)
