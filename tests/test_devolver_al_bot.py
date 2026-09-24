"""Devolver al bot tiene que devolverle también lo que se habló sin él.

Mientras la conversación está en pausa, los mensajes del cliente y del
asesor se guardan en `mensajes` pero nunca pasan por el grafo. Si al
reanudar no se le entregan, el bot retoma en blanco: vuelve a pedir el
nombre, la sucursal y el horario que el cliente ya le dio a la persona.
"""

import datetime
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from langchain_core.messages import AIMessage, HumanMessage

from sidhe_agent import main


class _Grafo:
    def __init__(self):
        self.estado_escrito = None

    async def aget_state(self, config):
        return types.SimpleNamespace(tasks=[], values={}, next=())

    async def ainvoke(self, *args, **kwargs):
        return None

    async def aupdate_state(self, config, valores):
        self.estado_escrito = valores


@pytest.fixture
def grafo(monkeypatch):
    g = _Grafo()
    main.app.state.graph = g
    return g


@pytest.mark.asyncio
async def test_lo_hablado_en_la_pausa_vuelve_al_hilo(grafo, monkeypatch):
    async def _inicio(canal, user_id):
        return datetime.datetime(2026, 9, 23, 15, 55)

    async def _hablado(canal, user_id, desde):
        return [
            ("out", "Con gusto la ayudo, ¿me pasa sus datos?"),
            ("in", "Plaza Américas Xalapa, sábado 11:00"),
            ("in", "Diana Kristal Bravo Olguín"),
        ]

    monkeypatch.setattr(main.conversaciones, "inicio_de_la_pausa", _inicio)
    monkeypatch.setattr(main.conversaciones, "hablado_durante_la_pausa", _hablado)

    await main._devolver_al_agente("whatsapp", "+5212283407867", "nota")

    mensajes = grafo.estado_escrito["messages"]
    # El asesor entra como voz del bot; el cliente, como suya
    assert isinstance(mensajes[0], AIMessage)
    assert [type(m).__name__ for m in mensajes[1:3]] == ["HumanMessage"] * 2
    assert "Diana Kristal" in mensajes[2].content
    # La nota va al final, ya con el contexto delante
    assert mensajes[-1].content == "nota"
    assert grafo.estado_escrito["escalado"] is False


@pytest.mark.asyncio
async def test_si_no_se_puede_leer_el_contexto_igual_se_devuelve(grafo, monkeypatch):
    """Mejor un bot sin contexto que una conversación que no se puede devolver."""

    async def _revienta(canal, user_id):
        raise RuntimeError("base caída")

    monkeypatch.setattr(main.conversaciones, "inicio_de_la_pausa", _revienta)

    await main._devolver_al_agente("whatsapp", "+521", "nota")

    mensajes = grafo.estado_escrito["messages"]
    assert [m.content for m in mensajes] == ["nota"]


@pytest.mark.asyncio
async def test_sin_escalamiento_previo_no_se_inventa_contexto(grafo, monkeypatch):
    async def _sin_pausa(canal, user_id):
        return None

    monkeypatch.setattr(main.conversaciones, "inicio_de_la_pausa", _sin_pausa)

    await main._devolver_al_agente("whatsapp", "+521", "nota")

    assert [m.content for m in grafo.estado_escrito["messages"]] == ["nota"]
