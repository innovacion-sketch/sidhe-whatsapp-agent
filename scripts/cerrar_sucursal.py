"""Cierra una sucursal: la saca del bot y enseña qué quedó pendiente en ella.

Marcarla como cerrada NO basta: los horarios ya generados siguen en la tabla
y, sobre todo, las citas ya agendadas siguen vivas. Si nadie las revisa, el
recordatorio de cita le va a decir a un cliente que se presente en una tienda
que ya no existe.

Por eso este script primero ENSEÑA y no toca nada:

    python scripts/cerrar_sucursal.py "Liverpool Coapa"

Y solo con --aplicar marca la sucursal como cerrada y borra los horarios
libres a futuro (los horarios con cita NO se tocan):

    python scripts/cerrar_sucursal.py "Liverpool Coapa" --aplicar

Las citas ya agendadas se cancelan aparte y a propósito, porque hay que
avisarle a cada cliente:

    python scripts/cerrar_sucursal.py "Liverpool Coapa" --aplicar --cancelar-citas
"""

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import delete, select

from sidhe_agent.db.models import Cita, Slot, Sucursal
from sidhe_agent.db.session import dispose_engine, get_session
from sidhe_agent.services.google_calendar import borrar_evento
from sidhe_agent.tools.citas import fecha_legible


async def _buscar(session, nombre: str) -> Sucursal | None:
    return (
        await session.execute(
            select(Sucursal).where(Sucursal.nombre.ilike(f"%{nombre.strip()}%"))
        )
    ).scalars().first()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Cierra una sucursal")
    parser.add_argument("nombre", help='Nombre o parte del nombre ("Coapa")')
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Marca la sucursal cerrada y borra sus horarios libres a futuro",
    )
    parser.add_argument(
        "--cancelar-citas",
        action="store_true",
        help="Ademas cancela las citas futuras (avisale tu a cada cliente)",
    )
    args = parser.parse_args()

    hoy = datetime.date.today()
    async with get_session() as session:
        sucursal = await _buscar(session, args.nombre)
        if sucursal is None:
            print(f"No encontre ninguna sucursal que se parezca a '{args.nombre}'")
            await dispose_engine()
            return

        print(f"Sucursal: {sucursal.nombre} (id {sucursal.id})")
        print(f"Estado actual: {'ABIERTA' if sucursal.activa else 'CERRADA'}")
        print(f"Telefono: {sucursal.telefono or '(sin telefono)'}")

        citas = (
            await session.execute(
                select(Cita, Slot)
                .join(Slot, Cita.slot_id == Slot.id)
                .where(
                    Cita.sucursal_id == sucursal.id,
                    Cita.estado == "confirmada",
                    Slot.fecha >= hoy,
                )
                .order_by(Slot.fecha, Slot.hora_inicio)
            )
        ).all()
        # Un horario libre puede seguir teniendo una cita CANCELADA colgando:
        # esos no se pueden borrar (la base no lo permite) y tampoco hace
        # falta, porque una sucursal cerrada ya no ofrece horarios.
        sin_historial = ~(
            select(Cita.id).where(Cita.slot_id == Slot.id).exists()
        )
        condicion_borrables = (
            Slot.sucursal_id == sucursal.id,
            Slot.fecha >= hoy,
            Slot.reservados == 0,
            sin_historial,
        )
        libres = (
            await session.execute(select(Slot).where(*condicion_borrables))
        ).scalars().all()

        print(f"\nCitas futuras confirmadas: {len(citas)}")
        for cita, slot in citas:
            print(
                f"  - {fecha_legible(slot.fecha)} {slot.hora_inicio.strftime('%H:%M')}"
                f" · {cita.cliente_nombre} · {cita.cliente_telefono} (folio {cita.id})"
            )
        print(f"Horarios libres a futuro que se pueden borrar: {len(libres)}")

        if not args.aplicar:
            if not sucursal.activa and not citas and not libres:
                print("\nYa esta cerrada y no quedo nada pendiente. Nada que hacer.")
            else:
                print("\nNo toque nada. Corre otra vez con --aplicar para cerrarla.")
                if citas:
                    print("Esas citas hay que reubicarlas o avisarles ANTES de cerrar.")
            await dispose_engine()
            return

        sucursal.activa = False
        canceladas = 0
        if args.cancelar_citas:
            for cita, slot in citas:
                cita.estado = "cancelada"
                # Igual que al cancelar desde el bot: el lugar se libera
                slot.reservados = max(0, slot.reservados - 1)
                await borrar_evento(sucursal.calendar_id, cita.google_event_id)
                canceladas += 1
        borrados = (
            await session.execute(delete(Slot).where(*condicion_borrables))
        ).rowcount
        await session.commit()

    print(f"\nListo. {sucursal.nombre} quedo marcada como CERRADA.")
    print(f"Horarios libres borrados: {borrados}")
    print(f"Citas canceladas: {canceladas}")
    if citas and not args.cancelar_citas:
        print(
            f"OJO: quedan {len(citas)} citas confirmadas. El recordatorio las va a"
            " mandar igual. Reubicalas o corre con --cancelar-citas."
        )
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
