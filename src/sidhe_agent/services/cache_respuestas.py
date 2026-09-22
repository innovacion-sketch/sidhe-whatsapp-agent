"""Reusar respuestas de catálogo en vez de volver a pagarlas.

"¿A qué hora abren?" la contestan diez personas distintas y la respuesta es
la misma para todas. Esa se guarda y se reusa sin llamar al modelo. "¿Ya
están mis plantillas?" jamás: esa depende de quién pregunta, y contestarla
con la respuesta de otro cliente sería el peor error que puede cometer este
bot.

Tres candados, en orden de importancia:

1. **Solo se guarda lo impersonal.** Si el turno usó una herramienta
   (pedido, agenda, sucursales), si ofreció botones, si escaló o si la
   respuesta menciona el nombre del cliente, no se guarda. Lo que queda es
   lo que el bot contestó de corrido con las FAQs del prompt.
2. **Solo se reusa ante una pregunta casi idéntica.** Similitud por
   embeddings con un umbral alto y, además, nada que hable en primera
   persona de un pedido, una cita o un estudio.
3. **Se invalida sola.** Cada respuesta guarda la huella del system prompt
   con el que nació; si cambia un precio o un horario, la huella cambia y
   lo viejo deja de servirse sin que nadie tenga que acordarse de limpiar.

Ante cualquier duda o error, devuelve None y contesta el modelo: el ahorro
nunca vale una respuesta equivocada.
"""

import datetime
import hashlib
import re
import unicodedata
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..db.models import RespuestaCacheada
from ..db.session import get_session
from . import cortesias
from .embeddings import embed_textos

logger = structlog.get_logger(__name__)

MIN_LARGO = 8
MAX_LARGO = 200

# Primera persona + algo propio: "mis plantillas", "mi cita", "mi pedido".
# Esas nunca se sirven de caché aunque se parezcan a otra pregunta.
PERSONAL = re.compile(
    r"\bm[ií]s?\b[^.?!]{0,30}\b"
    r"(pedid|plantill|cita|orden|folio|estudio|zapat|talla|paquete|envio)",
    re.IGNORECASE,
)
# Tampoco lo que claramente pide un trámite propio
TRAMITE = re.compile(
    r"\b(cancel|reagend|reprogram|agend|modific|factur)\w*\b", re.IGNORECASE
)
# Tiene que parecer una pregunta de verdad. "Si sobre los precios" es la
# respuesta a algo que preguntó el bot: fuera de esa conversación no
# significa nada, y reusar su respuesta con otro cliente es una apuesta.
INTERROGATIVAS = re.compile(
    r"\b(que|cual|cuales|cuanto|cuanta|cuantos|cuantas|cuando|donde|como|quien|"
    r"porque|hacen|tienen|puedo|puedes|pueden|aceptan|manejan|venden|cuesta|"
    r"cuestan|vale|valen|sale|salen|hay|sirve|sirven|incluye|incluyen|dura|"
    r"duran|tarda|tardan|necesito|requiere|requieren|atienden|abren|cierran)\b",
    re.IGNORECASE,
)

_huella_prompt = ""


def fijar_prompt(system_prompt: str) -> str:
    """Guarda la huella del prompt vigente. Se llama una vez al arrancar."""
    global _huella_prompt
    _huella_prompt = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:32]
    return _huella_prompt


def huella_actual() -> str:
    return _huella_prompt


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def clave(texto: str) -> str:
    """La pregunta sin acentos, signos ni dobles espacios.

    Con esto se reconoce la pregunta escrita igual sin llamar a ningún
    proveedor de embeddings: "¿A qué hora abren?" y "a que hora abren"
    caen en la misma clave.
    """
    letras = [c if c.isalnum() else " " for c in _normalizar(texto)]
    return " ".join("".join(letras).split())[:200]


def es_pregunta_generica(texto: str) -> bool:
    """Si esta pregunta PUEDE contestarse con una respuesta de otro cliente."""
    limpio = (texto or "").strip()
    if not (MIN_LARGO <= len(limpio) <= MAX_LARGO):
        return False
    if cortesias.es_acuse(limpio):
        return False
    normalizado = _normalizar(limpio)
    if PERSONAL.search(normalizado) or TRAMITE.search(normalizado):
        return False
    lleva_signo = "?" in limpio or "¿" in limpio
    return lleva_signo or bool(INTERROGATIVAS.search(normalizado))


def _hubo_herramientas(mensajes: list) -> bool:
    """Si en el último turno el bot consultó algo (pedido, agenda, sucursal)."""
    for mensaje in reversed(mensajes or []):
        tipo = type(mensaje).__name__
        if tipo == "ToolMessage":
            return True
        if tipo == "HumanMessage":
            return False
    return False


def apta_para_guardar(
    pregunta: str,
    respuesta: str,
    mensajes: list,
    hay_ui: bool = False,
    escalado: bool = False,
    nombre_cliente: str = "",
) -> bool:
    """Si esta respuesta sirve para cualquiera que pregunte lo mismo."""
    if hay_ui or escalado or not respuesta.strip():
        return False
    if not es_pregunta_generica(pregunta):
        return False
    if _hubo_herramientas(mensajes):
        return False
    # El bot lo tuteó por su nombre: esa respuesta es de esa persona
    nombre = (nombre_cliente or "").strip()
    if nombre and _normalizar(nombre.split()[0]) in _normalizar(respuesta):
        return False
    return True


async def _por_similitud(session, pregunta: str, desde, umbral: float):
    """La entrada más parecida por embeddings, si el proveedor está listo."""
    try:
        vector = (await embed_textos([pregunta], "consulta"))[0]
    except Exception as exc:
        # Sin proveedor de embeddings el caché sigue sirviendo por texto exacto
        logger.info("cache_sin_embeddings", detalle=str(exc)[:120])
        return None
    distancia = RespuestaCacheada.embedding.cosine_distance(vector)
    fila = (
        await session.execute(
            select(RespuestaCacheada, distancia)
            .where(
                RespuestaCacheada.prompt_hash == _huella_prompt,
                RespuestaCacheada.creado_en >= desde,
                RespuestaCacheada.embedding.is_not(None),
            )
            .order_by(distancia)
            .limit(1)
        )
    ).first()
    if fila is None:
        return None
    guardada, dist = fila
    similitud = 1 - float(dist)
    return guardada if similitud >= umbral else None


async def buscar(pregunta: str) -> str | None:
    """La respuesta guardada para una pregunta casi idéntica, o None.

    Primero busca la pregunta escrita igual (una comparación de texto, sin
    costo ni dependencias) y solo después intenta por parecido semántico.
    """
    ajustes = get_settings()
    if not ajustes.cache_respuestas_activo or not es_pregunta_generica(pregunta):
        return None
    try:
        desde = datetime.datetime.now(ZoneInfo(ajustes.tz)) - datetime.timedelta(
            days=ajustes.cache_respuestas_dias
        )
        async with get_session() as session:
            guardada = (
                await session.execute(
                    select(RespuestaCacheada)
                    .where(
                        RespuestaCacheada.prompt_hash == _huella_prompt,
                        RespuestaCacheada.pregunta_normalizada == clave(pregunta),
                        RespuestaCacheada.creado_en >= desde,
                    )
                    .limit(1)
                )
            ).scalars().first()
            como = "texto_identico"
            if guardada is None:
                guardada = await _por_similitud(
                    session, pregunta, desde, ajustes.cache_respuestas_similitud
                )
                como = "parecido"
            if guardada is None:
                return None
            guardada.usos += 1
            await session.commit()
            logger.info("respuesta_servida_de_cache", como=como, usos=guardada.usos)
            return guardada.respuesta
    except Exception:
        # Cualquier error: que conteste el modelo
        logger.exception("error_consultando_cache_de_respuestas")
        return None


async def guardar(pregunta: str, respuesta: str) -> None:
    """Guarda una respuesta impersonal. Nunca interrumpe la conversación.

    El embedding es opcional: si no hay proveedor configurado se guarda
    igual y el caché funciona por texto idéntico.
    """
    if not get_settings().cache_respuestas_activo or not _huella_prompt:
        return
    try:
        try:
            vector = (await embed_textos([pregunta], "documento"))[0]
        except Exception:
            vector = None
        async with get_session() as session:
            ya_estaba = (
                await session.execute(
                    select(RespuestaCacheada.id)
                    .where(
                        RespuestaCacheada.prompt_hash == _huella_prompt,
                        RespuestaCacheada.pregunta_normalizada == clave(pregunta),
                    )
                    .limit(1)
                )
            ).first()
            if ya_estaba:
                return
            session.add(
                RespuestaCacheada(
                    pregunta=pregunta.strip()[:MAX_LARGO],
                    pregunta_normalizada=clave(pregunta),
                    respuesta=respuesta,
                    embedding=vector,
                    prompt_hash=_huella_prompt,
                )
            )
            await session.commit()
        logger.info("respuesta_guardada_en_cache", con_embedding=vector is not None)
    except Exception:
        logger.exception("error_guardando_en_cache_de_respuestas")


async def limpiar(todo: bool = False) -> int:
    """Borra el caché. `todo=False` deja solo lo del prompt vigente."""
    from sqlalchemy import delete

    async with get_session() as session:
        consulta = delete(RespuestaCacheada)
        if not todo:
            consulta = consulta.where(RespuestaCacheada.prompt_hash != _huella_prompt)
        borradas = (await session.execute(consulta)).rowcount
        await session.commit()
    return borradas


def resumen_de(entradas: list[Any]) -> dict:
    """Cuántas respuestas hay guardadas y cuántas veces se han reusado."""
    return {
        "guardadas": len(entradas),
        "reusos": sum(getattr(e, "usos", 0) or 0 for e in entradas),
    }
