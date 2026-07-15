"""Resumen + trim de conversaciones largas (patrón summarize & trim).

Cuando el historial supera UMBRAL_MENSAJES, los mensajes viejos se condensan
en state["resumen"] (que el agente recibe como <resumen_conversacion>) y se
eliminan del historial con RemoveMessage. Esto controla el costo de contexto
en conversaciones de semanas.
"""

from typing import Any, Awaitable, Callable

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)

logger = structlog.get_logger(__name__)

UMBRAL_MENSAJES = 30
MENSAJES_A_CONSERVAR = 10

PROMPT_RESUMEN = """\
Resume la siguiente conversación de WhatsApp entre un cliente y el asistente \
de Sidhe Group (plantillas ortopédicas). Conserva: nombre y datos del cliente, \
padecimientos mencionados, citas agendadas o canceladas (con sucursal, fecha y \
hora), preferencias, y cualquier pendiente. Sé conciso (máximo 200 palabras), \
en español, en tercera persona."""


def necesita_resumen(mensajes: list[AnyMessage]) -> bool:
    return len(mensajes) > UMBRAL_MENSAJES


def particionar_mensajes(mensajes: list[AnyMessage]) -> list[AnyMessage]:
    """Mensajes viejos a resumir. El corte nunca parte una secuencia de tools:
    el primer mensaje conservado siempre es un HumanMessage."""
    corte = max(0, len(mensajes) - MENSAJES_A_CONSERVAR)
    while corte < len(mensajes) and not isinstance(mensajes[corte], HumanMessage):
        corte += 1
    return mensajes[:corte]


def _transcript(mensajes: list[AnyMessage], max_por_mensaje: int = 400) -> str:
    lineas = []
    for mensaje in mensajes:
        if isinstance(mensaje, HumanMessage):
            rol = "Cliente"
        elif isinstance(mensaje, AIMessage):
            rol = "Asistente"
        else:
            rol = "Sistema/tool"
        contenido = (
            mensaje.content if isinstance(mensaje.content, str) else str(mensaje.content)
        )
        if contenido.strip():
            lineas.append(f"{rol}: {contenido[:max_por_mensaje]}")
    return "\n".join(lineas)


def make_resumir(
    llm: BaseChatModel,
) -> Callable[[dict], Awaitable[dict[str, Any]]]:
    async def resumir(state: dict) -> dict[str, Any]:
        mensajes = state.get("messages", [])
        viejos = particionar_mensajes(mensajes)
        if not viejos:
            return {}
        contexto = ""
        if state.get("resumen"):
            contexto = f"Resumen previo de la conversación:\n{state['resumen']}\n\n"
        try:
            respuesta = await llm.ainvoke(
                [
                    SystemMessage(content=PROMPT_RESUMEN),
                    HumanMessage(content=contexto + _transcript(viejos)),
                ]
            )
            resumen = (
                respuesta.content
                if isinstance(respuesta.content, str)
                else str(respuesta.content)
            )
        except Exception:
            # Sin resumen no se trimea: perder contexto es peor que pagarlo.
            logger.exception("error_resumiendo_conversacion")
            return {}
        return {
            "resumen": resumen.strip(),
            "messages": [RemoveMessage(id=m.id) for m in viejos if m.id],
        }

    return resumir
