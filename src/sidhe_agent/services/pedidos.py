"""Estado de fabricación de plantillas: sincronización y consulta.

La hoja STATUS del Google Sheet de operaciones es la fuente de verdad; esta
tabla solo es una copia para que el bot conteste al instante.

Regla del negocio: casi todo lo que está en la hoja se está fabricando. Solo
"EN SUCURSAL" significa que el cliente ya puede pasar a recoger. Por eso lo
desconocido cae en "en proceso" y no en revisión humana: son etapas internas
de producción (IMPRESION, PEGADO, TERMINADO…), no excepciones. Se reservan
para un asesor las pocas marcas que sí son un problema (garantías, "VER
EN…", "NO PROCEDE").

Solo se guardan los últimos meses (PEDIDOS_MESES_HISTORIAL): lo anterior es
historial y nadie pregunta por WhatsApp por unas plantillas de hace dos años.
"""

import datetime
import re
import unicodedata

from sqlalchemy import delete, func, select

from ..db.models import Pedido, Sucursal
from ..db.session import get_session

# Categorías con las que responde el agente
LISTO = "listo_en_sucursal"
ENTREGADO = "entregado"
ENVIADO = "enviado_a_domicilio"
EN_PROCESO = "en_proceso"
REVISION = "requiere_revision"

# Lo único que autoriza a decirle al cliente "ya puedes pasar por ellas"
MAPA_EXACTO = {
    "EN SUCURSAL": LISTO,
    "ENTREGADO": ENTREGADO,
}

# Prefijos, para tolerar las variantes escritas a mano. Las excepciones van
# primero: "VER EN SUCURSAL" es una revisión, no un pedido listo.
PREFIJOS = (
    ("VER EN", REVISION),
    ("VER EM", REVISION),
    ("VER PENDIENTE", REVISION),
    ("PX PENDIENTE", REVISION),
    ("GARANTIA", REVISION),
    ("NO PROCEDE", REVISION),
    ("CANCELAD", REVISION),
    ("DEVOLUCION", REVISION),
    ("REEMBOLSO", REVISION),
    ("ENVIADO A DOMICILIO", ENVIADO),
    ("ENVIADO", ENVIADO),
    ("EN SUCURSAL", LISTO),
    ("ENTREGADO", ENTREGADO),
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
    """Status de la hoja → categoría estable.

    Lo que no reconocemos es una etapa más de fabricación (así lo confirmó
    operaciones), no una excepción: el default es EN_PROCESO.
    """
    texto = normalizar_texto(crudo)
    if texto in MAPA_EXACTO:
        return MAPA_EXACTO[texto]
    for prefijo, categoria in PREFIJOS:
        if texto.startswith(prefijo):
            return categoria
    return EN_PROCESO


COLUMNAS_ESPERADAS = ("FECHA", "NOMBRE", "SUCURSAL")


def columnas_faltantes(encabezado: list) -> list[str]:
    """Columnas obligatorias que no están en la primera fila de la hoja.

    Protege contra leer la hoja equivocada: si alguien renombra la pestaña o
    cambia el rango, preferimos fallar a llenar la tabla con basura.
    """
    presentes = {normalizar_texto(str(celda or "")) for celda in encabezado}
    return [c for c in COLUMNAS_ESPERADAS if c not in presentes]


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


# Palabras que traen casi todas las sucursales y no distinguen a ninguna
GENERICAS = frozenset(
    {"LIVERPOOL", "PLAZA", "GALERIAS", "CENTRO", "COMERCIAL",
     "DE", "DEL", "LA", "EL", "LOS", "LAS"}
)

# Cómo nombra operaciones a una sucursal contra cómo la nombramos nosotros
SINONIMOS = {"GDL": "GUADALAJARA", "TOLUCA": "METEPEC"}


def _palabras_clave(texto: str) -> list[str]:
    palabras = [SINONIMOS.get(p, p) for p in normalizar_texto(texto).split()]
    return [p for p in palabras if p not in GENERICAS]


def sucursal_compatible(dicha: str, en_la_hoja: str) -> bool:
    """¿El cliente se refiere a esta sucursal?

    La hoja las nombra a su manera ("PARQUE DELTA", "GDL LA PERLA") y el
    cliente dice otra ("Delta", "Liverpool Parque Delta"). Se comparan
    PALABRAS COMPLETAS, no pedazos: comparar subcadenas hacía que "CALI"
    coincidiera con "AguasCALIentes" y le habríamos dicho a un cliente de
    Cali que sus plantillas estaban en Aguascalientes.
    """
    a, b = _palabras_clave(dicha), _palabras_clave(en_la_hoja)
    if not a or not b:
        return False
    if set(a) <= set(b) or set(b) <= set(a):
        return True
    # "SANLUIS" contra "SAN LUIS": la misma palabra escrita junta
    return "".join(a) == "".join(b)


async def buscar_por_nombre(nombre: str, sucursal: str) -> list[dict]:
    """Respaldo cuando el teléfono no está en la hoja.

    Exige nombre Y sucursal, y el nombre tiene que coincidir COMPLETO: así el
    bot no expone el pedido de otra persona por una búsqueda parcial. La
    sucursal sí se compara con tolerancia, porque el cliente no tiene por qué
    saber cómo la escribe operaciones.
    """
    nombre_norm = normalizar_texto(nombre)
    if len(nombre_norm.split()) < 2 or not normalizar_texto(sucursal):
        return []
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Pedido)
                .where(Pedido.nombre_normalizado == nombre_norm)
                .order_by(Pedido.fecha.desc().nullslast())
                .limit(20)
            )
        ).scalars().all()
    coinciden = [p for p in filas if sucursal_compatible(sucursal, p.sucursal)]
    return [_a_dict(p) for p in coinciden[:5]]


async def total() -> int:
    async with get_session() as session:
        return (await session.execute(select(func.count(Pedido.id)))).scalar_one()


# Las hojas de cálculo cuentan los días desde este origen
EPOCA_SERIAL = datetime.date(1899, 12, 30)


def _fecha_de_texto(texto: str) -> datetime.date | None:
    if not texto:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _fecha_valida(valor) -> datetime.date | None:
    """Normaliza la fecha venga como venga.

    openpyxl la entrega como datetime; la API de Sheets como número de serie
    o como texto. Además la hoja trae años tecleados mal (0206, 0325), que se
    descartan.
    """
    if isinstance(valor, bool):
        return None
    if isinstance(valor, datetime.datetime):
        valor = valor.date()
    elif isinstance(valor, (int, float)):
        try:
            valor = EPOCA_SERIAL + datetime.timedelta(days=int(valor))
        except (OverflowError, ValueError):
            return None
    elif isinstance(valor, str):
        valor = _fecha_de_texto(valor.strip())

    if not isinstance(valor, datetime.date):
        return None
    return valor if 2015 <= valor.year <= 2100 else None


def preparar_filas(filas: list[tuple], meses: int = 0) -> list[dict]:
    """Filas crudas de la hoja STATUS → registros listos para insertar.

    Orden de columnas: Fecha, Nombre, Sucursal, Envio, Dirección, Telefono,
    Status, Lugar de Impresion, Localizacion Final.

    Con `meses` > 0 solo se conservan los pedidos de esa ventana. La ventana
    se mide desde la fecha MÁS RECIENTE de la hoja, no desde hoy: si la hoja
    lleva días sin actualizarse, igual entran los últimos pedidos reales.
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

    if meses <= 0:
        return registros

    fechas = [r["fecha"] for r in registros if r["fecha"]]
    if not fechas:
        # Ninguna fecha se pudo leer: algo cambió en la hoja. Devolver vacío
        # hace que el llamador falle y conserve la copia anterior, en vez de
        # cargar 47 mil filas sin fecha como si fueran todas recientes.
        return []
    corte = max(fechas) - datetime.timedelta(days=31 * meses)
    return [r for r in registros if r["fecha"] and r["fecha"] >= corte]


async def reemplazar(registros: list[dict]) -> int:
    """Sustituye la tabla completa. La hoja es la fuente de verdad."""
    async with get_session() as session:
        await session.execute(delete(Pedido))
        for i in range(0, len(registros), 1000):
            session.add_all(Pedido(**r) for r in registros[i : i + 1000])
            await session.flush()
        await session.commit()
    return len(registros)


def resumen(registros: list[dict]) -> dict:
    """Conteos para el log de la sincronización y el script de importación."""
    fechas = [r["fecha"] for r in registros if r["fecha"]]
    por_categoria: dict[str, int] = {}
    for registro in registros:
        categoria = registro["status"]
        por_categoria[categoria] = por_categoria.get(categoria, 0) + 1
    return {
        "pedidos": len(registros),
        "con_telefono": sum(1 for r in registros if r["telefono"]),
        "desde": min(fechas).isoformat() if fechas else None,
        "hasta": max(fechas).isoformat() if fechas else None,
        "por_categoria": por_categoria,
    }


async def etiquetas_de_sucursales() -> list[str]:
    """Nombres y alias de las sucursales activas, para reconocer la hoja.

    La hoja también registra ventas que NO salen de un stand: eventos y
    corporativos (la sucursal móvil B2B) y plazas que no operamos por este
    canal. Sirve para distinguirlas.
    """
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Sucursal.nombre, Sucursal.alias).where(
                    Sucursal.activa.is_(True)
                )
            )
        ).all()
    etiquetas = []
    for nombre, alias in filas:
        etiquetas.append(nombre)
        etiquetas.extend(alias or [])
    return etiquetas


def es_de_sucursal(etiqueta: str, conocidas: list[str]) -> bool:
    """¿Esta venta salió de un stand, o es móvil/B2B?"""
    if not normalizar_texto(etiqueta):
        return False
    return any(sucursal_compatible(etiqueta, conocida) for conocida in conocidas)
