"""Qué respuestas está reusando el bot sin volver a pagarle a Claude.

Dos preguntas que este script contesta:

1. **¿Está ahorrando?** Cada entrada trae cuántas veces se reusó. Lo que
   importa no es cuántas hay guardadas sino la suma de reusos: eso son
   llamadas al modelo que no se hicieron.
2. **¿Se coló algo de alguien?** Esta es la importante. Una respuesta que
   diga "nos vemos el miércoles 23" es la cita de una persona, lista para
   dársela a otra. El script marca con `[?]` todo lo que huela a eso,
   usando exactamente el mismo filtro que decide qué se guarda, para que
   la auditoría y el candado no se separen nunca.

Solo lee, salvo que se le pase `--borrar`. Uso:

    python scripts/ver_cache.py                # todo, lo más reusado arriba
    python scripts/ver_cache.py --sospechosas  # solo lo que hay que revisar
    python scripts/ver_cache.py --completas    # sin recortar las respuestas
    python scripts/ver_cache.py --borrar       # vacía el caché (pide confirmar)
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import delete, select

from sidhe_agent.db.models import RespuestaCacheada
from sidhe_agent.db.session import dispose_engine, get_session
from sidhe_agent.services.cache_respuestas import CONTEXTUAL, es_pregunta_generica

RECORTE = 160


def sospechosa(entrada: RespuestaCacheada) -> str:
    """Por qué habría que revisar esta entrada, o "" si está limpia."""
    if CONTEXTUAL.search(entrada.respuesta or ""):
        return "habla de esa conversación"
    if not es_pregunta_generica(entrada.pregunta or ""):
        return "no parece una pregunta de catálogo"
    return ""


def una_linea(texto: str, completa: bool) -> str:
    limpio = " ".join((texto or "").split())
    if completa or len(limpio) <= RECORTE:
        return limpio
    return limpio[:RECORTE] + "…"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Muestra el caché de respuestas")
    parser.add_argument(
        "--sospechosas",
        action="store_true",
        help="Solo las entradas que habría que revisar",
    )
    parser.add_argument(
        "--completas", action="store_true", help="No recortar las respuestas"
    )
    parser.add_argument(
        "--borrar", action="store_true", help="Vacía el caché (se vuelve a llenar solo)"
    )
    args = parser.parse_args()

    async with get_session() as session:
        entradas = list(
            (
                await session.execute(
                    select(RespuestaCacheada).order_by(
                        RespuestaCacheada.usos.desc(), RespuestaCacheada.id
                    )
                )
            ).scalars()
        )

        if args.borrar:
            print(f"Se van a borrar {len(entradas)} entradas.")
            if input("Escribe BORRAR para confirmar: ").strip() != "BORRAR":
                print("Cancelado, no se tocó nada.")
                await dispose_engine()
                return
            borradas = (await session.execute(delete(RespuestaCacheada))).rowcount
            await session.commit()
            print(f"Borradas: {borradas}. El caché se vuelve a llenar solo.")
            await dispose_engine()
            return

    revisar = [(e, sospechosa(e)) for e in entradas]
    marcadas = [(e, motivo) for e, motivo in revisar if motivo]
    mostrar = marcadas if args.sospechosas else revisar

    reusos = sum(e.usos or 0 for e in entradas)
    print(f"Guardadas: {len(entradas)}   Reusos: {reusos}   Por revisar: {len(marcadas)}")
    if reusos:
        print(f"Son {reusos} llamadas al modelo que no se hicieron.")
    print()

    for entrada, motivo in mostrar:
        marca = f"[?] {motivo}" if motivo else f"{entrada.usos or 0} reusos"
        fecha = entrada.creado_en.strftime("%d/%m") if entrada.creado_en else "?"
        print(f"--- #{entrada.id} · {marca} · {fecha}")
        print(f"  P: {una_linea(entrada.pregunta, args.completas)}")
        print(f"  R: {una_linea(entrada.respuesta, args.completas)}")

    if marcadas and not args.sospechosas:
        print(f"\nHay {len(marcadas)} marcadas con [?]. Vuelve a correrlo con"
              " --sospechosas para verlas solas.")
    if not entradas:
        print("El caché está vacío: se llena solo conforme la gente pregunta.")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
