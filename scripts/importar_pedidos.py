"""Importa la hoja STATUS del Excel de operaciones a la tabla `pedidos`.

Reemplaza la tabla completa: el Excel es la fuente de verdad y esta copia
solo sirve para que el bot conteste al instante.

Uso:
    uv run python scripts/importar_pedidos.py "PACIENTES SUPERVISION.xlsx"
    uv run python scripts/importar_pedidos.py archivo.xlsx --hoja STATUS
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sidhe_agent.db.session import dispose_engine
from sidhe_agent.services import pedidos

COLUMNAS_ESPERADAS = ["FECHA", "NOMBRE", "SUCURSAL"]


def leer_hoja(ruta: Path, hoja: str) -> list[tuple]:
    import openpyxl

    libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    if hoja not in libro.sheetnames:
        raise SystemExit(
            f"La hoja '{hoja}' no existe. Hojas disponibles: {libro.sheetnames[:12]}"
        )
    filas = list(libro[hoja].iter_rows(values_only=True))
    libro.close()
    if not filas:
        raise SystemExit("La hoja está vacía")

    encabezado = [pedidos.normalizar_texto(c or "") for c in filas[0]]
    faltantes = [c for c in COLUMNAS_ESPERADAS if c not in encabezado]
    if faltantes:
        raise SystemExit(
            f"El encabezado no tiene {faltantes}. Encontrado: {encabezado[:9]}"
        )
    return filas[1:]


async def main(ruta: str, hoja: str) -> None:
    archivo = Path(ruta)
    if not archivo.is_file():
        raise SystemExit(f"No existe el archivo: {archivo}")

    crudas = leer_hoja(archivo, hoja)
    registros = pedidos.preparar_filas(crudas)
    if not registros:
        raise SystemExit("No se encontró ninguna fila con nombre")

    total = await pedidos.reemplazar(registros)

    con_telefono = sum(1 for r in registros if r["telefono"])
    categorias = Counter(r["status"] for r in registros)
    print(f"Pedidos importados: {total}")
    print(f"Con teléfono (búsqueda automática): {con_telefono} "
          f"({100 * con_telefono // total}%)")
    print("Por categoría:")
    for categoria, n in categorias.most_common():
        print(f"  {n:7}  {categoria}")

    revision = [r for r in registros if r["status"] == pedidos.REVISION]
    if revision:
        muestras = Counter(r["status_original"] for r in revision).most_common(5)
        print("\nStatus que el bot mandará a un asesor (los más comunes):")
        for texto, n in muestras:
            print(f"  {n:7}  {texto!r}")

    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archivo", help="ruta del .xlsx de operaciones")
    parser.add_argument("--hoja", default="STATUS")
    args = parser.parse_args()
    asyncio.run(main(args.archivo, args.hoja))
