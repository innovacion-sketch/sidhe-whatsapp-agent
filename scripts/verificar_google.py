"""Diagnóstico de la integración con Google (Calendar y Sheets).

Responde de un vistazo las tres preguntas que siempre trae el setup:
  1. ¿Las credenciales cargan y con qué correo de cuenta de servicio?
  2. ¿Qué sucursales tienen calendario conectado y cuáles faltan?
  3. ¿Se puede leer el Sheet de pedidos?

Uso:
    uv run python scripts/verificar_google.py
    uv run python scripts/verificar_google.py --probar "Liverpool Polanco"

Con --probar crea un evento de prueba en el calendario de esa sucursal y lo
borra enseguida: es la única forma de confirmar que el calendario está
compartido con permiso de ESCRITURA y no solo de lectura.
"""

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select

from sidhe_agent.config import get_settings
from sidhe_agent.db.models import Sucursal
from sidhe_agent.db.session import dispose_engine, get_session
from sidhe_agent.services import google_calendar, google_sheets, pedidos

OK = "OK  "
FALTA = "FALTA"
ERROR = "ERROR"


def revisar_credenciales() -> str | None:
    """Imprime el estado de las credenciales y devuelve el correo del bot."""
    crudo = get_settings().google_credentials
    if not crudo:
        print(f"{FALTA} GOOGLE_CREDENTIALS_JSON vacío: no hay sincronización")
        return None
    try:
        info = json.loads(crudo)
    except json.JSONDecodeError as exc:
        print(f"{ERROR} GOOGLE_CREDENTIALS_JSON no es JSON válido: {exc}")
        print("      Si lo pegaste en base64, revisa que no le falten caracteres.")
        return None

    correo = info.get("client_email")
    print(f"{OK} Credenciales cargadas. Proyecto: {info.get('project_id')}")
    print(f"     Cuenta de servicio: {correo}")
    print("     Ese es el correo con el que hay que COMPARTIR calendarios y Sheets.")
    return correo


async def revisar_sucursales() -> list[Sucursal]:
    async with get_session() as session:
        sucursales = list(
            (
                await session.execute(
                    select(Sucursal).where(Sucursal.activa.is_(True)).order_by(Sucursal.nombre)
                )
            ).scalars()
        )

    con_calendario = [s for s in sucursales if s.calendar_id]
    sin_calendario = [s for s in sucursales if not s.calendar_id]
    sin_telefono = [s for s in sucursales if not s.telefono]

    print(f"\nSucursales activas: {len(sucursales)}")
    print(f"  con calendario conectado: {len(con_calendario)}")
    for s in con_calendario:
        print(f"     - {s.nombre}: {s.calendar_id}")
    if sin_calendario:
        print(f"  SIN calendario ({len(sin_calendario)}): completa la columna "
              "calendar_id de data/sucursales.csv y corre seed_sucursales.py")
        for s in sin_calendario:
            print(f"     - {s.nombre}")
    if sin_telefono:
        print(f"  SIN teléfono ({len(sin_telefono)}): el bot no puede pasar su "
              "número cuando un cliente necesita atención directa")
        for s in sin_telefono:
            print(f"     - {s.nombre}")
    return sucursales


async def probar_calendario(sucursales: list[Sucursal], nombre: str) -> None:
    elegidas = [s for s in sucursales if nombre.lower() in s.nombre.lower()]
    if not elegidas:
        print(f"\n{ERROR} Ninguna sucursal activa se parece a {nombre!r}")
        return
    sucursal = elegidas[0]
    if not sucursal.calendar_id:
        print(f"\n{FALTA} {sucursal.nombre} no tiene calendar_id configurado")
        return

    manana = datetime.date.today() + datetime.timedelta(days=1)
    print(f"\nProbando escritura en el calendario de {sucursal.nombre}…")
    event_id = await google_calendar.crear_evento(
        sucursal.calendar_id,
        nombre_cliente="PRUEBA - borrar",
        telefono="0000000000",
        folio=0,
        fecha=manana,
        hora_inicio=datetime.time(8, 0),
        hora_fin=datetime.time(8, 30),
        direccion=sucursal.direccion or "",
    )
    if not event_id:
        print(f"{ERROR} No se creó el evento. Causas típicas: el calendario no "
              "está compartido con la cuenta de servicio, o se compartió solo "
              "con permiso de lectura (hace falta 'Hacer cambios en eventos').")
        return
    print(f"{OK} Evento creado ({event_id}); borrándolo…")
    if await google_calendar.borrar_evento(sucursal.calendar_id, event_id):
        print(f"{OK} Borrado. El calendario de esta sucursal quedó listo.")
    else:
        print(f"{ERROR} No se pudo borrar; búscalo el {manana} a las 08:00 y "
              "quítalo a mano.")


async def revisar_sheet() -> None:
    ajustes = get_settings()
    print("\nGoogle Sheet de pedidos:")
    if not ajustes.google_sheets_pedidos_id:
        print(f"{FALTA} GOOGLE_SHEETS_PEDIDOS_ID vacío: el estado de pedidos "
              "solo se actualiza importando el .xlsx a mano")
        return
    try:
        filas = await google_sheets.descargar_status()
    except google_sheets.ErrorSincronizacion as exc:
        print(f"{ERROR} {exc}")
        print("      Comparte el Sheet con la cuenta de servicio (lectura basta).")
        return

    faltantes = pedidos.columnas_faltantes(list(filas[0])) if filas else ["todas"]
    print(f"{OK} Hoja '{ajustes.google_sheets_pedidos_hoja}' leída: "
          f"{max(len(filas) - 1, 0)} filas de datos")
    if faltantes:
        print(f"{ERROR} Al encabezado le faltan columnas: {faltantes}")
    else:
        print(f"{OK} Encabezado correcto. Ya puedes correr sincronizar_pedidos.py")


async def main(probar: str | None) -> None:
    print("=== Integración con Google ===")
    if revisar_credenciales():
        sucursales = await revisar_sucursales()
        if probar:
            await probar_calendario(sucursales, probar)
        await revisar_sheet()
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--probar",
        default=None,
        help="nombre (o parte) de una sucursal para probar su calendario",
    )
    args = parser.parse_args()
    asyncio.run(main(args.probar))
