"""Tools de sucursales: búsqueda por texto libre y listado de zonas.

El matching usa ILIKE + unaccent (extensión creada en la migración inicial)
sobre nombre, alias, ciudad y zona, para tolerar acentos y variantes.
"""

import datetime

from langchain_core.tools import tool
from sqlalchemy import Text, cast, func, or_, select

from ..db.models import Sucursal
from ..db.session import get_session

MAX_RESULTADOS = 10


def _horario(apertura: datetime.time, cierre: datetime.time) -> str:
    return f"{apertura.strftime('%H:%M')} a {cierre.strftime('%H:%M')}"


def _a_dict(sucursal: Sucursal) -> dict:
    return {
        "id": sucursal.id,
        "nombre": sucursal.nombre,
        "ciudad": sucursal.ciudad,
        "zona": sucursal.zona,
        "direccion": sucursal.direccion,
        "horario": _horario(sucursal.horario_apertura, sucursal.horario_cierre),
    }


async def _buscar_sucursal(texto_ubicacion: str) -> list[dict]:
    patron = f"%{texto_ubicacion.strip().lower()}%"
    condicion = or_(
        func.unaccent(func.lower(Sucursal.nombre)).like(func.unaccent(patron)),
        func.unaccent(func.lower(Sucursal.ciudad)).like(func.unaccent(patron)),
        func.unaccent(func.lower(Sucursal.zona)).like(func.unaccent(patron)),
        # alias es JSONB (lista de variantes); casteado a texto para el LIKE
        func.unaccent(func.lower(cast(Sucursal.alias, Text))).like(func.unaccent(patron)),
    )
    async with get_session() as session:
        resultado = await session.execute(
            select(Sucursal)
            .where(Sucursal.activa.is_(True), condicion)
            .order_by(Sucursal.nombre)
            .limit(MAX_RESULTADOS)
        )
        return [_a_dict(s) for s in resultado.scalars()]


async def _listar_zonas() -> list[dict]:
    async with get_session() as session:
        resultado = await session.execute(
            select(Sucursal.zona, func.count(Sucursal.id))
            .where(Sucursal.activa.is_(True))
            .group_by(Sucursal.zona)
            .order_by(Sucursal.zona)
        )
        return [
            {"zona": zona, "sucursales": conteo} for zona, conteo in resultado.all()
        ]


@tool
async def buscar_sucursal(texto_ubicacion: str) -> list[dict]:
    """Busca sucursales de Sidhe Group por ubicación mencionada por el cliente.

    Hace matching tolerante a acentos contra nombre, alias, ciudad y zona
    (ej. "perisur", "satelite", "guadalajara"). Devuelve máximo 10 sucursales
    con id, nombre, ciudad, zona, dirección y horario. Si devuelve lista
    vacía, dile al cliente que no encontraste sucursal en esa ubicación y
    ofrécele ver las zonas disponibles (tool listar_zonas).

    Args:
        texto_ubicacion: ciudad, zona, plaza o nombre que mencionó el cliente.
    """
    return await _buscar_sucursal(texto_ubicacion)


@tool
async def listar_zonas() -> list[dict]:
    """Lista las zonas donde Sidhe Group tiene sucursales, con su conteo.

    Úsala cuando el cliente quiere una cita pero no mencionó ciudad ni zona:
    presenta las zonas con presentar_opciones (tipo lista) para que toque una.
    """
    return await _listar_zonas()
