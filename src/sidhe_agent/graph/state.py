"""Estado del grafo del agente."""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    canal: str
    user_id: str
    perfil: dict  # cargado del Store al inicio del turno
    resumen: str  # resumen de historial antiguo si aplica (Fase 3)
    escalado: bool
    # UI interactiva decidida por la tool presentar_opciones en este turno;
    # el adapter la convierte a list-picker/quick-reply al enviar.
    ui_pendiente: dict | None
