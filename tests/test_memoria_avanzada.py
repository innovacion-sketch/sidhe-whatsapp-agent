"""Tests de Fase 3: extracción de perfil y resumen/trim (sin DB ni red)."""

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.store.memory import InMemoryStore

from sidhe_agent.graph.nodes import make_actualizar_memoria
from sidhe_agent.memory.long_term import PerfilExtraido, leer_perfil
from sidhe_agent.memory.summarizer import (
    MENSAJES_A_CONSERVAR,
    make_resumir,
    necesita_resumen,
    particionar_mensajes,
)

USER = "+5215511111111"


class ExtractorStub:
    """Simula llm.with_structured_output(PerfilExtraido)."""

    def __init__(self, perfil: PerfilExtraido):
        self.perfil = perfil
        self.llamadas = 0

    async def ainvoke(self, _entrada):
        self.llamadas += 1
        return self.perfil


class ResumidorStub:
    async def ainvoke(self, _entrada):
        return AIMessage(content="Resumen de prueba de la conversación.")


def _estado(mensajes) -> dict:
    return {"messages": mensajes, "user_id": USER, "canal": "whatsapp"}


async def test_extraccion_hace_upsert_al_store():
    store = InMemoryStore()
    extractor = ExtractorStub(
        PerfilExtraido(nombre="Ana López", padecimiento="fascitis plantar")
    )
    nodo = make_actualizar_memoria(extractor, store)

    await nodo(
        _estado(
            [
                HumanMessage(content="Hola, soy Ana López y tengo fascitis plantar"),
                AIMessage(content="Mucho gusto Ana, te puedo ayudar."),
            ]
        )
    )
    perfil = await leer_perfil(store, USER)
    assert perfil == {"nombre": "Ana López", "padecimiento": "fascitis plantar"}

    # Segundo turno: merge sin borrar lo previo
    nodo2 = make_actualizar_memoria(
        ExtractorStub(PerfilExtraido(sucursal_habitual="Liverpool Perisur")), store
    )
    await nodo2(_estado([HumanMessage(content="Prefiero la sucursal Perisur")]))
    perfil = await leer_perfil(store, USER)
    assert perfil["nombre"] == "Ana López"
    assert perfil["sucursal_habitual"] == "Liverpool Perisur"


async def test_extraccion_sin_extractor_es_passthrough():
    nodo = make_actualizar_memoria(None, InMemoryStore())
    assert await nodo(_estado([HumanMessage(content="hola")])) == {}


async def test_extraccion_no_rompe_si_falla():
    class ExtractorRoto:
        async def ainvoke(self, _entrada):
            raise RuntimeError("boom")

    nodo = make_actualizar_memoria(ExtractorRoto(), InMemoryStore())
    assert await nodo(_estado([HumanMessage(content="hola")])) == {}


def _conversacion_larga(n_pares: int):
    mensajes = []
    for i in range(n_pares):
        mensajes.append(HumanMessage(content=f"pregunta {i}", id=f"h{i}"))
        mensajes.append(AIMessage(content=f"respuesta {i}", id=f"a{i}"))
    return mensajes


def test_umbral_de_resumen():
    assert not necesita_resumen(_conversacion_larga(15))  # 30 mensajes
    assert necesita_resumen(_conversacion_larga(16))  # 32 mensajes


def test_particion_no_parte_secuencias():
    mensajes = _conversacion_larga(17)  # 34 mensajes
    viejos = particionar_mensajes(mensajes)
    conservados = mensajes[len(viejos):]
    assert len(conservados) >= MENSAJES_A_CONSERVAR
    assert isinstance(conservados[0], HumanMessage)


async def test_resumir_trimea_y_guarda_resumen():
    mensajes = _conversacion_larga(17)
    nodo = make_resumir(ResumidorStub())
    resultado = await nodo({"messages": mensajes, "resumen": "Resumen previo."})

    assert resultado["resumen"] == "Resumen de prueba de la conversación."
    eliminados = resultado["messages"]
    assert all(isinstance(m, RemoveMessage) for m in eliminados)
    assert len(mensajes) - len(eliminados) >= MENSAJES_A_CONSERVAR
    # Los eliminados son exactamente los más viejos
    assert [m.id for m in eliminados] == [m.id for m in particionar_mensajes(mensajes)]


async def test_resumir_no_trimea_si_el_llm_falla():
    class ResumidorRoto:
        async def ainvoke(self, _entrada):
            raise RuntimeError("boom")

    nodo = make_resumir(ResumidorRoto())
    assert await nodo({"messages": _conversacion_larga(17)}) == {}
