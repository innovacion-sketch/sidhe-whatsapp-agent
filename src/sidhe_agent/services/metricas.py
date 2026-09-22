"""Metricas del panel: se calculan sobre las tablas de auditoria.

Todo sale de `mensajes` (que guarda cada mensaje entrante y saliente con su
canal), `citas` y `escalamientos`. Como el canal es una columna, el panel
muestra Facebook e Instagram automaticamente en cuanto el adaptador de
Chatwoot empiece a escribir con esos canales: no hay nada que cambiar aqui.

"clientes" cuenta pares distintos (canal, user_id) con actividad en el
periodo, no conversaciones separadas por tiempo: es la cifra que importa para
la conversion (cuantas personas distintas atendio el bot).
"""

import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select, text

from ..config import get_settings
from ..db.models import Cita, Escalamiento, Mensaje, Slot, Sucursal
from ..db.session import get_session
from . import consumo as consumo_modelo

MAX_SUCURSALES_TOP = 10


def _desde(dias: int) -> datetime.datetime:
    ahora = datetime.datetime.now(ZoneInfo(get_settings().tz))
    return ahora - datetime.timedelta(days=dias)


async def _resumen(session, desde) -> dict:
    fila = (
        await session.execute(
            select(
                func.count(Mensaje.id),
                func.count(distinct(func.concat(Mensaje.canal, ":", Mensaje.user_id))),
                func.count(Mensaje.id).filter(Mensaje.direccion == "in"),
                func.count(Mensaje.id).filter(Mensaje.direccion == "out"),
                func.count(Mensaje.id).filter(Mensaje.tipo == "audio"),
                func.count(Mensaje.id).filter(Mensaje.tipo == "seleccion_interactiva"),
                func.count(Mensaje.id).filter(Mensaje.tipo == "cache"),
            ).where(Mensaje.creado_en >= desde)
        )
    ).one()
    return {
        "mensajes": fila[0],
        "clientes": fila[1],
        "entrantes": fila[2],
        "salientes": fila[3],
        "notas_de_voz": fila[4],
        "toques_de_boton": fila[5],
        # Preguntas repetidas contestadas sin llamar al modelo
        "respuestas_de_cache": fila[6],
    }


async def _citas(session, desde) -> dict:
    fila = (
        await session.execute(
            select(
                func.count(Cita.id),
                func.count(Cita.id).filter(Cita.estado == "confirmada"),
                func.count(Cita.id).filter(Cita.estado == "cancelada"),
                func.count(Cita.id).filter(Cita.google_event_id.is_not(None)),
            ).where(Cita.creada_en >= desde)
        )
    ).one()
    return {
        "total": fila[0],
        "confirmadas": fila[1],
        "canceladas": fila[2],
        "en_calendario": fila[3],
    }


async def _escalamientos(session, desde) -> dict:
    fila = (
        await session.execute(
            select(
                func.count(Escalamiento.id),
                func.count(Escalamiento.id).filter(Escalamiento.estado == "pendiente"),
            ).where(Escalamiento.creado_en >= desde)
        )
    ).one()
    return {"total": fila[0], "pendientes": fila[1]}


async def _por_canal(session, desde) -> list[dict]:
    filas = (
        await session.execute(
            select(
                Mensaje.canal,
                func.count(Mensaje.id),
                func.count(distinct(Mensaje.user_id)),
            )
            .where(Mensaje.creado_en >= desde)
            .group_by(Mensaje.canal)
            .order_by(func.count(Mensaje.id).desc())
        )
    ).all()
    return [
        {"canal": canal, "mensajes": mensajes, "clientes": clientes}
        for canal, mensajes, clientes in filas
    ]


async def _serie_diaria(session, desde) -> list[dict]:
    dia = func.date(Mensaje.creado_en).label("dia")
    filas = (
        await session.execute(
            select(dia, func.count(Mensaje.id), func.count(distinct(Mensaje.user_id)))
            .where(Mensaje.creado_en >= desde)
            .group_by(dia)
            .order_by(dia)
        )
    ).all()
    return [
        {"dia": str(d), "mensajes": m, "clientes": c} for d, m, c in filas
    ]


async def _top_sucursales(session, desde) -> list[dict]:
    filas = (
        await session.execute(
            select(Sucursal.nombre, func.count(Cita.id))
            .join(Cita, Cita.sucursal_id == Sucursal.id)
            .where(Cita.creada_en >= desde)
            .group_by(Sucursal.nombre)
            .order_by(func.count(Cita.id).desc())
            .limit(MAX_SUCURSALES_TOP)
        )
    ).all()
    return [{"sucursal": n, "citas": c} for n, c in filas]


async def _proximas_citas(session) -> list[dict]:
    hoy = datetime.datetime.now(ZoneInfo(get_settings().tz)).date()
    filas = (
        await session.execute(
            select(Cita, Slot, Sucursal)
            .join(Slot, Cita.slot_id == Slot.id)
            .join(Sucursal, Cita.sucursal_id == Sucursal.id)
            .where(Cita.estado == "confirmada", Slot.fecha >= hoy)
            .order_by(Slot.fecha, Slot.hora_inicio)
            .limit(20)
        )
    ).all()
    return [
        {
            "folio": cita.id,
            "cliente": cita.cliente_nombre,
            "sucursal": sucursal.nombre,
            "fecha": slot.fecha.isoformat(),
            "hora": slot.hora_inicio.strftime("%H:%M"),
            "canal": cita.canal,
        }
        for cita, slot, sucursal in filas
    ]


async def _primera_respuesta_seg(session, desde) -> float | None:
    """Segundos promedio entre el mensaje del cliente y la respuesta del bot."""
    consulta = text(
        """
        with pares as (
          select m.creado_en as entrada,
                 min(r.creado_en) as respuesta
          from mensajes m
          join mensajes r
            on r.canal = m.canal and r.user_id = m.user_id
           and r.direccion = 'out' and r.creado_en > m.creado_en
          where m.direccion = 'in' and m.creado_en >= :desde
          group by m.id, m.creado_en
        )
        select avg(extract(epoch from (respuesta - entrada))) from pares
        """
    )
    valor = (await session.execute(consulta, {"desde": desde})).scalar()
    return round(float(valor), 1) if valor is not None else None


async def calcular(dias: int = 30) -> dict:
    """Todas las metricas del panel para los ultimos `dias` dias."""
    desde = _desde(dias)
    async with get_session() as session:
        resumen = await _resumen(session, desde)
        citas = await _citas(session, desde)
        datos = {
            "dias": dias,
            "desde": desde.date().isoformat(),
            "resumen": resumen,
            "citas": citas,
            "escalamientos": await _escalamientos(session, desde),
            "por_canal": await _por_canal(session, desde),
            "serie_diaria": await _serie_diaria(session, desde),
            "top_sucursales": await _top_sucursales(session, desde),
            "proximas_citas": await _proximas_citas(session),
            "primera_respuesta_seg": await _primera_respuesta_seg(session, desde),
        }
    # Gasto real en el modelo durante el mismo periodo, por si la factura de
    # Anthropic no cuadra con lo que se esperaba
    datos["consumo"] = await consumo_modelo.resumen_periodo(dias)
    clientes = resumen["clientes"]
    datos["conversion"] = (
        round(100 * citas["total"] / clientes, 1) if clientes else 0.0
    )
    return datos
