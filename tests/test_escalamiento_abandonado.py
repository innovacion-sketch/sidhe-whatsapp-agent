"""La red de las cuatro horas: que de verdad se dispare.

Un cliente mandó sus datos de cita a un escalamiento que nadie atendió y
se quedó dos horas y media sin respuesta, con la conversación pintada de
gris ("con el bot") en el panel. El bot callaba porque el hilo seguía
interrumpido.

La causa era el orden: se miraba el interrupt ANTES que el estado de
atención, y un hilo escalado siempre tiene interrupt, así que nunca se
llegaba a reactivar. La protección estaba muerta justo en el caso para el
que se escribió.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sidhe_agent import main
from sidhe_agent.services import conversaciones


class _Grafo:
    """Un grafo con el hilo interrumpido, como queda tras escalar."""

    def __init__(self):
        self.invocado = False

    async def aget_state(self, config):
        tarea = types.SimpleNamespace(interrupts=("esperando asesor",))
        return types.SimpleNamespace(tasks=[tarea], values={}, next=())

    async def ainvoke(self, *args, **kwargs):
        self.invocado = True


@pytest.mark.asyncio
async def test_si_nadie_atendio_en_horas_el_bot_retoma_aunque_este_interrumpido(
    monkeypatch,
):
    grafo = _Grafo()
    app = types.SimpleNamespace(state=types.SimpleNamespace(graph=grafo))
    devuelto = {}

    async def _revisar_pausa(canal, user_id, horas):
        return conversaciones.REACTIVADO

    async def _devolver(canal, user_id, nota):
        devuelto["nota"] = nota
        return True

    monkeypatch.setattr(main.conversaciones, "revisar_pausa", _revisar_pausa)
    monkeypatch.setattr(main, "_devolver_al_agente", _devolver)

    entrante = main.IncomingMessage(
        canal="whatsapp", user_id="+5212283407867", tipo="texto",
        contenido="Si me confirma por favor",
    )
    await main.procesar_mensaje(app, entrante)

    assert devuelto.get("nota") == main.NOTA_NADIE_ATENDIO, (
        "el hilo abandonado tiene que reanudarse, no quedarse mudo"
    )
