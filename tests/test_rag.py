"""Tests de Fase 4: chunking, factory de embeddings y búsqueda semántica.

El test de búsqueda usa Postgres real (fixture `db` del conftest; se salta si
no hay servidor) con un embedder falso determinista — no llama a ninguna API.
"""

import pytest

from sidhe_agent.db.models import EMBEDDING_DIM
from sidhe_agent.services import embeddings as embeddings_mod
from sidhe_agent.services import ingesta as ingesta_mod
from sidhe_agent.services.ingesta import trocear_texto
from sidhe_agent.tools import conocimiento as conocimiento_mod
from sidhe_agent.tools.conocimiento import _buscar_conocimiento

# --- Chunking (puro) -------------------------------------------------------


def test_trocear_agrupa_parrafos():
    texto = "Párrafo uno.\n\nPárrafo dos.\n\nPárrafo tres."
    assert trocear_texto(texto, max_chars=100) == [texto]


def test_trocear_respeta_max_chars():
    parrafos = [f"Este es el párrafo número {i} con algo de contenido." for i in range(20)]
    chunks = trocear_texto("\n\n".join(parrafos), max_chars=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    # No se pierde contenido
    assert "párrafo número 19" in chunks[-1]


def test_trocear_corta_parrafos_gigantes_con_overlap():
    gigante = "x" * 3000
    chunks = trocear_texto(gigante, max_chars=1000, overlap=200)
    assert all(len(c) <= 1000 for c in chunks)
    assert sum(len(c) for c in chunks) >= 3000  # el overlap duplica contexto


def test_trocear_texto_vacio():
    assert trocear_texto("   \n\n  ") == []


# --- Factory de embeddings -------------------------------------------------


async def test_proveedor_desconocido(monkeypatch):
    monkeypatch.setattr(
        embeddings_mod.get_settings(), "embeddings_provider", "otro", raising=False
    )
    with pytest.raises(ValueError, match="no soportado"):
        await embeddings_mod.embed_textos(["hola"])


async def test_bge_m3_es_stub(monkeypatch):
    monkeypatch.setattr(
        embeddings_mod.get_settings(), "embeddings_provider", "bge-m3", raising=False
    )
    with pytest.raises(NotImplementedError, match="stub"):
        await embeddings_mod.embed_textos(["hola"])


def test_modelos_default_cubren_proveedores_implementados():
    assert set(embeddings_mod.MODELOS_DEFAULT) == {"voyage", "cohere", "openai"}


# --- Búsqueda semántica sobre Postgres (skip sin DB) ------------------------


def _vector_falso(texto: str) -> list[float]:
    """Embedder determinista: eje 0 = plantillas, eje 1 = garantía."""
    v = [0.0] * EMBEDDING_DIM
    v[0] = 1.0 if "plantilla" in texto.lower() else 0.1
    v[1] = 1.0 if "garant" in texto.lower() else 0.1
    return v


async def _embed_falso(textos, tipo="documento"):
    return [_vector_falso(t) for t in textos]


async def test_busqueda_semantica_ordena_por_similitud(db, monkeypatch):
    monkeypatch.setattr(ingesta_mod, "embed_textos", _embed_falso)
    monkeypatch.setattr(conocimiento_mod, "embed_textos", _embed_falso)

    await ingesta_mod.ingestar_texto(
        titulo="Guía de producto",
        fuente="tests/guia.md",
        texto=(
            "Las plantillas se fabrican con TPU de alta resistencia.\n\n"
            "La garantía cubre defectos de fabricación por 90 días."
        ),
    )

    resultado = await _buscar_conocimiento("¿qué cubre la garantía?")
    assert resultado["resultados"][0]["texto"].startswith("La garantía")
    assert resultado["resultados"][0]["fuente"] == "Guía de producto"
    assert resultado["resultados"][0]["relevancia"] >= resultado["resultados"][-1]["relevancia"]

    # Re-ingesta idempotente por fuente: no duplica chunks
    await ingesta_mod.ingestar_texto(
        titulo="Guía de producto",
        fuente="tests/guia.md",
        texto="La garantía cubre defectos de fabricación por 90 días.",
    )
    resultado = await _buscar_conocimiento("garantía")
    assert len(resultado["resultados"]) == 1


async def test_busqueda_sin_corpus_devuelve_nota(db):
    resultado = await _buscar_conocimiento("¿hacen envíos?")
    assert resultado == {"resultados": [], "nota": "base documental aún sin cargar"}
