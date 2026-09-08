"""Bandeja de conversaciones del panel.

Se arma sobre la tabla `mensajes` (auditoría de todo lo que entra y sale) y
sobre `escalamientos`, que además funciona como interruptor del bot: mientras
un cliente tenga un escalamiento pendiente, el bot guarda silencio y la
conversación la lleva una persona. Eso vale igual si el escalamiento lo pidió
el agente o si un humano tomó el control desde el panel.
"""

import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..config import get_settings
from ..db.models import Escalamiento, Mensaje
from ..db.session import get_session

MAX_CONVERSACIONES = 100
MAX_MENSAJES = 200
MOTIVO_PANEL = "atendida desde el panel"

# WhatsApp solo permite mensajes libres dentro de las 24h posteriores al
# último mensaje del cliente; fuera de eso hace falta una plantilla aprobada.
VENTANA_LIBRE_HORAS = 24


def _ahora() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(get_settings().tz))


async def _pendientes(session) -> set[tuple[str, str]]:
    filas = (
        await session.execute(
            select(Escalamiento.canal, Escalamiento.user_id).where(
                Escalamiento.estado == "pendiente"
            )
        )
    ).all()
    return {(canal, user_id) for canal, user_id in filas}


async def bot_pausado(canal: str, user_id: str) -> bool:
    """True si esta conversación la está atendiendo una persona."""
    async with get_session() as session:
        existe = (
            await session.execute(
                select(Escalamiento.id).where(
                    Escalamiento.canal == canal,
                    Escalamiento.user_id == user_id,
                    Escalamiento.estado == "pendiente",
                )
            )
        ).first()
        return existe is not None


async def tomar_control(canal: str, user_id: str, motivo: str = MOTIVO_PANEL) -> bool:
    """Silencia al bot en esta conversación. Devuelve True si la pausó ahora."""
    async with get_session() as session:
        if (canal, user_id) in await _pendientes(session):
            return False
        session.add(
            Escalamiento(
                canal=canal,
                user_id=user_id,
                motivo=motivo,
                contexto_resumen="",
                estado="pendiente",
            )
        )
        await session.commit()
        return True


async def listar(buscar: str = "", limite: int = 50) -> list[dict]:
    """Conversaciones ordenadas por actividad reciente."""
    limite = max(1, min(limite, MAX_CONVERSACIONES))
    ultimo = func.max(Mensaje.creado_en).label("ultimo")
    consulta = (
        select(
            Mensaje.canal,
            Mensaje.user_id,
            ultimo,
            func.count(Mensaje.id).label("total"),
        )
        .group_by(Mensaje.canal, Mensaje.user_id)
        .order_by(ultimo.desc())
        .limit(limite)
    )
    if buscar.strip():
        patron = f"%{buscar.strip().lower()}%"
        consulta = consulta.having(
            func.lower(func.min(Mensaje.user_id)).like(patron)
        )

    async with get_session() as session:
        filas = (await session.execute(consulta)).all()
        pausadas = await _pendientes(session)

        conversaciones = []
        for canal, user_id, ultima_fecha, total in filas:
            ultimo_texto = (
                await session.execute(
                    select(Mensaje.contenido, Mensaje.direccion)
                    .where(Mensaje.canal == canal, Mensaje.user_id == user_id)
                    .order_by(Mensaje.creado_en.desc())
                    .limit(1)
                )
            ).first()
            conversaciones.append(
                {
                    "canal": canal,
                    "user_id": user_id,
                    "ultimo_mensaje": (ultimo_texto[0] if ultimo_texto else "")[:120],
                    "ultima_direccion": ultimo_texto[1] if ultimo_texto else "",
                    "ultima_fecha": ultima_fecha.isoformat() if ultima_fecha else None,
                    "mensajes": total,
                    "bot_pausado": (canal, user_id) in pausadas,
                }
            )
        return conversaciones


async def historial(canal: str, user_id: str, limite: int = MAX_MENSAJES) -> dict:
    """Mensajes de una conversación, del más antiguo al más reciente."""
    limite = max(1, min(limite, MAX_MENSAJES))
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Mensaje)
                .where(Mensaje.canal == canal, Mensaje.user_id == user_id)
                .order_by(Mensaje.creado_en.desc())
                .limit(limite)
            )
        ).scalars().all()
        pausada = (canal, user_id) in await _pendientes(session)

    mensajes = [
        {
            "direccion": m.direccion,
            "tipo": m.tipo,
            "contenido": m.contenido,
            "fecha": m.creado_en.isoformat(),
        }
        for m in reversed(filas)
    ]
    ultimo_entrante = next(
        (m for m in reversed(mensajes) if m["direccion"] == "in"), None
    )
    return {
        "canal": canal,
        "user_id": user_id,
        "bot_pausado": pausada,
        "ventana_abierta": _ventana_abierta(ultimo_entrante),
        "mensajes": mensajes,
    }


def _ventana_abierta(ultimo_entrante: dict | None) -> bool:
    """¿Se puede mandar texto libre, o ya venció la ventana de 24h?"""
    if not ultimo_entrante:
        return False
    fecha = datetime.datetime.fromisoformat(ultimo_entrante["fecha"])
    return (_ahora() - fecha) < datetime.timedelta(hours=VENTANA_LIBRE_HORAS)
