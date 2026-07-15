"""Embeddings vía API con proveedor conmutable (EMBEDDINGS_PROVIDER).

Implementados: voyage, cohere, openai. Stub: bge-m3 (self-hosted futuro).
Todos devuelven vectores de EMBEDDING_DIM=1024 (dimensión de la columna
chunks.embedding); si el proveedor devuelve otra dimensión se falla explícito
para no guardar vectores incompatibles.

Los proveedores que distinguen documento/consulta (voyage, cohere) reciben el
input_type correcto — mejora la calidad de la búsqueda asimétrica.
"""

from typing import Literal

import httpx

from ..config import get_settings
from ..db.models import EMBEDDING_DIM

TipoTexto = Literal["documento", "consulta"]

MODELOS_DEFAULT = {
    "voyage": "voyage-3.5",
    "cohere": "embed-multilingual-v3.0",
    "openai": "text-embedding-3-small",
}

PROVEEDORES = ("voyage", "cohere", "openai", "bge-m3")


def modelo_configurado() -> str:
    settings = get_settings()
    return settings.embeddings_model or MODELOS_DEFAULT.get(
        settings.embeddings_provider, ""
    )


def _validar_dimension(vectores: list[list[float]]) -> list[list[float]]:
    for vector in vectores:
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
                f"El proveedor devolvió dimensión {len(vector)}; "
                f"la columna chunks.embedding es vector({EMBEDDING_DIM})"
            )
    return vectores


async def _via_voyage(textos: list[str], tipo: TipoTexto) -> list[list[float]]:
    settings = get_settings()
    if not settings.voyage_api_key:
        raise ValueError("Falta VOYAGE_API_KEY para EMBEDDINGS_PROVIDER=voyage")
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {settings.voyage_api_key}"}, timeout=60.0
    ) as client:
        respuesta = await client.post(
            "https://api.voyageai.com/v1/embeddings",
            json={
                "model": modelo_configurado(),
                "input": textos,
                "input_type": "document" if tipo == "documento" else "query",
                "output_dimension": EMBEDDING_DIM,
            },
        )
        respuesta.raise_for_status()
        datos = sorted(respuesta.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in datos]


async def _via_cohere(textos: list[str], tipo: TipoTexto) -> list[list[float]]:
    settings = get_settings()
    if not settings.cohere_api_key:
        raise ValueError("Falta COHERE_API_KEY para EMBEDDINGS_PROVIDER=cohere")
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {settings.cohere_api_key}"}, timeout=60.0
    ) as client:
        respuesta = await client.post(
            "https://api.cohere.com/v2/embed",
            json={
                "model": modelo_configurado(),
                "texts": textos,
                "input_type": (
                    "search_document" if tipo == "documento" else "search_query"
                ),
                "embedding_types": ["float"],
            },
        )
        respuesta.raise_for_status()
        return respuesta.json()["embeddings"]["float"]


async def _via_openai(textos: list[str], tipo: TipoTexto) -> list[list[float]]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise ValueError("Falta OPENAI_API_KEY para EMBEDDINGS_PROVIDER=openai")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    respuesta = await client.embeddings.create(
        model=modelo_configurado(),
        input=textos,
        dimensions=EMBEDDING_DIM,
    )
    datos = sorted(respuesta.data, key=lambda d: d.index)
    return [d.embedding for d in datos]


async def _via_bge_m3(textos: list[str], tipo: TipoTexto) -> list[list[float]]:
    raise NotImplementedError(
        "Proveedor bge-m3 es un stub: despliega BGE-M3 self-hosted, apunta "
        "EMBEDDINGS_BASE_URL a su endpoint e implementa esta función "
        "(POST {base_url}/embed con {'inputs': textos})."
    )


_DISPATCH = {
    "voyage": _via_voyage,
    "cohere": _via_cohere,
    "openai": _via_openai,
    "bge-m3": _via_bge_m3,
}


async def embed_textos(
    textos: list[str], tipo: TipoTexto = "documento"
) -> list[list[float]]:
    """Vectoriza una lista de textos con el proveedor configurado."""
    proveedor = get_settings().embeddings_provider
    if proveedor not in _DISPATCH:
        raise ValueError(
            f"EMBEDDINGS_PROVIDER '{proveedor}' no soportado; "
            f"usa uno de: {', '.join(PROVEEDORES)}"
        )
    if not textos:
        return []
    return _validar_dimension(await _DISPATCH[proveedor](textos, tipo))
