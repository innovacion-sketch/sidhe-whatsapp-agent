"""Arma la pestaña "Agendar" del Excel de citas, lista para las sucursales.

Crea la pestaña si no existe (no toca las demás) y le pone:
- encabezados y la primera fila congelada;
- listas desplegables para sucursal (las activas de la base), hora y
  recordatorio, fecha con calendario y casilla para cancelar: lo que más
  hace fallar una captura a mano son los errores de dedo;
- el teléfono como texto, para que Sheets no se coma dígitos;
- aviso al editar las columnas que llena el bot, y la de control oculta.

Se puede correr las veces que haga falta: vuelve a aplicar el formato sin
borrar lo capturado. Uso, en el contenedor:

    python scripts/preparar_pestana_agendar.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select

from sidhe_agent.config import get_settings
from sidhe_agent.db.models import Sucursal
from sidhe_agent.db.session import dispose_engine, get_session
from sidhe_agent.services.agenda_excel import (
    CANCELAR,
    CONTROL,
    ENCABEZADOS,
    ESTADO,
    FECHA,
    HORA,
    RECORDATORIO,
    SUCURSAL,
    TELEFONO,
    _servicio,
)

FILAS = 1000
HORAS = [f"{h:02d}:00" for h in range(10, 21)]


def _rango(sheet_id: int, columna: int, hasta: int | None = None, fila: int = 1) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": fila,
        "endRowIndex": FILAS,
        "startColumnIndex": columna,
        "endColumnIndex": (hasta if hasta is not None else columna) + 1,
    }


def _lista(sheet_id: int, columna: int, opciones: list[str]) -> dict:
    return {
        "setDataValidation": {
            "range": _rango(sheet_id, columna),
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": o} for o in opciones],
                },
                "strict": True,
                "showCustomUi": True,
            },
        }
    }


async def _sucursales() -> list[str]:
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Sucursal.nombre).where(Sucursal.activa.is_(True)).order_by(Sucursal.nombre)
            )
        ).scalars().all()
    await dispose_engine()
    return list(filas)


def preparar(hoja: str, pestana: str, sucursales: list[str]) -> None:
    servicio = _servicio().spreadsheets()
    existentes = {
        p["properties"]["title"]: p["properties"]["sheetId"]
        for p in servicio.get(spreadsheetId=hoja).execute()["sheets"]
    }
    if pestana in existentes:
        sheet_id = existentes[pestana]
        print(f"La pestaña '{pestana}' ya existe: solo se actualiza el formato.")
    else:
        respuesta = servicio.batchUpdate(
            spreadsheetId=hoja,
            body={"requests": [{"addSheet": {"properties": {
                "title": pestana, "index": 0,
                "gridProperties": {"frozenRowCount": 1},
            }}}]},
        ).execute()
        sheet_id = respuesta["replies"][0]["addSheet"]["properties"]["sheetId"]
        print(f"Pestaña '{pestana}' creada.")

    servicio.values().update(
        spreadsheetId=hoja,
        range=f"'{pestana}'!A1:J1",
        valueInputOption="RAW",
        body={"values": [ENCABEZADOS]},
    ).execute()

    peticiones = [
        # Encabezado en negritas y congelado
        {"repeatCell": {
            "range": _rango(sheet_id, 0, CONTROL, fila=0) | {"endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }},
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }},
        _lista(sheet_id, SUCURSAL, sucursales),
        _lista(sheet_id, HORA, HORAS),
        _lista(sheet_id, RECORDATORIO, ["Sí", "No"]),
        # Fecha con calendario y en formato mexicano
        {"setDataValidation": {
            "range": _rango(sheet_id, FECHA),
            "rule": {"condition": {"type": "DATE_IS_VALID"}, "strict": True},
        }},
        {"repeatCell": {
            "range": _rango(sheet_id, FECHA),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "dd/mm/yyyy"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},
        # El teléfono como texto: si no, Sheets lo vuelve número y se come ceros
        {"repeatCell": {
            "range": _rango(sheet_id, TELEFONO),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},
        # Casilla para cancelar
        {"setDataValidation": {
            "range": _rango(sheet_id, CANCELAR),
            "rule": {"condition": {"type": "BOOLEAN"}},
        }},
        # La columna de control, oculta
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": CONTROL, "endIndex": CONTROL + 1},
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }},
        # Estado ancho, para que se lea el motivo completo
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": ESTADO, "endIndex": ESTADO + 1},
            "properties": {"pixelSize": 380},
            "fields": "pixelSize",
        }},
    ]

    # Aviso al editar lo que llena el bot. Solo aviso, no bloqueo: un bloqueo
    # mal puesto podría dejar fuera a quien sí tiene que escribir ahí.
    protegidas = servicio.get(
        spreadsheetId=hoja, fields="sheets(properties(sheetId),protectedRanges)"
    ).execute()
    ya_protegida = any(
        p.get("description") == "columnas del bot"
        for s in protegidas["sheets"]
        if s["properties"]["sheetId"] == sheet_id
        for p in s.get("protectedRanges", [])
    )
    if not ya_protegida:
        peticiones.append({"addProtectedRange": {"protectedRange": {
            "range": _rango(sheet_id, ESTADO, CONTROL, fila=0),
            "description": "columnas del bot",
            "warningOnly": True,
        }}})

    servicio.batchUpdate(spreadsheetId=hoja, body={"requests": peticiones}).execute()
    print(f"Listo: {len(sucursales)} sucursales en la lista, horas de {HORAS[0]} a {HORAS[-1]}.")


def main() -> None:
    ajustes = get_settings()
    if not ajustes.citas_sheet_id:
        raise SystemExit("Falta CITAS_SHEET_ID en el entorno.")
    if not ajustes.google_credentials:
        raise SystemExit("Faltan las credenciales de la cuenta de servicio.")
    sucursales = asyncio.run(_sucursales())
    preparar(ajustes.citas_sheet_id, ajustes.citas_sheet_pestana, sucursales)


if __name__ == "__main__":
    main()
