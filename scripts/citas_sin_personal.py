"""Revisa qué citas futuras podrían quedarse sin quién las atienda.

Responde con más detalle que la alerta por correo, que solo mira los días
cerrados por completo. Aquí cada cita cae en una de estas categorías:

  CERRADO    el rol no tiene a nadie ese día en esa sucursal
  DESCANSO   es un día de descanso fijo de la sucursal
  FUERA      hay gente ese día, pero nadie a la hora de la cita
  SIN ROL    esa semana todavía no se carga: no se sabe
  OK         hay alguien programado que cubre esa hora

Solo lee. Uso:

    python scripts/citas_sin_personal.py           # próximos 21 días
    python scripts/citas_sin_personal.py --dias 60
"""

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select

from sidhe_agent.db.models import Cita, Slot, Sucursal
from sidhe_agent.db.session import dispose_engine, get_session
from sidhe_agent.services import asistencias
from sidhe_agent.services.alertas_citas import ESTADOS, clasificar


async def main() -> None:
    parser = argparse.ArgumentParser(description="Citas que podrían quedarse solas")
    parser.add_argument("--dias", type=int, default=21)
    parser.add_argument("--todas", action="store_true", help="incluir las que estan OK")
    args = parser.parse_args()

    hoy = datetime.date.today()
    hasta = hoy + datetime.timedelta(days=args.dias)
    sin_personal = await asistencias.dias_sin_personal(hoy, hasta)
    turnos = await asistencias.turnos(hoy, hasta)
    if turnos is None:
        print("OJO: no se pudo leer el rol de personal; todo sale como SIN ROL\n")

    async with get_session() as session:
        filas = (
            await session.execute(
                select(Cita, Slot, Sucursal)
                .join(Slot, Cita.slot_id == Slot.id)
                .join(Sucursal, Cita.sucursal_id == Sucursal.id)
                .where(
                    Cita.estado == "confirmada",
                    Slot.fecha >= hoy,
                    Slot.fecha <= hasta,
                )
                .order_by(Slot.fecha, Slot.hora_inicio)
            )
        ).all()

    conteo: dict[str, int] = {}
    for cita, slot, sucursal in filas:
        estado = clasificar(
            sucursal.nombre,
            slot.fecha,
            slot.hora_inicio,
            sucursal.dias_operacion,
            sin_personal,
            turnos,
        )
        conteo[estado] = conteo.get(estado, 0) + 1
        if estado == "OK" and not args.todas:
            continue
        print(
            f"  {estado:9} {slot.fecha} {slot.hora_inicio.strftime('%H:%M')}  "
            f"{sucursal.nombre:34} {cita.cliente_nombre}  "
            f"{cita.cliente_telefono}  (folio {cita.id})"
        )

    print(f"\nCitas revisadas: {len(filas)} en los proximos {args.dias} dias")
    for estado in ESTADOS:
        if conteo.get(estado):
            print(f"  {estado:9} {conteo[estado]}")
    if conteo.get("SIN ROL"):
        print(
            "\nSIN ROL no significa que no haya personal: significa que esa "
            "semana todavia no se carga en el sistema de asistencias."
        )

    await asistencias.cerrar_motor()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
