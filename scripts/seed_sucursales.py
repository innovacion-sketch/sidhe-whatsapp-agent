"""Seed de sucursales.

Si existe data/sucursales.csv (columnas: nombre, alias, ciudad, estado, zona,
direccion, horario_apertura, horario_cierre, dias_operacion, telefono) carga
las ~30 reales; si no, inserta 5 sucursales de prueba en CDMX/GDL/MTY.

Uso: uv run python scripts/seed_sucursales.py
"""

import asyncio
import csv
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select

from sidhe_agent.db.models import Sucursal
from sidhe_agent.db.session import dispose_engine, get_session

RUTA_CSV = Path(__file__).parent.parent / "data" / "sucursales.csv"

DIAS_LV_S = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"]
DIAS_TODOS = DIAS_LV_S + ["domingo"]

SUCURSALES_PRUEBA = [
    {
        "nombre": "Liverpool Perisur",
        "alias": ["perisur", "peri sur"],
        "ciudad": "Ciudad de México",
        "estado": "Ciudad de México",
        "zona": "CDMX Sur",
        "direccion": "Liverpool Perisur, Anillo Periférico 4690, Coyoacán, CDMX",
        "horario_apertura": datetime.time(11, 0),
        "horario_cierre": datetime.time(20, 0),
        "dias_operacion": DIAS_TODOS,
        "telefono": None,
        "calendar_id": None,
    },
    {
        "nombre": "Liverpool Polanco",
        "alias": ["polanco"],
        "ciudad": "Ciudad de México",
        "estado": "Ciudad de México",
        "zona": "CDMX Poniente",
        "direccion": "Liverpool Polanco, Mariano Escobedo 425, Miguel Hidalgo, CDMX",
        "horario_apertura": datetime.time(11, 0),
        "horario_cierre": datetime.time(20, 0),
        "dias_operacion": DIAS_TODOS,
        "telefono": None,
        "calendar_id": None,
    },
    {
        "nombre": "Liverpool Satélite",
        "alias": ["satelite", "plaza satelite"],
        "ciudad": "Naucalpan",
        "estado": "Estado de México",
        "zona": "CDMX Norte",
        "direccion": "Liverpool Plaza Satélite, Circuito Centro Comercial 2251, Naucalpan",
        "horario_apertura": datetime.time(11, 0),
        "horario_cierre": datetime.time(20, 0),
        "dias_operacion": DIAS_TODOS,
        "telefono": None,
        "calendar_id": None,
    },
    {
        "nombre": "Liverpool Galerías Guadalajara",
        "alias": ["galerias guadalajara", "galerias gdl", "guadalajara"],
        "ciudad": "Zapopan",
        "estado": "Jalisco",
        "zona": "Guadalajara",
        "direccion": "Liverpool Galerías, Av. Rafael Sanzio 150, Zapopan, Jalisco",
        "horario_apertura": datetime.time(11, 0),
        "horario_cierre": datetime.time(20, 0),
        "dias_operacion": DIAS_TODOS,
        "telefono": None,
        "calendar_id": None,
    },
    {
        "nombre": "Liverpool Valle Oriente",
        "alias": ["valle oriente", "monterrey"],
        "ciudad": "San Pedro Garza García",
        "estado": "Nuevo León",
        "zona": "Monterrey",
        "direccion": "Liverpool Valle Oriente, Av. Lázaro Cárdenas 1000, San Pedro Garza García",
        "horario_apertura": datetime.time(11, 0),
        "horario_cierre": datetime.time(20, 0),
        "dias_operacion": DIAS_TODOS,
        "telefono": None,
        "calendar_id": None,
    },
]


CAMPOS_OBLIGATORIOS = ("nombre", "ciudad", "estado", "zona", "direccion")


def _desde_csv() -> tuple[list[dict], list[str]]:
    """(filas válidas, nombres de filas omitidas por datos faltantes)."""
    filas: list[dict] = []
    omitidas: list[str] = []
    with RUTA_CSV.open(encoding="utf-8") as archivo:
        for fila in csv.DictReader(archivo):
            nombre = fila.get("nombre", "").strip()
            faltantes = [
                campo for campo in CAMPOS_OBLIGATORIOS if not fila.get(campo, "").strip()
            ]
            if not fila.get("horario_apertura", "").strip():
                faltantes.append("horario_apertura")
            if not fila.get("horario_cierre", "").strip():
                faltantes.append("horario_cierre")
            if faltantes:
                omitidas.append(f"{nombre or '(sin nombre)'} — falta: {', '.join(faltantes)}")
                continue
            filas.append(
                {
                    "nombre": nombre,
                    "alias": [a.strip() for a in fila.get("alias", "").split("|") if a.strip()],
                    "ciudad": fila["ciudad"].strip(),
                    "estado": fila["estado"].strip(),
                    "zona": fila["zona"].strip(),
                    "direccion": fila["direccion"].strip(),
                    "horario_apertura": datetime.time.fromisoformat(fila["horario_apertura"].strip()),
                    "horario_cierre": datetime.time.fromisoformat(fila["horario_cierre"].strip()),
                    "dias_operacion": [
                        d.strip() for d in fila.get("dias_operacion", "").split("|") if d.strip()
                    ]
                    or DIAS_TODOS,
                    "telefono": fila.get("telefono", "").strip() or None,
                    "calendar_id": fila.get("calendar_id", "").strip() or None,
                }
            )
    return filas, omitidas


async def main() -> None:
    if RUTA_CSV.exists():
        datos, omitidas = _desde_csv()
        origen = "data/sucursales.csv"
    else:
        datos, omitidas = SUCURSALES_PRUEBA, []
        origen = "datos de prueba"

    insertadas = actualizadas = 0
    async with get_session() as session:
        for registro in datos:
            existente = (
                await session.execute(
                    select(Sucursal).where(Sucursal.nombre == registro["nombre"])
                )
            ).scalar_one_or_none()
            if existente:
                # Upsert: re-ejecutar el seed aplica correcciones del CSV
                for campo, valor in registro.items():
                    setattr(existente, campo, valor)
                existente.activa = True
                actualizadas += 1
            else:
                session.add(Sucursal(**registro, activa=True))
                insertadas += 1
        await session.commit()

    print(f"Origen: {origen}")
    print(f"Sucursales insertadas: {insertadas}, actualizadas: {actualizadas}")
    if omitidas:
        print(f"\nOMITIDAS ({len(omitidas)}) — completa el CSV y re-ejecuta:")
        for linea in omitidas:
            print(f"  - {linea}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
