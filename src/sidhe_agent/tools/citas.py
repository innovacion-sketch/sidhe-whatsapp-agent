"""Tools de citas: disponibilidad, agendado transaccional, consulta y cancelación.

Concurrencia (crítico): agendar_cita toma el slot con SELECT ... FOR UPDATE
dentro de una transacción, valida reservados < capacidad e incrementa e
inserta atómicamente. Si el slot se llenó entre la consulta y la confirmación
devuelve {"error": "slot_no_disponible", "alternativas": [...]} con los 3
slots más cercanos — nunca revienta ni promete la cita.

El teléfono del cliente SIEMPRE sale del estado del grafo (InjectedState);
el modelo no puede inventarlo ni tocar citas de otro teléfono.
"""

import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db.models import Cita, Slot, Sucursal
from ..db.session import get_session
from ..services.google_calendar import borrar_evento, crear_evento
from ..services.n8n import avisar_cita

VENTANA_MAX_DIAS = 14
MAX_RESULTADOS = 10
MAX_ALTERNATIVAS = 3

DIAS_ABREV = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES_ABREV = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


def _ahora() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(get_settings().tz))


def fecha_legible(fecha: datetime.date) -> str:
    """Formato corto para botones: 'Lun 20 jul'."""
    return f"{DIAS_ABREV[fecha.weekday()]} {fecha.day} {MESES_ABREV[fecha.month - 1]}"


def _slot_a_dict(slot: Slot) -> dict:
    return {
        "slot_id": slot.id,
        "fecha": slot.fecha.isoformat(),
        "fecha_legible": fecha_legible(slot.fecha),
        "hora_inicio": slot.hora_inicio.strftime("%H:%M"),
        "hora_fin": slot.hora_fin.strftime("%H:%M"),
        "cupo_disponible": slot.capacidad - slot.reservados,
    }


def _condicion_futuro(ahora: datetime.datetime):
    hoy = ahora.date()
    return (Slot.fecha > hoy) | ((Slot.fecha == hoy) & (Slot.hora_inicio > ahora.time()))


async def _consultar_disponibilidad(
    sucursal_id: int, fecha_inicio: str, fecha_fin: str
) -> dict:
    """Rango de varios dias -> FECHAS con cupo; un solo dia -> HORARIOS.

    Devolver horarios de todo el rango no sirve al flujo de agendado: el
    limite de resultados se consumia con el primer dia y el agente nunca veia
    las demas fechas (y las reconsultaba en bucle).
    """
    try:
        inicio = datetime.date.fromisoformat(fecha_inicio)
        fin = datetime.date.fromisoformat(fecha_fin)
    except ValueError:
        return {
            "error": "fechas_invalidas",
            "formato_esperado": "YYYY-MM-DD",
            "fecha_de_hoy": _ahora().date().isoformat(),
        }

    ahora = _ahora()
    if fin < ahora.date():
        # Rango ya pasado (el modelo suele errar el anio): devolver la fecha
        # real para que se corrija en el siguiente intento, no un error mudo.
        return {
            "error": "rango_en_el_pasado",
            "fecha_de_hoy": ahora.date().isoformat(),
            "sugerencia": "vuelve a llamar con fechas desde hoy en adelante",
        }
    inicio = max(inicio, ahora.date())
    fin = min(fin, inicio + datetime.timedelta(days=VENTANA_MAX_DIAS - 1))
    if fin < inicio:
        return {
            "error": "rango_invalido",
            "fecha_de_hoy": ahora.date().isoformat(),
        }

    condiciones = (
        Slot.sucursal_id == sucursal_id,
        Slot.fecha >= inicio,
        Slot.fecha <= fin,
        Slot.reservados < Slot.capacidad,
        _condicion_futuro(ahora),
    )

    async with get_session() as session:
        if inicio == fin:
            resultado = await session.execute(
                select(Slot)
                .where(*condiciones)
                .order_by(Slot.hora_inicio)
                .limit(MAX_RESULTADOS)
            )
            horarios = [_slot_a_dict(s) for s in resultado.scalars()]
            return {
                "tipo": "horarios",
                "fecha": inicio.isoformat(),
                "fecha_legible": fecha_legible(inicio),
                "horarios": horarios,
            }

        resultado = await session.execute(
            select(Slot.fecha, func.count(Slot.id))
            .where(*condiciones)
            .group_by(Slot.fecha)
            .order_by(Slot.fecha)
            .limit(MAX_RESULTADOS)
        )
        fechas = [
            {
                "fecha": fecha.isoformat(),
                "fecha_legible": fecha_legible(fecha),
                "horarios_disponibles": conteo,
            }
            for fecha, conteo in resultado.all()
        ]
        return {"tipo": "fechas", "fechas": fechas}


async def _alternativas(session, slot: Slot) -> list[dict]:
    """Los 3 slots con cupo más cercanos en la misma sucursal."""
    ahora = _ahora()
    resultado = await session.execute(
        select(Slot)
        .where(
            Slot.sucursal_id == slot.sucursal_id,
            Slot.id != slot.id,
            Slot.reservados < Slot.capacidad,
            _condicion_futuro(ahora),
        )
        .order_by(Slot.fecha, Slot.hora_inicio)
        .limit(MAX_ALTERNATIVAS)
    )
    return [_slot_a_dict(s) for s in resultado.scalars()]


async def _agendar_cita(
    slot_id: int, nombre_cliente: str, telefono: str, canal: str
) -> dict:
    if not telefono:
        return {"error": "telefono_no_disponible_en_sesion"}
    async with get_session() as session:
        try:
            resultado = await session.execute(
                select(Slot).where(Slot.id == slot_id).with_for_update()
            )
            slot = resultado.scalar_one_or_none()
            if slot is None:
                return {"error": "slot_inexistente"}
            if slot.reservados >= slot.capacidad:
                return {
                    "error": "slot_no_disponible",
                    "alternativas": await _alternativas(session, slot),
                }
            slot.reservados += 1
            cita = Cita(
                slot_id=slot.id,
                sucursal_id=slot.sucursal_id,
                cliente_telefono=telefono,
                cliente_nombre=nombre_cliente.strip(),
                estado="confirmada",
                canal=canal,
            )
            session.add(cita)
            await session.commit()
        except IntegrityError:
            # UNIQUE (slot_id, cliente_telefono): doble reserva del mismo cliente
            await session.rollback()
            return {"error": "ya_tienes_cita_en_ese_horario"}

        sucursal = await session.get(Sucursal, slot.sucursal_id)
        confirmacion = {
            "folio": cita.id,
            "sucursal": sucursal.nombre if sucursal else "",
            "direccion": sucursal.direccion if sucursal else "",
            "fecha": slot.fecha.isoformat(),
            "fecha_legible": fecha_legible(slot.fecha),
            "hora": slot.hora_inicio.strftime("%H:%M"),
            "nombre_cliente": cita.cliente_nombre,
        }

        # Integraciones externas: la cita ya esta confirmada en la DB, asi que
        # un fallo aqui no debe afectar al cliente (ambas funciones lo tragan).
        event_id = await crear_evento(
            sucursal.calendar_id if sucursal else None,
            nombre_cliente=cita.cliente_nombre,
            telefono=telefono,
            folio=cita.id,
            fecha=slot.fecha,
            hora_inicio=slot.hora_inicio,
            hora_fin=slot.hora_fin,
            direccion=sucursal.direccion if sucursal else "",
        )
        if event_id:
            cita.google_event_id = event_id
            await session.commit()
        await avisar_cita("creada", {**confirmacion, "telefono": telefono})
        return confirmacion


async def _consultar_mis_citas(telefono: str) -> list[dict]:
    ahora = _ahora()
    async with get_session() as session:
        resultado = await session.execute(
            select(Cita, Slot, Sucursal)
            .join(Slot, Cita.slot_id == Slot.id)
            .join(Sucursal, Cita.sucursal_id == Sucursal.id)
            .where(
                Cita.cliente_telefono == telefono,
                Cita.estado == "confirmada",
                Slot.fecha >= ahora.date(),
            )
            .order_by(Slot.fecha, Slot.hora_inicio)
        )
        return [
            {
                "cita_id": cita.id,
                "sucursal": sucursal.nombre,
                "direccion": sucursal.direccion,
                "fecha": slot.fecha.isoformat(),
                "fecha_legible": fecha_legible(slot.fecha),
                "hora": slot.hora_inicio.strftime("%H:%M"),
            }
            for cita, slot, sucursal in resultado.all()
        ]


async def _cancelar_cita(cita_id: int, telefono: str) -> dict:
    async with get_session() as session:
        resultado = await session.execute(
            select(Cita)
            .where(Cita.id == cita_id, Cita.cliente_telefono == telefono)
            .with_for_update()
        )
        cita = resultado.scalar_one_or_none()
        if cita is None:
            return {"error": "cita_no_encontrada"}
        if cita.estado != "confirmada":
            return {"error": "cita_no_cancelable", "estado": cita.estado}

        slot = (
            await session.execute(
                select(Slot).where(Slot.id == cita.slot_id).with_for_update()
            )
        ).scalar_one()
        slot.reservados = max(0, slot.reservados - 1)
        cita.estado = "cancelada"
        await session.commit()

        sucursal = await session.get(Sucursal, cita.sucursal_id)
        await borrar_evento(
            sucursal.calendar_id if sucursal else None, cita.google_event_id
        )
        await avisar_cita(
            "cancelada",
            {
                "folio": cita.id,
                "sucursal": sucursal.nombre if sucursal else "",
                "fecha": slot.fecha.isoformat(),
                "hora": slot.hora_inicio.strftime("%H:%M"),
                "nombre_cliente": cita.cliente_nombre,
                "telefono": telefono,
            },
        )
        return {"ok": True, "folio": cita.id}


# --- Tools expuestas al modelo -------------------------------------------


@tool
async def consultar_disponibilidad(
    sucursal_id: int, fecha_inicio: str, fecha_fin: str
) -> dict:
    """Consulta disponibilidad para estudio de pisada en una sucursal.

    Se usa en DOS pasos distintos segun el rango que pidas:

    1) FECHAS: pide un rango de varios dias (ej. de hoy a 13 dias despues) y
       devuelve {"tipo": "fechas", "fechas": [{"fecha", "fecha_legible",
       "horarios_disponibles"}]} con hasta 10 dias que tienen cupo. Presenta
       esas fechas con presentar_opciones (id "fecha_<YYYY-MM-DD>").

    2) HORARIOS: cuando el cliente elija un dia, vuelve a llamarla con
       fecha_inicio Y fecha_fin IGUALES a esa fecha; devuelve
       {"tipo": "horarios", "horarios": [{"slot_id", "hora_inicio", ...}]}.
       Presenta esos horarios con presentar_opciones (id "slot_<slot_id>").

    Nunca pidas mas de 14 dias. Si una lista viene vacia, informa al cliente
    que no hay cupo en ese rango y ofrece otro periodo.

    Args:
        sucursal_id: id de la sucursal (de buscar_sucursal).
        fecha_inicio: primera fecha, formato YYYY-MM-DD.
        fecha_fin: ultima fecha, formato YYYY-MM-DD (igual a fecha_inicio
            para obtener los horarios de ese dia).
    """
    return await _consultar_disponibilidad(sucursal_id, fecha_inicio, fecha_fin)


@tool
async def agendar_cita(
    slot_id: int, nombre_cliente: str, state: Annotated[dict, InjectedState]
) -> dict:
    """Agenda la cita de estudio de pisada en el slot elegido por el cliente.

    Llámala SOLO después de la confirmación explícita del cliente (botón
    "Confirmar"). El teléfono sale automáticamente de la sesión; nunca lo
    pidas. Si devuelve error slot_no_disponible, ofrece las alternativas
    incluidas sin prometer la cita original. Si la agenda con éxito, confirma
    con folio, sucursal, dirección, fecha y hora, y recomienda llegar 10
    minutos antes.

    Args:
        slot_id: id del slot elegido (viene de consultar_disponibilidad).
        nombre_cliente: nombre del cliente (del perfil o pedido por texto).
    """
    return await _agendar_cita(
        slot_id,
        nombre_cliente,
        state.get("user_id", ""),
        state.get("canal", "whatsapp"),
    )


@tool
async def consultar_mis_citas(state: Annotated[dict, InjectedState]) -> list[dict]:
    """Consulta las citas confirmadas y vigentes del cliente de esta conversación.

    Solo devuelve citas del teléfono de la sesión actual. Devuelve cita_id,
    sucursal, dirección, fecha y hora de cada una.
    """
    return await _consultar_mis_citas(state.get("user_id", ""))


@tool
async def cancelar_cita(cita_id: int, state: Annotated[dict, InjectedState]) -> dict:
    """Cancela una cita del cliente de esta conversación.

    Llámala SOLO tras confirmación explícita del cliente. Solo puede cancelar
    citas de su propio teléfono; con cita_id de otra persona devuelve
    cita_no_encontrada.

    Args:
        cita_id: id de la cita (de consultar_mis_citas).
    """
    return await _cancelar_cita(cita_id, state.get("user_id", ""))
