"""Cuántas citas del bot terminaron en un pedido de plantillas.

Cruza las citas agendadas por WhatsApp contra la hoja de operaciones: si
después de la cita aparece un pedido con el mismo teléfono, esa cita vendió.
El teléfono se compara por sus últimos 10 dígitos, que es como viene en la
hoja y como lo manda WhatsApp.

Lo que este número NO es: una cifra exacta de ventas. Es un piso. Tres
razones, y conviene decirlas cuando se presente el dato:

- La hoja solo trae los últimos meses; una cita más vieja que eso no tiene
  con qué cruzarse y se reporta aparte, no como "no vendió".
- Cerca del 10% de los pedidos de la hoja no traen teléfono.
- Si el pedido se registró con otro número (el del familiar que pagó, o uno
  tecleado distinto en la sucursal), el cruce no lo ve.

Solo lee. Uso:

    python scripts/conversion_citas.py            # últimos 90 días
    python scripts/conversion_citas.py --dias 30
    python scripts/conversion_citas.py --ventana 45   # días para que compre
"""

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import func, select

from sidhe_agent.db.models import Cita, Pedido, Slot, Sucursal
from sidhe_agent.db.session import dispose_engine, get_session
from sidhe_agent.services.pedidos import normalizar_telefono


def porcentaje(parte: int, total: int) -> str:
    return f"{100 * parte / total:.1f}%" if total else "—"


def vendio(
    fecha_cita: datetime.date, fecha_compra: datetime.date | None, ventana: int
) -> bool:
    """Si esa compra se puede atribuir a esa cita.

    Tiene que ser posterior o del mismo día —un pedido de antes ya existía,
    no lo trajo la cita— y dentro de la ventana, porque una compra cuatro
    meses después difícilmente sea de ese estudio.
    """
    if fecha_compra is None:
        return False
    return fecha_cita <= fecha_compra <= fecha_cita + datetime.timedelta(days=ventana)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Conversión de citas a venta")
    parser.add_argument("--dias", type=int, default=90, help="citas de los últimos N días")
    parser.add_argument(
        "--ventana", type=int, default=60, help="días tras la cita para contar la compra"
    )
    args = parser.parse_args()

    hoy = datetime.date.today()
    desde = hoy - datetime.timedelta(days=args.dias)

    async with get_session() as session:
        citas = (
            await session.execute(
                select(Cita, Slot, Sucursal)
                .join(Slot, Cita.slot_id == Slot.id)
                .join(Sucursal, Cita.sucursal_id == Sucursal.id)
                .where(Slot.fecha >= desde, Slot.fecha <= hoy)
                .order_by(Slot.fecha)
            )
        ).all()
        # Pedidos por teléfono, con su fecha más temprana
        pedidos = (
            await session.execute(
                select(Pedido.telefono, func.min(Pedido.fecha))
                .where(Pedido.telefono.is_not(None))
                .group_by(Pedido.telefono)
            )
        ).all()
        cobertura = (
            await session.execute(select(func.min(Pedido.fecha), func.max(Pedido.fecha)))
        ).one()

    compras = {tel: fecha for tel, fecha in pedidos if fecha}
    desde_hoja, hasta_hoja = cobertura

    vendidas = sin_cruce = fuera_de_hoja = canceladas = 0
    por_sucursal: dict[str, list[int]] = {}
    for cita, slot, sucursal in citas:
        if cita.estado == "cancelada":
            canceladas += 1
            continue
        if desde_hoja and slot.fecha < desde_hoja:
            fuera_de_hoja += 1
            continue
        # El contador por sucursal solo cuenta lo comparable, igual que el
        # total: si no, los porcentajes de arriba y de abajo no cuadran.
        marcador = por_sucursal.setdefault(sucursal.nombre, [0, 0])
        marcador[1] += 1
        telefono = normalizar_telefono(cita.cliente_telefono)
        if vendio(slot.fecha, compras.get(telefono) if telefono else None, args.ventana):
            vendidas += 1
            marcador[0] += 1
        else:
            sin_cruce += 1

    comparables = vendidas + sin_cruce
    print(f"Citas de los últimos {args.dias} días: {len(citas)}")
    print(f"  canceladas:            {canceladas}")
    print(f"  fuera del alcance de la hoja: {fuera_de_hoja}")
    print(f"  comparables:           {comparables}")
    print()
    print(f"CON PEDIDO POSTERIOR:    {vendidas}  ({porcentaje(vendidas, comparables)})")
    print(f"sin pedido encontrado:   {sin_cruce}")
    if desde_hoja:
        print(f"\nLa hoja cubre del {desde_hoja} al {hasta_hoja}.")
    print("Ojo: es un piso, no una cifra exacta. Ver el encabezado del script.")

    print("\nPor sucursal (con pedido / comparables):")
    for nombre, (con, total) in sorted(
        por_sucursal.items(), key=lambda x: -x[1][0]
    ):
        if total:
            print(f"  {nombre:36} {con:3}/{total:3}  {porcentaje(con, total)}")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
