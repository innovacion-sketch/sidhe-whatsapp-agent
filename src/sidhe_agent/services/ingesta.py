"""Ingesta de documentos para RAG: chunking + embeddings → pgvector.

Chunking por párrafos con empaquetado greedy hasta MAX_CHARS; los párrafos
que exceden el máximo se cortan duro con OVERLAP de contexto. Re-ingestar un
documento con la misma `fuente` reemplaza sus chunks anteriores.
"""

import re

import structlog
from sqlalchemy import delete, select

from ..db.models import Chunk, Documento
from ..db.session import get_session
from .embeddings import embed_textos

logger = structlog.get_logger(__name__)

MAX_CHARS = 1200
OVERLAP = 200
LOTE_EMBEDDINGS = 64


def trocear_texto(
    texto: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP
) -> list[str]:
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    chunks: list[str] = []
    actual = ""

    def cerrar() -> None:
        nonlocal actual
        if actual:
            chunks.append(actual)
            actual = ""

    for parrafo in parrafos:
        if len(parrafo) > max_chars:
            cerrar()
            paso = max_chars - overlap
            for i in range(0, len(parrafo), paso):
                pedazo = parrafo[i : i + max_chars]
                if pedazo.strip():
                    chunks.append(pedazo.strip())
                if i + max_chars >= len(parrafo):
                    break
            continue
        if actual and len(actual) + len(parrafo) + 2 > max_chars:
            cerrar()
        actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    cerrar()
    return chunks


async def ingestar_texto(
    titulo: str, fuente: str, texto: str, metadatos: dict | None = None
) -> dict:
    """Trocea, vectoriza y guarda un documento. Devuelve conteos."""
    pedazos = trocear_texto(texto)
    if not pedazos:
        return {"documento_id": None, "chunks": 0}

    vectores: list[list[float]] = []
    for i in range(0, len(pedazos), LOTE_EMBEDDINGS):
        vectores.extend(
            await embed_textos(pedazos[i : i + LOTE_EMBEDDINGS], "documento")
        )

    async with get_session() as session:
        # Reemplazo idempotente por fuente
        previos = (
            (await session.execute(select(Documento).where(Documento.fuente == fuente)))
            .scalars()
            .all()
        )
        for previo in previos:
            await session.execute(delete(Chunk).where(Chunk.documento_id == previo.id))
            await session.delete(previo)

        documento = Documento(titulo=titulo, fuente=fuente, metadatos=metadatos or {})
        session.add(documento)
        await session.flush()
        for indice, (pedazo, vector) in enumerate(zip(pedazos, vectores)):
            session.add(
                Chunk(
                    documento_id=documento.id,
                    texto=pedazo,
                    embedding=vector,
                    metadatos={"indice": indice, "fuente": fuente},
                )
            )
        await session.commit()
        logger.info(
            "documento_ingerido",
            documento_id=documento.id,
            titulo=titulo,
            chunks=len(pedazos),
            reemplazados=len(previos),
        )
        return {"documento_id": documento.id, "chunks": len(pedazos)}
