"""Nodos del grafo: carga de memoria, agente y actualización de memoria.

El system prompt (identidad + reglas + FAQs) va con cache_control ephemeral:
como los tools se renderizan antes del system en el prefijo del prompt, el
breakpoint sobre el último bloque estable del system cachea tools + system
juntos. El bloque dinámico (perfil/resumen) va DESPUÉS del breakpoint para no
invalidar el caché en cada turno.
"""

import json
from typing import Any, Awaitable, Callable

import structlog
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langgraph.store.base import BaseStore
from langgraph.types import interrupt

from ..graph.state import AgentState
from ..memory.long_term import PROMPT_EXTRACCION, guardar_perfil, leer_perfil
from ..memory.summarizer import _transcript
from ..observability import enmascarar_user_id

logger = structlog.get_logger(__name__)

# Ventana del turno reciente que ve el extractor de perfil
MENSAJES_PARA_EXTRACCION = 8


def make_cargar_memoria(
    store: BaseStore | None,
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    async def cargar_memoria(state: AgentState) -> dict[str, Any]:
        perfil = await leer_perfil(store, state.get("user_id", ""))
        # La UI de un turno anterior no debe filtrarse al turno actual.
        return {"perfil": perfil, "ui_pendiente": None}

    return cargar_memoria


def _bloques_system(system_prompt: str, state: AgentState) -> list[dict[str, Any]]:
    bloques: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    dinamico: list[str] = []
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


def make_agente(
    llm_con_tools: Runnable, system_prompt: str
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    async def agente(state: AgentState) -> dict[str, Any]:
        _log_resultados_de_tools(state["messages"])
        system = SystemMessage(content=_bloques_system(system_prompt, state))
        respuesta = await llm_con_tools.ainvoke([system, *state["messages"]])
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
