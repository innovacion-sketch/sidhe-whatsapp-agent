"""Bandeja de conversaciones del panel.

Se arma sobre la tabla `mensajes` (auditoría de todo lo que entra y sale) y
sobre `escalamientos`, que además funciona como interruptor del bot: mientras
un cliente tenga un escalamiento pendiente, el bot guarda silencio y la
conversación la lleva una persona. Eso vale igual si el escalamiento lo pidió
el agente o si un humano tomó el control desde el panel.
"""

import datetime
from zoneinfo import ZoneInfo

import unicodedata

from sqlalchemy import func, or_, select, tuple_, update

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
SIN_RESPUESTA = "sin_respuesta"        # rojo: escribió y NADIE contestó, ni el bot
ATENDIDA = "atendida"                  # verde: el asesor contestó y fue lo último
CERRADA = "cerrada"                    # verde: se dio por resuelta
CON_BOT = "bot"                        # sin color: el bot la lleva
ESTADOS = (ESPERANDO_ASESOR, SIN_RESPUESTA, ATENDIDA, CERRADA, CON_BOT)

# A partir de cuántos minutos sin ninguna respuesta se considera abandonada.
# El bot contesta en segundos: si pasó esto y no salió nada, algo se atoró.
# Una clienta mandó sus datos de cita y estuvo dos horas y media esperando
# mientras el panel la pintaba de gris, "con el bot", porque su escalamiento
# ya figuraba como atendido. Sin este estado, ese caso no se ve.
MINUTOS_SIN_RESPUESTA = 30

# Se calcula el estado sobre las más recientes y luego se filtra; así un
# cliente esperando no desaparece de "Esperando asesor" solo porque entraron
# 50 conversaciones nuevas del bot después de la suya.
CANDIDATAS = 300

# Cuánto texto se muestra alrededor de la palabra encontrada
ANCHO_RECORTE = 140


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


async def inicio_de_la_pausa(canal: str, user_id: str) -> datetime.datetime | None:
    """Cuándo se escaló esta conversación por última vez.

    Sirve de corte para saber qué se habló mientras el bot estaba callado.
    Mira el escalamiento más reciente sin importar su estado, porque se
    consulta justo después de darlo por atendido.
    """
    async with get_session() as session:
        return (
            await session.execute(
                select(func.max(Escalamiento.creado_en)).where(
                    Escalamiento.canal == canal, Escalamiento.user_id == user_id
                )
            )
        ).scalar()


# Cuántos mensajes de la pausa se le devuelven al bot. Una conversación que
# un asesor llevó media hora no cabe entera y tampoco hace falta: lo que
# importa es el final, que es donde quedaron las cosas.
MAX_MENSAJES_DE_PAUSA = 20


async def hablado_durante_la_pausa(
    canal: str, user_id: str, desde: datetime.datetime
) -> list[tuple[str, str]]:
    """(dirección, texto) de lo que se dijo mientras el bot estaba en pausa.

    Esos mensajes nunca pasaron por el grafo —el bot estaba callado—, así
    que no existen en su memoria. Sin ellos, al devolverle la conversación
    vuelve a preguntar lo que el cliente ya contestó.
    """
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Mensaje.direccion, Mensaje.contenido)
                .where(
                    Mensaje.canal == canal,
                    Mensaje.user_id == user_id,
                    Mensaje.creado_en >= desde,
                )
                .order_by(Mensaje.creado_en.desc())
                .limit(MAX_MENSAJES_DE_PAUSA)
            )
        ).all()
    return [
        (direccion, contenido)
        for direccion, contenido in reversed(filas)
        if (contenido or "").strip()
    ]


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


def _lleva_mucho_sin_respuesta(
    ultimo_entrante: datetime.datetime | None,
    ultimo_saliente: datetime.datetime | None,
    ahora: datetime.datetime | None,
) -> bool:
    """El cliente habló y no salió nada en mucho rato.

    Sin `ahora` no se decide: se prefiere no pintar de rojo antes que pintar
    de rojo por una comparación que no se pudo hacer.
    """
    if ultimo_entrante is None or ahora is None:
        return False
    if ultimo_saliente is not None and ultimo_saliente >= ultimo_entrante:
        return False
    if ultimo_entrante.tzinfo is None or ahora.tzinfo is None:
        ultimo_entrante = ultimo_entrante.replace(tzinfo=None)
        ahora = ahora.replace(tzinfo=None)
    return ahora - ultimo_entrante >= datetime.timedelta(
        minutes=MINUTOS_SIN_RESPUESTA
    )


def calcular_estado(
    *,
    escalado_desde: datetime.datetime | None,
    ultimo_humano: datetime.datetime | None,
    ultimo_entrante: datetime.datetime | None,
    cerrada_en: datetime.datetime | None,
    ultimo_saliente: datetime.datetime | None = None,
    ahora: datetime.datetime | None = None,
) -> str:
    """Rojo significa "alguien te está esperando ahorita".

    - Cerrada: el último cierre es posterior al último mensaje del cliente.
      Si el cliente vuelve a escribir, se reabre sola.
    - Esperando asesor: el bot escaló y ningún humano ha contestado desde
      entonces, o el cliente volvió a escribir después del asesor.
    - Atendida: hay escalamiento y la última palabra la tiene el asesor.
    - Sin responder: el cliente escribió y no salió NADA, ni del bot ni de
      nadie. No importa por qué se atoró; se ve igual.
    - Con el bot: no hay escalamiento pendiente y el bot va contestando.
    """
    if cerrada_en is not None and (
        ultimo_entrante is None or cerrada_en >= ultimo_entrante
    ):
        return CERRADA
    if escalado_desde is None:
        if _lleva_mucho_sin_respuesta(ultimo_entrante, ultimo_saliente, ahora):
            return SIN_RESPUESTA
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
    # Cualquier salida, del bot o de una persona: si no hay ninguna después
    # del último mensaje del cliente, nadie le contestó.
    salientes = await por_conversacion(
        select(*clave, func.max(Mensaje.creado_en))
        .where(de_estas, Mensaje.direccion == "out")
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
    ahora = _ahora()
    for par in pares:
        estado = calcular_estado(
            escalado_desde=escalados.get(par),
            ultimo_humano=humanos.get(par),
            ultimo_entrante=entrantes.get(par),
            cerrada_en=cierres.get(par),
            ultimo_saliente=salientes.get(par),
            ahora=ahora,
        )
        if estado == ESPERANDO_ASESOR:
            desde = esperando_desde(
                escalados.get(par), humanos.get(par), entrantes.get(par)
            )
        elif estado == SIN_RESPUESTA:
            # Espera desde que escribio y nadie le contesto
            desde = entrantes.get(par)
        else:
            desde = None
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


def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def recorte(texto: str, termino: str, ancho: int = ANCHO_RECORTE) -> str:
    """Un pedazo del mensaje alrededor de la palabra buscada.

    Sin esto, una coincidencia a la mitad de un mensaje largo no se vería:
    el asesor tendría que abrir la conversación para saber por qué salió.
    """
    texto = texto or ""
    posicion = _sin_acentos(texto).find(_sin_acentos(termino.strip()))
    if posicion < 0:
        return texto[:ancho]
    inicio = max(0, posicion - ancho // 3)
    fin = min(len(texto), posicion + len(termino) + (2 * ancho) // 3)
    return ("…" if inicio else "") + texto[inicio:fin] + ("…" if fin < len(texto) else "")


async def listar(buscar: str = "", limite: int = 50, estado: str = "") -> dict:
    """Conversaciones por actividad reciente, con su estado y conteos.

    `buscar` mira el teléfono Y el texto de todos los mensajes, sin importar
    acentos ni mayúsculas; de cada conversación que coincide se devuelve el
    fragmento donde coincidió, en `coincidencia`.

    `estado` vacío o desconocido = sin filtro.
    """
    limite = max(1, min(limite, MAX_CONVERSACIONES))
    termino = buscar.strip()
    ultimo = func.max(Mensaje.creado_en).label("ultimo")
    consulta = (
        select(Mensaje.canal, Mensaje.user_id, ultimo, func.count(Mensaje.id))
        .group_by(Mensaje.canal, Mensaje.user_id)
        .order_by(ultimo.desc())
        .limit(CANDIDATAS)
    )
    coincide_texto = None
    if termino:
        patron = func.unaccent(f"%{termino.lower()}%")
        coincide_texto = func.unaccent(func.lower(Mensaje.contenido)).like(patron)
        # bool_or: basta con que UN mensaje de la conversación coincida, pero
        # los totales y el último mensaje siguen siendo los de siempre.
        consulta = consulta.having(
            func.bool_or(
                or_(
                    func.unaccent(func.lower(Mensaje.user_id)).like(patron),
                    coincide_texto,
                )
            )
        )

    async with get_session() as session:
        filas = (await session.execute(consulta)).all()
        pares = [(canal, user_id) for canal, user_id, _, _ in filas]
        estados = await _estados(session, pares)

        coincidencias: dict[tuple[str, str], str] = {}
        if termino and pares:
            # El mensaje coincidente más reciente de cada conversación
            for canal, user_id, contenido in (
                await session.execute(
                    select(Mensaje.canal, Mensaje.user_id, Mensaje.contenido)
                    .where(tuple_(Mensaje.canal, Mensaje.user_id).in_(pares))
                    .where(coincide_texto)
                    .distinct(Mensaje.canal, Mensaje.user_id)
                    .order_by(
                        Mensaje.canal, Mensaje.user_id, Mensaje.creado_en.desc()
                    )
                )
            ).all():
                coincidencias[(canal, user_id)] = recorte(contenido, termino)

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
                # Vacío si la coincidencia fue por teléfono, no por texto
                "coincidencia": coincidencias.get(par, ""),
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
