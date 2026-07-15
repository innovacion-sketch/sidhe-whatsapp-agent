"""Genera slots de disponibilidad para todas las sucursales activas.

Para cada sucursal crea slots de --minutos (default 60) dentro de su horario
y días de operación, desde mañana hasta --dias adelante (default 14), con
capacidad 1. Es idempotente: salta los slots ya existentes.

Uso: uv run python scripts/seed_slots.py [--dias 14] [--minutos 60]
"""

import argparse
import asyncio
import datetime
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select

from sidhe_agent.config import get_settings
from sidhe_agent.db.models import Slot, Sucursal
from sidhe_agent.db.session import dispose_engine, get_session

DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def _horas_del_dia(
    apertura: datetime.time, cierre: datetime.time, minutos: int
) -> list[tuple[datetime.time, datetime.time]]:
    slots = []
    cursor = datetime.datetime.combine(datetime.date.min, apertura)
    fin_dia = datetime.datetime.combine(datetime.date.min, cierre)
    paso = datetime.timedelta(minutes=minutos)
    while cursor + paso <= fin_dia:
        slots.append((cursor.time(), (cursor + paso).time()))
        cursor += paso
    return slots


async def main(dias: int, minutos: int) -> None:
    hoy = datetime.datetime.now(ZoneInfo(get_settings().tz)).date()
    fechas = [hoy + datetime.timedelta(days=n) for n in range(1, dias + 1)]

    creados = 0
    async with get_session() as session:
        sucursales = (
            (await session.execute(select(Sucursal).where(Sucursal.activa.is_(True))))
            .scalars()
            .all()
        )
        for sucursal in sucursales:
            existentes = {
                (fecha, hora)
                for fecha, hora in (
                    await session.execute(
                        select(Slot.fecha, Slot.hora_inicio).where(
                            Slot.sucursal_id == sucursal.id
                        )
                    )
                ).all()
            }
            horas = _horas_del_dia(
                sucursal.horario_apertura, sucursal.horario_cierre, minutos
            )
            dias_operacion = sucursal.dias_operacion or DIAS_SEMANA
            for fecha in fechas:
                if DIAS_SEMANA[fecha.weekday()] not in dias_operacion:
                    continue
                for hora_inicio, hora_fin in horas:
                    if (fecha, hora_inicio) in existentes:
                        continue
                    session.add(
                        Slot(
                            sucursal_id=sucursal.id,
                            fecha=fecha,
                            hora_inicio=hora_inicio,
                            hora_fin=hora_fin,
                            capacidad=1,
                            reservados=0,
                        )
                    )
                    creados += 1
        await session.commit()

    print(f"Slots creados: {creados} ({len(fechas)} días, bloques de {minutos} min)")
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=14)
    parser.add_argument("--minutos", type=int, default=60)
    args = parser.parse_args()
    asyncio.run(main(args.dias, args.minutos))
