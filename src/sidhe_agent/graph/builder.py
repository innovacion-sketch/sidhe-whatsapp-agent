"""Construcción del StateGraph del agente.

Flujo:
    START → cargar_memoria → agente ⇄ tools
                              ├─(escalado)→ escalamiento (interrupt) → END
                              └─(sin tool calls)→ actualizar_memoria
                                                   ├─(>30 mensajes)→ resumir → END
                                                   └→ END
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.store.base import BaseStore

from ..memory.summarizer import make_resumir, necesita_resumen

from ..tools.citas import (
    agendar_cita,
    cancelar_cita,
    consultar_disponibilidad,
    consultar_mis_citas,
)
from ..tools.conocimiento import buscar_conocimiento
from ..tools.escalamiento import escalar_a_humano
from ..tools.presentacion import presentar_opciones
from ..tools.sucursales import buscar_sucursal, listar_zonas
from .nodes import (
    escalamiento,
    make_actualizar_memoria,
    make_agente,
    make_cargar_memoria,
)
from .state import AgentState

TOOLS = [
    buscar_conocimiento,
    escalar_a_humano,
    buscar_sucursal,
    listar_zonas,
    consultar_disponibilidad,
    agendar_cita,
    consultar_mis_citas,
    cancelar_cita,
    presentar_opciones,
]


def build_graph(
    llm: BaseChatModel,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    system_prompt: str = "",
    extractor: Runnable | None = None,
    resumidor: BaseChatModel | None = None,
) -> Any:
    """Compila el grafo.

    extractor: runnable de extracción de perfil (crear_extractor(llm)); si es
    None el nodo actualizar_memoria es pass-through. resumidor: LLM para el
    nodo resumir; default el mismo `llm`.
    """
    llm_con_tools = llm.bind_tools(TOOLS)

    grafo = StateGraph(AgentState)
    grafo.add_node("cargar_memoria", make_cargar_memoria(store))
    grafo.add_node("agente", make_agente(llm_con_tools, system_prompt))
    grafo.add_node("tools", ToolNode(TOOLS))
    grafo.add_node("actualizar_memoria", make_actualizar_memoria(extractor, store))
    grafo.add_node("resumir", make_resumir(resumidor or llm))
    grafo.add_node("escalamiento", escalamiento)

    def ruta_post_agente(state: AgentState) -> str:
        if tools_condition(state) == "tools":
            return "tools"
        return "escalamiento" if state.get("escalado") else "actualizar_memoria"

    def ruta_resumen(state: AgentState) -> str:
        return "resumir" if necesita_resumen(state.get("messages", [])) else END

    grafo.add_edge(START, "cargar_memoria")
    grafo.add_edge("cargar_memoria", "agente")
    grafo.add_conditional_edges(
        "agente",
        ruta_post_agente,
        {
            "tools": "tools",
            "escalamiento": "escalamiento",
            "actualizar_memoria": "actualizar_memoria",
        },
    )
    grafo.add_edge("tools", "agente")
    grafo.add_conditional_edges(
        "actualizar_memoria", ruta_resumen, {"resumir": "resumir", END: END}
    )
    grafo.add_edge("resumir", END)
    grafo.add_edge("escalamiento", END)

    return grafo.compile(checkpointer=checkpointer, store=store)
