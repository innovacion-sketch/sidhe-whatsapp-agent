"""Tool presentar_opciones: el agente decide la UI, el adapter la renderiza.

El resultado se guarda en state["ui_pendiente"] vía Command; al final del
turno main.py lo adjunta al OutgoingMessage y el WhatsAppTwilioAdapter lo
convierte a list-picker o quick-reply de la Content API.
"""

import json
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import ValidationError

from ..channels.schemas import Opcion, UIElement


@tool
def presentar_opciones(
    tipo: Literal["lista", "botones"],
    titulo: str,
    opciones: list[dict],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Muestra opciones tocables al cliente en WhatsApp (lista o botones).

    Úsala siempre que el cliente deba elegir entre opciones (zonas,
    sucursales, fechas, horarios, confirmación): nunca le pidas escribir lo
    que puede tocar. Límites de WhatsApp: "lista" admite hasta 10 opciones;
    "botones" hasta 3. El texto de tu respuesta acompaña a las opciones como
    cuerpo del mensaje.

    Args:
        tipo: "lista" (menú desplegable) o "botones" (respuestas rápidas).
        titulo: texto del botón que abre la lista o encabezado, máx 24 chars.
        opciones: lista de objetos {"id": str, "etiqueta": str (máx 24),
            "descripcion": str opcional (máx 72)}. El id regresa textual
            cuando el cliente toca la opción (ej. "suc_12", "slot_8841",
            "confirmar").
    """
    try:
        ui = UIElement(
            tipo=tipo,
            titulo=titulo,
            opciones=[Opcion(**opcion) for opcion in opciones],
        )
    except (ValidationError, TypeError) as exc:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {"error": "ui_invalida", "detalle": str(exc)},
                            ensure_ascii=False,
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    return Command(
        update={
            "ui_pendiente": ui.model_dump(),
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "ok": True,
                            "instruccion": (
                                "Las opciones se mostrarán como botones junto a tu "
                                "respuesta. Escribe ahora un texto breve que las "
                                "acompañe, sin repetir las opciones."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
