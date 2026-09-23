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


def hay_turno_que_cubra(
    turnos_del_dia: list[tuple], inicio: datetime.time, fin: datetime.time
) -> bool:
    """Si algún turno cubre el bloque COMPLETO, de principio a fin.

    De punta a punta a propósito: una cita de 16:30 con el turno terminando
    a las 17:00 deja al cliente a medias.
    """
    return any(
        entrada <= inicio and fin <= salida for entrada, salida in turnos_del_dia
    )


def horas_ofrecibles(
    bloques: list[tuple], turnos_del_dia: list[tuple] | None
) -> list[tuple]:
    """Los bloques que alguien puede atender ese día.

    `turnos_del_dia` en None o vacío significa que esa semana todavía no se
    carga en el rol: entonces se ofrece el horario completo de la tienda,
    igual que siempre. Recortar por falta de datos vaciaría la agenda.
    """
    if not turnos_del_dia:
        return bloques
    return [b for b in bloques if hay_turno_que_cubra(turnos_del_dia, b[0], b[1])]


def slots_faltantes(
    *,
    sucursal_id: int,
    apertura: datetime.time,
    cierre: datetime.time,
    dias_operacion: list[str] | None,
    fechas: list[datetime.date],
    existentes: set[tuple[datetime.date, datetime.time]],
    minutos: int,
    turnos: dict[datetime.date, list[tuple]] | None = None,
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
        del_dia = horas_ofrecibles(bloques, (turnos or {}).get(fecha))
        for hora_inicio, hora_fin in del_dia:
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
    # Turnos programados: solo se ofrecen horas que alguien pueda atender.
    # None = no se pudo consultar, y entonces se ofrece todo como siempre.
    turnos = await asistencias.turnos(hoy, fechas[-1])

    creados = borrados = fuera_de_turno = 0
    async with get_session() as session:
        sucursales = (
            await session.execute(select(Sucursal).where(Sucursal.activa.is_(True)))
        ).scalars().all()

        for sucursal in sucursales:
            # Solo lo de hoy en adelante: la historia de slots crece sin fin
            # y no hace falta leerla para saber qué falta.
            filas_slots = (
                await session.execute(
                    select(
                        Slot.id, Slot.fecha, Slot.hora_inicio, Slot.hora_fin
                    ).where(Slot.sucursal_id == sucursal.id, Slot.fecha >= hoy)
                )
            ).all()
            existentes = {(fecha, hora) for _, fecha, hora, _ in filas_slots}
            # Turnos de ESTA sucursal por fecha. Una fecha sin turnos = esa
            # semana no se ha cargado: se ofrece el horario completo.
            turnos_sucursal = (
                None
                if turnos is None
                else {f: turnos.get((sucursal.nombre, f), []) for f in fechas}
            )
            nuevos = slots_faltantes(
                sucursal_id=sucursal.id,
                apertura=sucursal.horario_apertura,
                cierre=sucursal.horario_cierre,
                dias_operacion=sucursal.dias_operacion,
                fechas=[f for f in fechas if (sucursal.nombre, f) not in sin_personal],
                existentes=existentes,
                minutos=minutos,
                turnos=turnos_sucursal,
            )
            session.add_all(Slot(**n) for n in nuevos)
            creados += len(nuevos)

            # Días que ya no deberían ofrecerse: los que el rol cerró y los
            # de descanso fijo de la sucursal. Pueden tener horarios creados
            # antes del cambio. Se quitan los que nadie reservó; los que ya
            # tienen cita NO se tocan: esa cita hay que atenderla o reubicarla
            # a mano, y para eso está la alerta por correo.
            # Horarios que quedaron fuera del turno de ese día. Solo cuando
            # hay rol cargado: sin rol no se recorta nada.
            if turnos is not None:
                sueltos = [
                    slot_id
                    for slot_id, fecha, h_ini, h_fin in filas_slots
                    if turnos.get((sucursal.nombre, fecha))
                    and not hay_turno_que_cubra(
                        turnos[(sucursal.nombre, fecha)], h_ini, h_fin
                    )
                ]
                if sueltos:
                    fuera_de_turno += (
                        await session.execute(
                            delete(Slot).where(
                                Slot.id.in_(sueltos),
                                Slot.reservados == 0,
                                ~select(Cita.id)
                                .where(Cita.slot_id == Slot.id)
                                .exists(),
                            )
                        )
                    ).rowcount

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
    if borrados or fuera_de_turno:
        logger.info(
            "horarios_retirados_por_falta_de_personal",
            dias_cerrados=borrados,
            fuera_de_turno=fuera_de_turno,
        )
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
