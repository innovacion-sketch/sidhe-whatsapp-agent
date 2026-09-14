"""Bandeja de conversaciones del panel.

Se arma sobre la tabla `mensajes` (auditoría de todo lo que entra y sale) y
sobre `escalamientos`, que además funciona como interruptor del bot: mientras
un cliente tenga un escalamiento pendiente, el bot guarda silencio y la
conversación la lleva una persona. Eso vale igual si el escalamiento lo pidió
el agente o si un humano tomó el control desde el panel.
"""

import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, tuple_, update

from ..config import get_settings
from ..db.models import CierreConversacion, Escalamiento, Mensaje
from ..db.session import get_session

MAX_CONVERSACIONES = 100
MAX_MENSAJES = 200
MOTIVO_PANEL = "atendida desde el panel"

# WhatsApp solo permite mensajes libres dentro de las 24h posteriores al
# último mensaje del cliente; fuera de eso hace falta una plantilla aprobada.
VENTANA_LIBRE_HORAS = 24

# Estado de atención de cada conversación, tal como lo ve el asesor
ESPERANDO_ASESOR = "esperando_asesor"  # rojo: alguien espera respuesta humana
ATENDIDA = "atendida"                  # verde: el asesor contestó y fue lo último
CERRADA = "cerrada"                    # verde: se dio por resuelta
CON_BOT = "bot"                        # sin color: el bot la lleva
ESTADOS = (ESPERANDO_ASESOR, ATENDIDA, CERRADA, CON_BOT)

# Se calcula el estado sobre las más recientes y luego se filtra; así un
# cliente esperando no desaparece de "Esperando asesor" solo porque entraron
# 50 conversaciones nuevas del bot después de la suya.
CANDIDATAS = 300


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


def calcular_estado(
    *,
    escalado_desde: datetime.datetime | None,
    ultimo_humano: datetime.datetime | None,
    ultimo_entrante: datetime.datetime | None,
    cerrada_en: datetime.datetime | None,
) -> str:
    """Rojo significa "alguien te está esperando ahorita".

    - Cerrada: el último cierre es posterior al último mensaje del cliente.
      Si el cliente vuelve a escribir, se reabre sola.
    - Esperando asesor: el bot escaló y ningún humano ha contestado desde
      entonces, o el cliente volvió a escribir después del asesor.
    - Atendida: hay escalamiento y la última palabra la tiene el asesor.
    - Con el bot: no hay escalamiento pendiente.
    """
    if cerrada_en is not None and (
        ultimo_entrante is None or cerrada_en >= ultimo_entrante
    ):
        return CERRADA
    if escalado_desde is None:
        return CON_BOT
    humano_al_dia = (
        ultimo_humano is not None
        and ultimo_humano >= escalado_desde
        and (ultimo_entrante is None or ultimo_humano >= ultimo_entrante)
    )
    return ATENDIDA if humano_al_dia else ESPERANDO_ASESOR


def esperando_desde(
    escalado_desde: datetime.datetime | None,
    ultimo_humano: datetime.datetime | None,
    ultimo_entrante: datetime.datetime | None,
) -> datetime.datetime | None:
    """Desde cuándo espera el cliente, para mostrar "esperando 25 min"."""
    if escalado_desde is None:
        return None
    if ultimo_humano is None or ultimo_humano < escalado_desde:
        return escalado_desde
    return ultimo_entrante


async def _estados(session, pares: list[tuple[str, str]]) -> dict:
    """(canal, user_id) → (estado, esperando_desde), en cuatro consultas."""
    if not pares:
        return {}
    clave = (Mensaje.canal, Mensaje.user_id)
    de_estas = tuple_(*clave).in_(pares)

    async def por_conversacion(consulta) -> dict:
        return {(c, u): v for c, u, v in (await session.execute(consulta)).all()}

    escalados = await por_conversacion(
        select(Escalamiento.canal, Escalamiento.user_id, func.min(Escalamiento.creado_en))
        .where(
            Escalamiento.estado == "pendiente",
            tuple_(Escalamiento.canal, Escalamiento.user_id).in_(pares),
        )
        .group_by(Escalamiento.canal, Escalamiento.user_id)
    )
    humanos = await por_conversacion(
        select(*clave, func.max(Mensaje.creado_en))
        .where(de_estas, Mensaje.direccion == "out", Mensaje.tipo == "humano")
        .group_by(*clave)
    )
    entrantes = await por_conversacion(
        select(*clave, func.max(Mensaje.creado_en))
        .where(de_estas, Mensaje.direccion == "in")
        .group_by(*clave)
    )
    cierres = await por_conversacion(
        select(
            CierreConversacion.canal,
            CierreConversacion.user_id,
            func.max(CierreConversacion.cerrada_en),
        )
        .where(tuple_(CierreConversacion.canal, CierreConversacion.user_id).in_(pares))
        .group_by(CierreConversacion.canal, CierreConversacion.user_id)
    )

    resultado = {}
    for par in pares:
        estado = calcular_estado(
            escalado_desde=escalados.get(par),
            ultimo_humano=humanos.get(par),
            ultimo_entrante=entrantes.get(par),
            cerrada_en=cierres.get(par),
        )
        desde = (
            esperando_desde(escalados.get(par), humanos.get(par), entrantes.get(par))
            if estado == ESPERANDO_ASESOR
            else None
        )
        resultado[par] = (estado, desde)
    return resultado


def filtrar(todas: list[dict], estado: str, limite: int) -> dict:
    """Aplica el filtro de estado y arma los conteos de las pestañas.

    Los conteos se sacan ANTES de filtrar: la pestaña "Esperando asesor (3)"
    tiene que decir 3 aunque estés viendo otra.
    """
    conteos = {e: 0 for e in ESTADOS}
    for conversacion in todas:
        conteos[conversacion["estado"]] += 1
    conteos["todas"] = len(todas)
    filtro = estado if estado in ESTADOS else ""
    visibles = [c for c in todas if not filtro or c["estado"] == filtro]
    return {"conversaciones": visibles[:limite], "conteos": conteos}


async def listar(buscar: str = "", limite: int = 50, estado: str = "") -> dict:
    """Conversaciones por actividad reciente, con su estado y conteos.

    `estado` vacío o desconocido = sin filtro.
    """
    limite = max(1, min(limite, MAX_CONVERSACIONES))
    ultimo = func.max(Mensaje.creado_en).label("ultimo")
    consulta = (
        select(Mensaje.canal, Mensaje.user_id, ultimo, func.count(Mensaje.id))
        .group_by(Mensaje.canal, Mensaje.user_id)
        .order_by(ultimo.desc())
        .limit(CANDIDATAS)
    )
    if buscar.strip():
        patron = f"%{buscar.strip().lower()}%"
        consulta = consulta.having(func.lower(func.min(Mensaje.user_id)).like(patron))

    async with get_session() as session:
        filas = (await session.execute(consulta)).all()
        pares = [(canal, user_id) for canal, user_id, _, _ in filas]
        estados = await _estados(session, pares)

        ultimos = {}
        if pares:
            # El último mensaje de cada conversación, en una sola consulta
            consulta_ultimos = (
                select(
                    Mensaje.canal, Mensaje.user_id, Mensaje.contenido, Mensaje.direccion
                )
                .where(tuple_(Mensaje.canal, Mensaje.user_id).in_(pares))
                .distinct(Mensaje.canal, Mensaje.user_id)
                .order_by(Mensaje.canal, Mensaje.user_id, Mensaje.creado_en.desc())
            )
            for canal, user_id, contenido, direccion in (
                await session.execute(consulta_ultimos)
            ).all():
                ultimos[(canal, user_id)] = (contenido, direccion)

    todas = []
    for canal, user_id, ultima_fecha, total in filas:
        par = (canal, user_id)
        estado_conv, desde = estados.get(par, (CON_BOT, None))
        contenido, direccion = ultimos.get(par, ("", ""))
        todas.append(
            {
                "canal": canal,
                "user_id": user_id,
                "ultimo_mensaje": (contenido or "")[:120],
                "ultima_direccion": direccion,
                "ultima_fecha": ultima_fecha.isoformat() if ultima_fecha else None,
                "mensajes": total,
                "estado": estado_conv,
                "esperando_desde": desde.isoformat() if desde else None,
                "bot_pausado": estado_conv in (ESPERANDO_ASESOR, ATENDIDA),
            }
        )
    return filtrar(todas, estado, limite)


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
        estado_conv, desde = (await _estados(session, [(canal, user_id)])).get(
            (canal, user_id), (CON_BOT, None)
        )

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
        "estado": estado_conv,
        "esperando_desde": desde.isoformat() if desde else None,
        "bot_pausado": estado_conv in (ESPERANDO_ASESOR, ATENDIDA),
        "ventana_abierta": _ventana_abierta(ultimo_entrante),
        "mensajes": mensajes,
    }


async def cerrar(canal: str, user_id: str) -> int:
    """Da la conversación por resuelta.

    Atiende los escalamientos pendientes (el bot vuelve a quedar a cargo) y
    registra el cierre. Devuelve cuántos escalamientos resolvió. Avisarle al
    agente le toca a main.py, que es quien tiene el grafo.
    """
    async with get_session() as session:
        resultado = await session.execute(
            update(Escalamiento)
            .where(
                Escalamiento.canal == canal,
                Escalamiento.user_id == user_id,
                Escalamiento.estado == "pendiente",
            )
            .values(estado="atendido")
        )
        session.add(CierreConversacion(canal=canal, user_id=user_id))
        await session.commit()
        return resultado.rowcount or 0


def _ventana_abierta(ultimo_entrante: dict | None) -> bool:
    """¿Se puede mandar texto libre, o ya venció la ventana de 24h?"""
    if not ultimo_entrante:
        return False
    fecha = datetime.datetime.fromisoformat(ultimo_entrante["fecha"])
    return (_ahora() - fecha) < datetime.timedelta(hours=VENTANA_LIBRE_HORAS)


ACTIVO = "activo"
PAUSADO = "pausado"
REACTIVADO = "reactivado"


async def revisar_pausa(canal: str, user_id: str, horas_limite: int) -> str:
    """Estado de atención de la conversación, reactivando si se abandonó.

    Un escalamiento sin respuesta deja al cliente escribiendo al vacío (pasó:
    cinco días de mensajes sin que nadie contestara). Si nadie lo atendió en
    `horas_limite`, se cierra y el bot vuelve a hacerse cargo.
    """
    async with get_session() as session:
        fila = (
            await session.execute(
                select(func.min(Escalamiento.creado_en)).where(
                    Escalamiento.canal == canal,
                    Escalamiento.user_id == user_id,
                    Escalamiento.estado == "pendiente",
                )
            )
        ).scalar()
        if fila is None:
            return ACTIVO

        antiguedad = _ahora() - fila
        if antiguedad < datetime.timedelta(hours=horas_limite):
            return PAUSADO

        await session.execute(
            update(Escalamiento)
            .where(
                Escalamiento.canal == canal,
                Escalamiento.user_id == user_id,
                Escalamiento.estado == "pendiente",
            )
            .values(estado="atendido")
        )
        await session.commit()
        return REACTIVADO


async def pendientes_detallados() -> list[dict]:
    """Escalamientos sin atender, para avisar al equipo desde n8n."""
    ahora = _ahora()
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Escalamiento)
                .where(Escalamiento.estado == "pendiente")
                .order_by(Escalamiento.creado_en)
            )
        ).scalars().all()
    return [
        {
            "id": e.id,
            "canal": e.canal,
            "user_id": e.user_id,
            "motivo": e.motivo,
            "creado_en": e.creado_en.isoformat(),
            "horas_esperando": round(
                (ahora - e.creado_en).total_seconds() / 3600, 1
            ),
        }
        for e in filas
    ]
