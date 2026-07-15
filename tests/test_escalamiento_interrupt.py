"""Test de integración del escalamiento: tool → despedida → interrupt → resume.

Sin Postgres: el registro en la tabla escalamientos falla y se degrada
(registrado=False), pero el flujo de pausa/reanudación es el real.
"""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from sidhe_agent.graph.builder import build_graph

USER = "+5215511111111"
CONFIG = {"configurable": {"thread_id": f"whatsapp:{USER}"}}


class FakeLLM(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def test_escalamiento_pausa_y_reanuda():
    llm = FakeLLM(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "escalar_a_humano",
                            "args": {"motivo": "queja de garantía"},
                            "id": "tc_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Un asesor humano te contactará pronto."),
            ]
        )
    )
    graph = build_graph(
        llm,
        checkpointer=MemorySaver(),
        store=InMemoryStore(),
        system_prompt="prompt de prueba",
    )

    resultado = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="quiero hablar con una persona")],
            "canal": "whatsapp",
            "user_id": USER,
        },
        CONFIG,
    )

    # El thread quedó pausado, con la despedida lista para enviarse
    assert "__interrupt__" in resultado
    assert resultado["escalado"] is True
    assert resultado["messages"][-1].content == "Un asesor humano te contactará pronto."

    snapshot = await graph.aget_state(CONFIG)
    assert any(t.interrupts for t in snapshot.tasks)

    # Reanudación (endpoint /internal/escalamientos/resolver)
    resultado = await graph.ainvoke(Command(resume="atendido"), CONFIG)
    assert "__interrupt__" not in resultado
    assert resultado["escalado"] is False

    snapshot = await graph.aget_state(CONFIG)
    assert not any(t.interrupts for t in snapshot.tasks)
