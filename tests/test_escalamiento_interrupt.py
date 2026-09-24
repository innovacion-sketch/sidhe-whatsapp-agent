"""Test de integración del escalamiento: tool → despedida → interrupt → resume.

Sin Postgres: la sesión de base se sustituye por una que "guarda" en
memoria. Antes se dejaba fallar el registro y el hilo se pausaba igual;
ahora un escalamiento que no se guardó NO pausa al bot (nadie se iba a
enterar), así que el flujo de pausa necesita que el registro funcione.
"""

from contextlib import asynccontextmanager

import pytest

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from sidhe_agent.graph.builder import build_graph
from sidhe_agent.tools import escalamiento as tool_escalamiento

USER = "+5215511111111"
CONFIG = {"configurable": {"thread_id": f"whatsapp:{USER}"}}


class _SesionQueGuarda:
    def __init__(self):
        self.guardados = []

    def add(self, objeto):
        self.guardados.append(objeto)

    async def commit(self):
        pass


@pytest.fixture
def base_que_guarda(monkeypatch):
    sesion = _SesionQueGuarda()

    @asynccontextmanager
    async def _get_session():
        yield sesion

    monkeypatch.setattr(tool_escalamiento, "get_session", _get_session)
    return sesion


@pytest.fixture
def base_caida(monkeypatch):
    @asynccontextmanager
    async def _get_session():
        raise ConnectionRefusedError("sin base")
        yield  # pragma: no cover

    monkeypatch.setattr(tool_escalamiento, "get_session", _get_session)


class FakeLLM(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def test_escalamiento_pausa_y_reanuda(base_que_guarda):
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


async def test_si_el_escalamiento_no_se_guardo_el_bot_no_se_calla(base_caida):
    """Pausar sin registro deja al cliente hablándole a nadie.

    Nadie del equipo se entera de un escalamiento que no quedó en la base.
    Antes el hilo se pausaba igual y el bot se callaba para siempre; ahora
    sigue a cargo y le da al cliente el teléfono de su sucursal.
    """
    llm = FakeLLM(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "escalar_a_humano",
                            "args": {"motivo": "queja"},
                            "id": "tc_9",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Te paso el teléfono de tu sucursal."),
            ]
        )
    )
    graph = build_graph(
        llm, checkpointer=MemorySaver(), store=InMemoryStore(), system_prompt="p"
    )
    config = {"configurable": {"thread_id": "whatsapp:+5215599999999"}}

    resultado = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="quiero una persona")],
            "canal": "whatsapp",
            "user_id": "+5215599999999",
        },
        config,
    )

    assert "__interrupt__" not in resultado
    assert not resultado.get("escalado")
