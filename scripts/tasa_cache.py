"""Si el caché de prompt de Anthropic está sirviendo, y si conviene "1h".

Dos mediciones, porque son dos preguntas distintas:

**Cuánto se está leyendo de caché.** De `uso_modelo`: qué parte de los
tokens de entrada se leyeron a 0.1x en vez de pagarse enteros. La consola
de Anthropic se queja cuando ese número es bajo.

**Si el caché alcanza a vivir.** Esta es la que decide el TTL. El caché
default dura 5 minutos y en WhatsApp el cliente tarda lo que tarda en
contestar: si la mayoría de las respuestas llegan pasados esos 5 minutos,
cada mensaje está reescribiendo el prompt a 1.25x en vez de leerlo a 0.1x,
y subir a "1h" (2x escribir, una sola vez) sale más barato. Si contestan
rápido, "5m" ya está bien y subirlo sería pagar de más.

El script mide los huecos reales entre mensajes seguidos de la misma
persona y dice cuál de las dos cosas conviene. Solo lee. Uso:

    python scripts/tasa_cache.py            # últimos 30 días
    python scripts/tasa_cache.py --dias 7
"""

import argparse
import asyncio
import datetime
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select

from sidhe_agent.config import get_settings
from sidhe_agent.db.models import Mensaje, UsoModelo
from sidhe_agent.db.session import dispose_engine, get_session

# Un hueco más largo que esto ya no es la misma conversación: el cliente se
# fue y volvió, y ningún TTL razonable cubre eso.
CORTE_CONVERSACION = datetime.timedelta(hours=1)


def porcentaje(parte: int, total: int) -> str:
    return f"{100 * parte / total:.0f}%" if total else "—"


async def lecturas(session, desde: datetime.date) -> list[UsoModelo]:
    return list(
        (
            await session.execute(
                select(UsoModelo).where(UsoModelo.fecha >= desde).order_by(UsoModelo.modelo)
            )
        ).scalars()
    )


async def huecos(session, desde: datetime.datetime) -> list[datetime.timedelta]:
    """Cuánto tarda cada cliente en escribir su siguiente mensaje."""
    filas = list(
        (
            await session.execute(
                select(Mensaje.user_id, Mensaje.creado_en)
                .where(Mensaje.direccion == "in", Mensaje.creado_en >= desde)
                .order_by(Mensaje.user_id, Mensaje.creado_en)
            )
        )
    )
    por_persona: dict[str, list] = defaultdict(list)
    for user_id, creado_en in filas:
        por_persona[user_id].append(creado_en)
    return [
        momentos[i] - momentos[i - 1]
        for momentos in por_persona.values()
        for i in range(1, len(momentos))
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Mide el caché de prompt")
    parser.add_argument("--dias", type=int, default=30)
    args = parser.parse_args()

    hoy = datetime.date.today()
    desde = hoy - datetime.timedelta(days=args.dias)
    desde_dt = datetime.datetime.combine(desde, datetime.time.min, datetime.timezone.utc)

    async with get_session() as session:
        uso = await lecturas(session, desde)
        gaps = await huecos(session, desde_dt)

    print(f"Últimos {args.dias} días\n")

    print("LO QUE SE LEE DE CACHÉ")
    if not uso:
        print("  Sin datos en uso_modelo todavía.")
    total_entrada = total_leido = total_escrito = 0
    por_modelo: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for fila in uso:
        acumulado = por_modelo[fila.modelo]
        acumulado[0] += int(fila.llamadas or 0)
        acumulado[1] += int(fila.entrada or 0)
        acumulado[2] += int(fila.cache_lectura or 0)
        acumulado[3] += int(fila.cache_escritura or 0)
    for modelo, (llamadas, entrada, leido, escrito) in sorted(por_modelo.items()):
        total_entrada += entrada
        total_leido += leido
        total_escrito += escrito
        print(
            f"  {modelo:24} {llamadas:6} llamadas   "
            f"leído de caché: {porcentaje(leido, entrada)}"
        )
        if entrada and not leido and not escrito:
            print("    ⚠ nada cacheado: el prompt no llega al mínimo del modelo")
    if total_entrada:
        print(f"  {'TOTAL':24} {porcentaje(total_leido, total_entrada)} de la entrada")

    print("\nSI EL CACHÉ ALCANZA A VIVIR")
    dentro_de_charla = [g for g in gaps if g <= CORTE_CONVERSACION]
    if not dentro_de_charla:
        print("  No hay suficientes conversaciones seguidas para medirlo.")
        await dispose_engine()
        return

    rapidos = sum(1 for g in dentro_de_charla if g <= datetime.timedelta(minutes=5))
    lentos = len(dentro_de_charla) - rapidos
    print(f"  Respuestas del cliente dentro de la misma charla: {len(dentro_de_charla)}")
    print(f"    antes de 5 min:  {rapidos:5}  ({porcentaje(rapidos, len(dentro_de_charla))})")
    print(f"    entre 5 min y 1h:{lentos:5}  ({porcentaje(lentos, len(dentro_de_charla))})")

    ttl = get_settings().anthropic_cache_ttl
    print(f"\n  ANTHROPIC_CACHE_TTL está en '{ttl}'.")
    if lentos > rapidos:
        print("  La mayoría contesta pasados los 5 minutos: con '5m' ese caché")
        print("  ya venció y se reescribe a 1.25x. Conviene poner '1h'.")
    else:
        print("  La mayoría contesta antes de 5 minutos: '5m' alcanza, y subir")
        print("  a '1h' sería pagar 2x por escribir un caché que no hace falta.")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
