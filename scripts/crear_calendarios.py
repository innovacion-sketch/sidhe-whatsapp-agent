"""Crea de golpe el calendario de cada sucursal y se lo comparte al stand.

Evita tener que entrar a Google Calendar sucursal por sucursal: la cuenta de
servicio crea un calendario por sucursal, guarda su id en la base y le da
acceso de escritura al correo del stand (y de propietario a una cuenta de la
empresa, si se indica con --admin).

El correo de cada sucursal se lee de la columna `correo_sucursal` de
data/sucursales.csv. Las sucursales sin correo igual se crean y se conectan
al bot: solo queda pendiente compartirlas.

Es idempotente: una sucursal que ya tiene calendar_id se omite, así que si
Google corta por límite de uso, basta volver a correrlo.

Uso:
    python scripts/crear_calendarios.py --dry-run
    python scripts/crear_calendarios.py
    python scripts/crear_calendarios.py --solo "Polanco"
    python scripts/crear_calendarios.py --admin citas@sidhegroup.com

OJO: Google le manda a cada sucursal una invitación por correo y alguien de
esa cuenta tiene que hacer clic en "Añadir este calendario" una vez. No hay
forma de saltarse ese clic. Si no les llega, pueden añadirlo a mano con el id
que imprime este script (Calendar → Otros calendarios → Suscribirse → pegar).
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select

from sidhe_agent.config import get_settings
from sidhe_agent.db.models import Sucursal
from sidhe_agent.db.session import dispose_engine, get_session
from sidhe_agent.services import google_calendar

RUTA_CSV = Path(__file__).parent.parent / "data" / "sucursales.csv"
PAUSA_SEGUNDOS = 1.5  # Google corta si se crean calendarios muy seguido


def correos_por_sucursal() -> dict[str, str]:
    """Mapa nombre de sucursal → correo del stand, desde el CSV."""
    if not RUTA_CSV.exists():
        return {}
    with RUTA_CSV.open(encoding="utf-8") as archivo:
        return {
            fila["nombre"].strip(): fila.get("correo_sucursal", "").strip()
            for fila in csv.DictReader(archivo)
            if fila.get("nombre", "").strip()
        }


def nombre_calendario(sucursal: str) -> str:
    """'Liverpool Polanco' → 'Citas Sidhe - Polanco'."""
    corto = sucursal.replace("Liverpool", "").strip() or sucursal
    return f"Citas Sidhe - {corto}"


async def pendientes(filtro: str | None) -> list[Sucursal]:
    async with get_session() as session:
        sucursales = list(
            (
                await session.execute(
                    select(Sucursal)
                    .where(Sucursal.activa.is_(True))
                    .order_by(Sucursal.nombre)
                )
            ).scalars()
        )
    sin_calendario = [s for s in sucursales if not s.calendar_id]
    if filtro:
        sin_calendario = [
            s for s in sin_calendario if filtro.lower() in s.nombre.lower()
        ]
    return sin_calendario


async def guardar_calendar_id(sucursal_id: int, calendar_id: str) -> None:
    async with get_session() as session:
        sucursal = await session.get(Sucursal, sucursal_id)
        sucursal.calendar_id = calendar_id
        await session.commit()


async def main(filtro: str | None, admin: str | None, simulacion: bool) -> None:
    if not get_settings().google_credentials:
        raise SystemExit("Falta GOOGLE_CREDENTIALS_JSON en el entorno")

    correos = correos_por_sucursal()
    faltantes = await pendientes(filtro)
    if not faltantes:
        print("Todas las sucursales activas ya tienen calendario.")
        await dispose_engine()
        return

    sin_correo = [s.nombre for s in faltantes if not correos.get(s.nombre)]
    print(f"Sucursales por conectar: {len(faltantes)}")
    if sin_correo:
        print(f"  Sin correo en el CSV ({len(sin_correo)}): se crea el "
              "calendario pero no se comparte con nadie todavía.")
        for nombre in sin_correo:
            print(f"     - {nombre}")

    if simulacion:
        print("\n--dry-run: esto es lo que haría\n")
        for s in faltantes:
            destino = correos.get(s.nombre) or "(nadie todavía)"
            print(f"  {nombre_calendario(s.nombre):38}  →  {destino}")
        await dispose_engine()
        return

    creados, fallidos = [], []
    for sucursal in faltantes:
        etiqueta = nombre_calendario(sucursal.nombre)
        try:
            calendar_id = await google_calendar.crear_calendario(etiqueta)
        except Exception as exc:
            print(f"ERROR  {sucursal.nombre}: no se creó el calendario ({exc})")
            fallidos.append(sucursal.nombre)
            if "usageLimits" in str(exc) or "rateLimit" in str(exc):
                print("\nGoogle cortó por límite de uso. Espera unos minutos y "
                      "vuelve a correr el script: retoma donde se quedó.")
                break
            continue

        await guardar_calendar_id(sucursal.id, calendar_id)

        correo = correos.get(sucursal.nombre)
        compartido = "sin compartir"
        if correo:
            try:
                await google_calendar.compartir_calendario(
                    calendar_id, correo, "writer"
                )
                compartido = f"compartido con {correo}"
            except Exception as exc:
                compartido = f"NO se compartió con {correo}: {exc}"
        if admin:
            try:
                await google_calendar.compartir_calendario(
                    calendar_id, admin, "owner"
                )
            except Exception as exc:
                print(f"       aviso: no se pudo dar propiedad a {admin}: {exc}")

        print(f"OK     {sucursal.nombre}  ·  {compartido}")
        creados.append((sucursal.nombre, calendar_id))
        await asyncio.sleep(PAUSA_SEGUNDOS)

    print(f"\nCalendarios creados: {len(creados)}")
    if fallidos:
        print(f"Fallaron: {', '.join(fallidos)}")

    if creados:
        print("\nAgrega esto a la columna calendar_id de data/sucursales.csv "
              "para que quede en el repositorio:")
        for nombre, calendar_id in creados:
            print(f"  {nombre},{calendar_id}")
        print("\nCada sucursal recibirá un correo para añadir su calendario. "
              "Si no les llega, que lo agreguen a mano: Google Calendar → "
              "Otros calendarios → Suscribirse a un calendario → pegar el id.")

    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--solo", default=None, help="parte del nombre de una sucursal")
    parser.add_argument(
        "--admin",
        default=None,
        help="correo de la empresa que quedará como propietario de cada calendario",
    )
    parser.add_argument("--dry-run", action="store_true", dest="simulacion")
    args = parser.parse_args()
    asyncio.run(main(args.solo, args.admin, args.simulacion))
