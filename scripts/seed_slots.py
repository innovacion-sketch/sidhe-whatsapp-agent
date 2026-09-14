"""Genera horarios de cita para todas las sucursales activas.

Normalmente no hace falta correrlo: el servicio rellena la agenda solo al
arrancar y cada pocas horas (ver services/agenda.py). Sirve para abrir la
agenda de inmediato sin esperar un deploy, o para verla crecer a mano.

Es idempotente: salta los horarios que ya existen, libres o reservados.

Uso:
    python scripts/seed_slots.py
    python scripts/seed_slots.py --dias 21 --minutos 60
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sidhe_agent.config import get_settings
from sidhe_agent.db.session import dispose_engine
from sidhe_agent.services.agenda import asegurar_slots


async def main(dias: int, minutos: int) -> None:
    creados = await asegurar_slots(dias, minutos)
    print(f"Horarios creados: {creados} (agenda abierta {dias} días, bloques de {minutos} min)")
    await dispose_engine()


if __name__ == "__main__":
    ajustes = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=ajustes.agenda_dias_adelante)
    parser.add_argument("--minutos", type=int, default=ajustes.agenda_minutos_por_cita)
    args = parser.parse_args()
    asyncio.run(main(args.dias, args.minutos))
