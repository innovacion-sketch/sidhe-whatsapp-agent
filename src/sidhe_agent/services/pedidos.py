"""Estado de fabricación de plantillas: importación y consulta.

La hoja STATUS del Excel de operaciones trae los status escritos a mano, con
variantes ("VER EN PX PEND ESTUDIOS", "VER EM PEND ESTUDIOS", …). Aquí se
normalizan a unas pocas categorías estables para que el agente nunca tenga
que interpretar texto libre ni inventar un estado: lo que no cae en una
categoría conocida se marca para revisión humana.
"""

import datetime
import re
import unicodedata

from sqlalchemy import delete, func, select

from ..db.models import Pedido
from ..db.session import get_session

# Categorías con las que responde el agente
LISTO = "listo_en_sucursal"
ENTREGADO = "entregado"
ENVIADO = "enviado_a_domicilio"
EN_PROCESO = "en_proceso"
REVISION = "requiere_revision"

# Status exactos de la hoja → categoría
MAPA_EXACTO = {
    "EN SUCURSAL": LISTO,
    "IMPRESION LISTA": EN_PROCESO,
    "ENTREGADO": ENTREGADO,
    "TERMINADO": EN_PROCESO,
    "IMPRESION": EN_PROCESO,
    "PEGADO": EN_PROCESO,
    "PEDIDO": EN_PROCESO,
    "NO PROCEDE": REVISION,
}

# Prefijos frecuentes con variantes de escritura
PREFIJOS = (
    ("ENVIADO A DOMICILIO", ENVIADO),
    ("VER EN", REVISION),
    ("VER EM", REVISION),
    ("VER PENDIENTE", REVISION),
    ("PX PENDIENTE", REVISION),
    ("GARANTIA", REVISION),
)


def normalizar_texto(valor: str) -> str:
    """Mayúsculas, sin acentos y con espacios colapsados."""
    if not valor:
        return ""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", str(valor))
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_acentos).strip().upper()


def normalizar_telefono(valor: str | None) -> str | None:
    """Últimos 10 dígitos: así viene en la hoja y así compara con WhatsApp."""
    if not valor:
        return None
    digitos = re.sub(r"\D", "", str(valor))
    return digitos[-10:] if len(digitos) >= 10 else None


def clasificar_status(crudo: str) -> str:
    """Status de la hoja → categoría estable. Lo desconocido va a revisión."""
    texto = normalizar_texto(crudo)
    if not texto:
        return REVISION
    if texto in MAPA_EXACTO:
        return MAPA_EXACTO[texto]
    for prefijo, categoria in PREFIJOS:
        if texto.startswith(prefijo):
            return categoria
    return REVISION


def _a_dict(pedido: Pedido) -> dict:
    return {
        "nombre": pedido.nombre,
        "sucursal": pedido.sucursal,
        "fecha_del_estudio": pedido.fecha.isoformat() if pedido.fecha else None,
        "status": pedido.status,
        "status_en_sistema": pedido.status_original,
        "donde_esta": pedido.localizacion_final or pedido.sucursal,
    }


async def buscar_por_telefono(telefono: str) -> list[dict]:
    """Pedidos de un teléfono, del más reciente al más antiguo."""
    numero = normalizar_telefono(telefono)
    if not numero:
        return []
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Pedido)
                .where(Pedido.telefono == numero)
                .order_by(Pedido.fecha.desc().nullslast())
                .limit(5)
            )
        ).scalars().all()
    return [_a_dict(p) for p in filas]


async def buscar_por_nombre(nombre: str, sucursal: str) -> list[dict]:
    """Respaldo cuando el teléfono no está en la hoja.

    Exige nombre Y sucursal, y solo devuelve resultados si el nombre coincide
    completo: así el bot no expone el pedido de otra persona por una búsqueda
    parcial.
    """
    nombre_norm = normalizar_texto(nombre)
    sucursal_norm = normalizar_texto(sucursal)
    if len(nombre_norm.split()) < 2 or not sucursal_norm:
        return []
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Pedido)
                .where(
                    Pedido.nombre_normalizado == nombre_norm,
                    func.upper(Pedido.sucursal) == sucursal_norm,
                )
                .order_by(Pedido.fecha.desc().nullslast())
                .limit(5)
            )
        ).scalars().all()
    return [_a_dict(p) for p in filas]


async def total() -> int:
    async with get_session() as session:
        return (await session.execute(select(func.count(Pedido.id)))).scalar_one()


def _fecha_valida(valor) -> datetime.date | None:
    """La hoja trae años tecleados mal (0206, 0325); se descartan."""
    if isinstance(valor, datetime.datetime):
        valor = valor.date()
    if not isinstance(valor, datetime.date):
        return None
    return valor if 2015 <= valor.year <= 2100 else None


def preparar_filas(filas: list[tuple]) -> list[dict]:
    """Filas crudas de la hoja STATUS → registros listos para insertar.

    Orden de columnas: Fecha, Nombre, Sucursal, Envio, Dirección, Telefono,
    Status, Lugar de Impresion, Localizacion Final.
    """
    registros = []
    for fila in filas:
        celdas = list(fila) + [None] * (9 - len(fila))
        nombre = str(celdas[1] or "").strip()
        if not nombre:
            continue
        crudo = str(celdas[6] or "")
        registros.append(
            {
                "fecha": _fecha_valida(celdas[0]),
                "nombre": nombre[:200],
                "nombre_normalizado": normalizar_texto(nombre)[:200],
                "sucursal": str(celdas[2] or "").strip()[:80],
                "telefono": normalizar_telefono(celdas[5]),
                "status_original": crudo.strip()[:120],
                "status": clasificar_status(crudo),
                "lugar_impresion": str(celdas[7] or "").strip()[:80] or None,
                "localizacion_final": str(celdas[8] or "").strip()[:80] or None,
                "envio": str(celdas[3] or "").strip()[:120] or None,
            }
        )
    return registros


async def reemplazar(registros: list[dict]) -> int:
    """Sustituye la tabla completa. El Excel es la fuente de verdad."""
    async with get_session() as session:
        await session.execute(delete(Pedido))
        for i in range(0, len(registros), 1000):
            session.add_all(Pedido(**r) for r in registros[i : i + 1000])
            await session.flush()
        await session.commit()
    return len(registros)
