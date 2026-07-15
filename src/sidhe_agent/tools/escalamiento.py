"""Tool de escalamiento a un asesor humano.

Registra el escalamiento en la tabla `escalamientos` y marca escalado=True en
el estado; tras la respuesta de despedida del agente, el nodo `escalamiento`
del grafo pausa el thread con interrupt() de forma retomable.
"""

import json
from typing import Annotated

import structlog
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ..db.models import Escalamiento
from ..db.session import get_session
from ..observability import enmascarar_user_id

logger = structlog.get_logger(__name__)


def _resumir_contexto(mensajes: list, max_mensajes: int = 6) -> str:
    """Últimos mensajes del cliente como contexto para el asesor humano."""
    del_cliente = [m for m in mensajes if isinstance(m, HumanMessage)]
    lineas = []
    for m in del_cliente[-max_mensajes:]:
        contenido = m.content if isinstance(m.content, str) else str(m.content)
        lineas.append(f"- {contenido[:300]}")
    return "\n".join(lineas)


@tool
async def escalar_a_humano(
    motivo: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Escala la conversación a un asesor humano de Sidhe Group.

    Úsala cuando: el cliente pida explícitamente hablar con una persona, haya
    una queja de garantía, un tema médico que exceda las preguntas frecuentes,
    o tras 2 intentos fallidos de entender su solicitud. Después de llamarla,
    despídete confirmando al cliente que un asesor lo contactará pronto por
    este mismo chat; tras ese mensaje la conversación queda en manos del
    asesor.

    Args:
        motivo: razón breve y concreta del escalamiento, en español.
    """
    canal = state.get("canal", "desconocido")
    user_id = state.get("user_id", "")
    contexto = _resumir_contexto(state.get("messages", []))

    registrado = False
    try:
        async with get_session() as session:
            session.add(
                Escalamiento(
                    canal=canal,
                    user_id=user_id,
                    motivo=motivo,
                    contexto_resumen=contexto,
                    estado="pendiente",
                )
            )
            await session.commit()
            registrado = True
    except Exception:
        # El cliente no debe quedarse sin respuesta por un fallo de DB.
        logger.exception(
            "error_registrando_escalamiento", user_id=enmascarar_user_id(user_id)
        )

    logger.info(
        "escalamiento_solicitado",
        user_id=enmascarar_user_id(user_id),
        canal=canal,
        registrado=registrado,
    )
    return Command(
        update={
            "escalado": True,
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "escalado": True,
                            "registrado": registrado,
                            "instruccion": (
                                "Confirma al cliente que un asesor humano de Sidhe "
                                "Group lo contactará pronto por este mismo chat."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
