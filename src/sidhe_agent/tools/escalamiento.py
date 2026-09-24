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
from ..services import horario_asesores

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
    sigue la "instruccion" que devuelve: dice qué prometerle al cliente
    según el horario de los asesores. Tras ese mensaje la conversación queda
    en manos del asesor.

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
            # Sin registro nadie se entera del escalamiento: pausar al bot
            # sería dejar al cliente hablándole a nadie
            "escalado": registrado,
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "escalado": registrado,
                            "registrado": registrado,
                            "instruccion": instruccion_para_el_cliente(
                                registrado, horario_asesores.ahora_local()
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def instruccion_para_el_cliente(registrado: bool, momento) -> str:
    """Qué prometerle al cliente, sin prometer lo que no depende del bot.

    Antes siempre era "un asesor te contactará pronto", también a las 11 de
    la noche de un domingo y también cuando el escalamiento no se había
    podido guardar, o sea cuando nadie se iba a enterar.
    """
    if not registrado:
        return (
            "No se pudo pasar la conversación a un asesor. NO le digas al "
            "cliente que alguien lo va a contactar. Discúlpate brevemente y "
            "dale el TELÉFONO de su sucursal (buscar_sucursal) para que lo "
            "atiendan directo; si no sabes su sucursal, pregúntale su ciudad "
            "o zona."
        )
    cuando = horario_asesores.cuando_contestan(momento)
    if not cuando:
        return (
            "Confirma al cliente que un asesor de Sidhe Group lo atenderá en "
            "breve por este mismo chat."
        )
    return (
        f"Ahorita no hay asesores: atienden {horario_asesores.horario_legible()}. "
        f"Dile al cliente que un asesor le contestará {cuando} por este mismo "
        "chat. NO digas 'pronto' ni 'en breve'. Si su tema es urgente, "
        "ofrécele también el TELÉFONO de su sucursal (buscar_sucursal)."
    )
