"""Nodos del grafo: carga de memoria, agente y actualización de memoria.

El system prompt (identidad + reglas + FAQs) va con cache_control ephemeral:
como los tools se renderizan antes del system en el prefijo del prompt, el
breakpoint sobre el último bloque estable del system cachea tools + system
juntos. El bloque dinámico (perfil/resumen) va DESPUÉS del breakpoint para no
invalidar el caché en cada turno.
"""

import datetime
import json
import re
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import structlog
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langgraph.store.base import BaseStore
from langgraph.types import interrupt

from ..config import get_settings
from ..graph.state import AgentState
from ..memory.long_term import PROMPT_EXTRACCION, guardar_perfil, leer_perfil
from ..memory.summarizer import _transcript
from ..observability import enmascarar_user_id

logger = structlog.get_logger(__name__)

# Ventana del turno reciente que ve el extractor de perfil
MENSAJES_PARA_EXTRACCION = 8
# Cada cuántos mensajes del cliente se extrae el perfil
CADA_CUANTOS_MENSAJES = 3


def toca_extraer(mensajes: list) -> bool:
    """Si en este turno toca extraer el perfil.

    Extraer en cada mensaje es una llamada al modelo por cada línea que
    escribe el cliente, y casi siempre no hay nada nuevo que guardar. No se
    pierde información: el extractor lee los últimos MENSAJES_PARA_EXTRACCION,
    así que lo que dijo en medio entra en la siguiente pasada. El primer
    mensaje sí se extrae, para no llegar tarde al nombre del cliente.
    """
    del_cliente = sum(1 for m in mensajes if isinstance(m, HumanMessage))
    if not del_cliente:
        return False
    return del_cliente == 1 or del_cliente % CADA_CUANTOS_MENSAJES == 0


def make_cargar_memoria(
    store: BaseStore | None,
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    async def cargar_memoria(state: AgentState) -> dict[str, Any]:
        perfil = await leer_perfil(store, state.get("user_id", ""))
        # La UI de un turno anterior no debe filtrarse al turno actual.
        return {"perfil": perfil, "ui_pendiente": None}

    return cargar_memoria


DIAS_SEMANA = [
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _contexto_temporal() -> str:
    """Fecha y hora actuales en la zona del negocio.

    El modelo no sabe que dia es hoy: sin esto inventaba fechas de su
    entrenamiento y consultar_disponibilidad las rechazaba por rango
    invalido. Va en el bloque dinamico, despues del breakpoint de cache.
    """
    ahora = datetime.datetime.now(ZoneInfo(get_settings().tz))
    return (
        "<contexto_temporal>\n"
        f"Hoy es {DIAS_SEMANA[ahora.weekday()]} {ahora.day} de "
        f"{MESES[ahora.month - 1]} de {ahora.year} "
        f"({ahora.strftime('%Y-%m-%d')}), {ahora.strftime('%H:%M')} hora de "
        "Ciudad de Mexico. Calcula SIEMPRE las fechas a partir de hoy; nunca "
        "las inventes ni uses otro anio.\n"
        "</contexto_temporal>"
    )


def _bloques_system(system_prompt: str, state: AgentState) -> list[dict[str, Any]]:
    bloques: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    dinamico: list[str] = [_contexto_temporal()]
    if state.get("perfil"):
        perfil_json = json.dumps(state["perfil"], ensure_ascii=False)
        dinamico.append(f"<perfil_cliente>\n{perfil_json}\n</perfil_cliente>")
    if state.get("resumen"):
        dinamico.append(
            f"<resumen_conversacion>\n{state['resumen']}\n</resumen_conversacion>"
        )
    if dinamico:
        bloques.append({"type": "text", "text": "\n\n".join(dinamico)})
    return bloques


def _log_resultados_de_tools(mensajes: list) -> None:
    """Registra el resultado de las tools del paso previo.

    Sin esto, un fallo de tool solo se ve como llamadas repetidas al modelo:
    no queda rastro de que devolvio la tool ni con que argumentos se llamo.
    """
    for mensaje in reversed(mensajes):
        if not isinstance(mensaje, ToolMessage):
            break
        contenido = (
            mensaje.content
            if isinstance(mensaje.content, str)
            else str(mensaje.content)
        )
        logger.info(
            "tool_resultado",
            tool=mensaje.name,
            status=getattr(mensaje, "status", None),
            resultado=contenido[:400],
        )


# Palabras con las que un cliente abre el tema de la cita
PALABRAS_DE_AGENDA = re.compile(
    r"\b(agend|cita|reagend|cancel|disponib|estudio de pisada|valoraci)",
    re.IGNORECASE,
)
# Si el hilo ya tocó una de estas, sigue en manos del modelo grande
TOOLS_DE_AGENDA = frozenset(
    {
        "consultar_disponibilidad",
        "agendar_cita",
        "cancelar_cita",
        "consultar_mis_citas",
        "buscar_sucursal",
        "listar_zonas",
        "presentar_opciones",
    }
)
# Cuántos mensajes atrás se mira para saber si la conversación va de citas
VENTANA_DE_AGENDA = 8


def es_conversacion_de_cita(state: AgentState) -> bool:
    """Si este turno necesita el modelo grande.

    Agendar son seis o siete pasos encadenados y es lo único que no se pudo
    medir en la comparación de modelos; ahí no se ahorra. Lo demás —precios,
    horarios, estado de pedido— salió idéntico con el modelo chico.

    La decisión es determinista a propósito: preguntarle a un modelo "¿esto
    es una cita?" costaría otra llamada y anularía el ahorro.
    """
    if state.get("ui_pendiente"):
        # Hay botones esperando: la respuesta del cliente es parte del flujo
        return True
    mensajes = state.get("messages", []) or []
    for mensaje in reversed(mensajes[-VENTANA_DE_AGENDA:]):
        if getattr(mensaje, "name", None) in TOOLS_DE_AGENDA:
            return True
        contenido = getattr(mensaje, "content", "")
        if isinstance(contenido, str) and PALABRAS_DE_AGENDA.search(contenido):
            return True
    return False


def make_agente(
    llm_con_tools: Runnable,
    system_prompt: str,
    llm_agenda: Runnable | None = None,
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    """`llm_agenda` atiende el flujo de citas; si es None, todo va al mismo."""

    async def agente(state: AgentState) -> dict[str, Any]:
        _log_resultados_de_tools(state["messages"])
        system = SystemMessage(content=_bloques_system(system_prompt, state))
        modelo = llm_con_tools
        if llm_agenda is not None and es_conversacion_de_cita(state):
            modelo = llm_agenda
        # El gasto se mide con un callback en el modelo (services/consumo.py),
        # no aquí: así también entran el extractor de perfil y el resumidor.
        respuesta = await modelo.ainvoke([system, *state["messages"]])
        for llamada in getattr(respuesta, "tool_calls", []) or []:
            logger.info(
                "tool_solicitada",
                tool=llamada.get("name"),
                args=llamada.get("args"),
            )
        return {"messages": [respuesta]}

    return agente


def make_actualizar_memoria(
    extractor: Runnable | None, store: BaseStore | None
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    """Extrae hechos duraderos del turno y hace upsert al Store (patrón langmem).

    Nunca rompe el flujo de respuesta: cualquier error solo se loguea.
    """

    async def actualizar_memoria(state: AgentState) -> dict[str, Any]:
        if extractor is None or store is None:
            return {}
        if not toca_extraer(state["messages"]):
            return {}
        try:
            fragmento = _transcript(state["messages"][-MENSAJES_PARA_EXTRACCION:])
            if not fragmento:
                return {}
            perfil = await extractor.ainvoke(
                [
                    SystemMessage(content=PROMPT_EXTRACCION),
                    HumanMessage(content=fragmento),
                ]
            )
            datos = {k: v for k, v in perfil.model_dump().items() if v}
            if datos:
                await guardar_perfil(store, state.get("user_id", ""), datos)
                logger.info(
                    "perfil_actualizado",
                    user_id=enmascarar_user_id(state.get("user_id", "")),
                    campos=sorted(datos),
                )
        except Exception:
            logger.exception("error_actualizando_memoria")
        return {}

    return actualizar_memoria


def escalamiento(state: AgentState) -> dict[str, Any]:
    """Pausa el thread tras un escalamiento (retomable con Command(resume=...)).

    Mientras el thread está pausado el bot guarda silencio: los mensajes del
    cliente los atiende el asesor humano. Al reanudar (endpoint interno
    /internal/escalamientos/resolver) se limpia la bandera y el grafo termina.
    """
    interrupt(
        {
            "motivo": "escalado_a_humano",
            "canal": state.get("canal", ""),
            "user_id": state.get("user_id", ""),
        }
    )
    return {"escalado": False}
