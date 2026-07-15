"""Ingesta documentos (.md / .txt) a la base documental RAG.

Uso:
    uv run python scripts/ingest_documents.py data/docs/garantias.md [más rutas...]
    uv run python scripts/ingest_documents.py data/docs/*.md --titulo "Política de garantías"

Requiere EMBEDDINGS_PROVIDER y la API key correspondiente en .env
(VOYAGE_API_KEY / COHERE_API_KEY / OPENAI_API_KEY). Re-ingestar el mismo
archivo reemplaza sus chunks (idempotente por ruta).
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sidhe_agent.db.session import dispose_engine
from sidhe_agent.services.ingesta import ingestar_texto


async def main(rutas: list[str], titulo: str | None) -> None:
    total_chunks = 0
    for ruta_str in rutas:
        ruta = Path(ruta_str)
        if not ruta.is_file():
            print(f"[omitido] no existe: {ruta}")
            continue
        texto = ruta.read_text(encoding="utf-8")
        resultado = await ingestar_texto(
            titulo=titulo or ruta.stem.replace("_", " ").capitalize(),
            fuente=str(ruta),
            texto=texto,
            metadatos={"archivo": ruta.name},
        )
        print(f"[ok] {ruta} → documento {resultado['documento_id']}, "
              f"{resultado['chunks']} chunks")
        total_chunks += resultado["chunks"]
    print(f"\nTotal: {total_chunks} chunks ingeridos")
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("rutas", nargs="+", help="archivos .md/.txt a ingerir")
    parser.add_argument("--titulo", default=None, help="título del documento")
    args = parser.parse_args()
    asyncio.run(main(args.rutas, args.titulo))
