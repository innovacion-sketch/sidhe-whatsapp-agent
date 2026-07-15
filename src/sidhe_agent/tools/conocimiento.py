"""Tool de búsqueda semántica en la base documental (RAG sobre pgvector).

Búsqueda por coseno (índice HNSW) top-5 sobre `chunks`. Si la base está vacía
o el proveedor de embeddings no está configurado, devuelve lista vacía con
nota para que el agente lo diga honestamente y ofrezca escalar.
"""

import structlog
from langchain_core.tools import tool
from sqlalchemy import func, select

from ..db.models import Chunk, Documento
from ..db.session import get_session
from ..services.embeddings import embed_textos

logger = structlog.get_logger(__name__)

TOP_K = 5


async def _buscar_conocimiento(query: str) -> dict:
    try:
        async with get_session() as session:
            hay_chunks = (
                await session.execute(select(func.count(Chunk.id)))
            ).scalar_one()
        if not hay_chunks:
            return {"resultados": [], "nota": "base documental aún sin cargar"}

        vector = (await embed_textos([query], "consulta"))[0]

        async with get_session() as session:
            distancia = Chunk.embedding.cosine_distance(vector).label("distancia")
            filas = (
                await session.execute(
                    select(Chunk.texto, Documento.titulo, distancia)
                    .join(Documento, Chunk.documento_id == Documento.id)
                    .where(Chunk.embedding.is_not(None))
                    .order_by(distancia)
                    .limit(TOP_K)
                )
            ).all()
        return {
            "resultados": [
                {
                    "texto": texto,
                    "fuente": titulo,
                    "relevancia": round(1 - dist, 3),
                }
                for texto, titulo, dist in filas
            ]
        }
    except (ValueError, NotImplementedError) as exc:
        # Proveedor de embeddings mal configurado o stub
        logger.warning("rag_no_configurado", detalle=str(exc))
        return {"resultados": [], "nota": "base documental no disponible"}
    except Exception:
        logger.exception("error_buscando_conocimiento")
        return {"resultados": [], "nota": "error consultando la base documental"}


@tool
async def buscar_conocimiento(query: str) -> dict:
    """Busca información adicional en la base documental de Sidhe Group.

    Úsala SOLO cuando la respuesta no esté en las preguntas frecuentes del
    system prompt. Devuelve hasta 5 fragmentos con su fuente y relevancia
    (0 a 1). Usa únicamente fragmentos relevantes a la pregunta; si no hay
    resultados útiles, dilo honestamente al cliente y ofrece escalar a un
    asesor humano.

    Args:
        query: pregunta o tema a buscar, en español.
    """
    return await _buscar_conocimiento(query)
