"""Sincronización del estado de pedidos desde el Google Sheet de operaciones.

Usa la MISMA cuenta de servicio que Google Calendar: basta con compartir el
Sheet (solo lectura) con ese correo y poner su id en GOOGLE_SHEETS_PEDIDOS_ID.
No hay archivo que subir ni importación manual.

La hoja es la fuente de verdad y la tabla `pedidos` solo una copia: cada
sincronización la reemplaza completa. Si Google falla, la copia anterior se
queda intacta y el bot sigue contestando con ella.
"""

import asyncio
import json
from typing import Any

import structlog

from ..config import get_settings
from . import pedidos

logger = structlog.get_logger(__name__)

ALCANCES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Columnas A-I de la hoja: Fecha … Localizacion Final
COLUMNAS = "A:I"


class ErrorSincronizacion(RuntimeError):
    """Falla esperable (credenciales, permisos, hoja mal nombrada)."""


def sincronizacion_activa() -> bool:
    ajustes = get_settings()
    return bool(ajustes.google_credentials and ajustes.google_sheets_pedidos_id)


def _servicio() -> Any:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(get_settings().google_credentials)
    credenciales = service_account.Credentials.from_service_account_info(
        info, scopes=ALCANCES
    )
    return build("sheets", "v4", credentials=credenciales, cache_discovery=False)


def _leer_sync(spreadsheet_id: str, rango: str) -> list[list]:
    """Valores crudos de la hoja.

    UNFORMATTED_VALUE + SERIAL_NUMBER devuelve las fechas como número de
    serie en vez de como texto localizado: se convierten igual en cualquier
    idioma de la hoja (`_fecha_valida` lo resuelve).
    """
    respuesta = (
        _servicio()
        .spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=rango,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="SERIAL_NUMBER",
        )
        .execute()
    )
    return respuesta.get("values", [])


async def descargar_status() -> list[list]:
    """Descarga la hoja STATUS completa (encabezado incluido)."""
    ajustes = get_settings()
    if not sincronizacion_activa():
        raise ErrorSincronizacion(
            "Falta GOOGLE_CREDENTIALS_JSON o GOOGLE_SHEETS_PEDIDOS_ID"
        )
    rango = f"{ajustes.google_sheets_pedidos_hoja}!{COLUMNAS}"
    try:
        return await asyncio.to_thread(
            _leer_sync, ajustes.google_sheets_pedidos_id, rango
        )
    except Exception as exc:  # googleapiclient lanza HttpError y derivados
        raise ErrorSincronizacion(f"No se pudo leer el Sheet ({rango}): {exc}") from exc


async def sincronizar(meses: int | None = None) -> dict:
    """Descarga la hoja, la normaliza y reemplaza la tabla `pedidos`.

    Devuelve el resumen de lo importado. Lanza ErrorSincronizacion si la hoja
    no se puede leer o no tiene la forma esperada; en ese caso NO se toca la
    copia que ya estaba en la base.
    """
    filas = await descargar_status()
    if len(filas) < 2:
        raise ErrorSincronizacion("La hoja no tiene filas de datos")

    faltantes = pedidos.columnas_faltantes(filas[0])
    if faltantes:
        raise ErrorSincronizacion(
            f"El encabezado de la hoja no tiene {faltantes}; "
            "revisa GOOGLE_SHEETS_PEDIDOS_HOJA"
        )

    if meses is None:
        meses = get_settings().pedidos_meses_historial
    registros = pedidos.preparar_filas(filas[1:], meses=meses)
    if not registros:
        raise ErrorSincronizacion(
            f"Ninguna fila quedó dentro de los últimos {meses} meses"
        )

    await pedidos.reemplazar(registros)
    resumen = pedidos.resumen(registros)
    resumen["filas_en_hoja"] = len(filas) - 1
    resumen["meses"] = meses
    logger.info("pedidos_sincronizados", **resumen)
    return resumen
