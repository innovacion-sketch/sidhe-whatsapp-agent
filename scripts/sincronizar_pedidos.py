"""Sincroniza la tabla `pedidos` con el Google Sheet de operaciones.

Lee la hoja en vivo con la cuenta de servicio (la misma de Calendar): no hay
archivo que descargar ni subir. Requiere GOOGLE_CREDENTIALS_JSON y
GOOGLE_SHEETS_PEDIDOS_ID, y que el Sheet esté compartido con el correo de
esa cuenta (basta permiso de lectura).

Uso:
    uv run python scripts/sincronizar_pedidos.py
    uv run python scripts/sincronizar_pedidos.py --meses 6

En producción no hace falta correrlo a mano: n8n puede pegarle cada pocas
horas a POST /internal/pedidos/sincronizar.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sidhe_agent.config import get_settings
from sidhe_agent.db.session import dispose_engine
from sidhe_agent.services import google_sheets


async def main(meses: int | None) -> None:
    ajustes = get_settings()
    if not ajustes.google_credentials:
        raise SystemExit("Falta GOOGLE_CREDENTIALS_JSON en el entorno")
    if not ajustes.google_sheets_pedidos_id:
        raise SystemExit("Falta GOOGLE_SHEETS_PEDIDOS_ID en el entorno")

    try:
        datos = await google_sheets.sincronizar(meses)
    except google_sheets.ErrorSincronizacion as exc:
        raise SystemExit(f"No se sincronizó: {exc}") from exc

    print(f"Filas en la hoja: {datos['filas_en_hoja']}")
    print(f"Pedidos guardados (últimos {datos['meses']} meses): {datos['pedidos']}")
    print(f"Rango de fechas: {datos['desde']} a {datos['hasta']}")
    print(f"Con teléfono: {datos['con_telefono']}")
    print("Por categoría:")
    for categoria, n in sorted(datos["por_categoria"].items(), key=lambda x: -x[1]):
        print(f"  {n:7}  {categoria}")
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--meses",
        type=int,
        default=None,
        help="meses de historial (default: PEDIDOS_MESES_HISTORIAL)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.meses))
