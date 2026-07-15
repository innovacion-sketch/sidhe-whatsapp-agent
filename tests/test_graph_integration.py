"""Test de integración del grafo con checkpointer y store en memoria."""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from sidhe_agent.graph.builder import build_graph

USER_ID = "+5215512345678"
THREAD = {"configurable": {"thread_id": f"whatsapp:{USER_ID}"}}


class FakeLLM(GenericFakeChatModel):
    """El grafo llama bind_tools; el fake solo devuelve mensajes en orden."""

    def bind_tools(self, tools, **kwargs):
        return self


def _graph(respuestas: list[str], store: InMemoryStore | None = None):
    llm = FakeLLM(messages=iter([AIMessage(content=r) for r in respuestas]))
    return build_graph(
        llm,
        checkpointer=MemorySaver(),
        store=store,
        system_prompt="prompt de prueba",
    )


async def test_turno_simple():
    grafo = _graph(["¡Hola! Soy el asistente de Sidhe Group."])
    resultado = await grafo.ainvoke(
        {
            "messages": [HumanMessage(content="hola")],
            "canal": "whatsapp",
            "user_id": USER_ID,
        },
        THREAD,
    )
    assert resultado["messages"][-1].content == "¡Hola! Soy el asistente de Sidhe Group."


async def test_memoria_entre_turnos():
    """El checkpointer conserva el historial del thread entre invocaciones."""
    grafo = _graph(["respuesta 1", "respuesta 2"])
    await grafo.ainvoke(
        {
            "messages": [HumanMessage(content="primer mensaje")],
            "canal": "whatsapp",
            "user_id": USER_ID,
        },
        THREAD,
    )
    resultado = await grafo.ainvoke(
        {"messages": [HumanMessage(content="segundo mensaje")]},
        THREAD,
    )
    # 2 humanos + 2 del agente acumulados en el mismo thread
    assert len(resultado["messages"]) == 4
    contenidos = [m.content for m in resultado["messages"]]
    assert "primer mensaje" in contenidos
    assert "respuesta 2" == contenidos[-1]


async def test_carga_perfil_del_store():
    store = InMemoryStore()
    await store.aput(("perfiles", USER_ID), "perfil", {"nombre": "Ana"})
    grafo = _graph(["hola Ana"], store=store)
    resultado = await grafo.ainvoke(
        {
            "messages": [HumanMessage(content="hola")],
            "canal": "whatsapp",
            "user_id": USER_ID,
        },
        THREAD,
    )
    assert resultado["perfil"] == {"nombre": "Ana"}
