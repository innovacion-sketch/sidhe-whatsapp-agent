"""Agendar desde el Excel de citas, sin que choque con el bot.

A las sucursales les llegan pacientes en persona o por teléfono, y antes
los anotaban directo en el Excel. Hoy el Excel es un espejo de la base del
bot (n8n copia cada cita): una fila escrita a mano no la ve el bot, que
sigue ofreciendo ese horario y lo da a otra persona. Doble cita.

Aquí la sucursal llena una fila en la pestaña "Agendar" y el servicio la
agenda en la MISMA base que el bot, con el mismo candado por horario, y
contesta en la fila misma:

    Sucursal | Fecha | Hora | Paciente | Teléfono | ¿Recordatorio? | Cancelar | Estado | Folio

- Si el bot dio ese horario un segundo antes, la fila dice que está
  ocupado y cuáles quedan libres.
- Si la fila tiene un error, se corrige y se vuelve a intentar sola: una
  columna oculta guarda la huella de lo último que se intentó, así que
  solo se reintenta cuando alguien cambió algo.
- Para cancelar se marca la casilla "Cancelar" en la fila con folio.

La cita que sale de aquí es igual a las del bot: aparece en "Todas", en
la pestaña de la sucursal y en su calendario.
"""

import asyncio
import datetime
import hashlib
import json
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..db.models import Slot, Sucursal
from ..db.session import get_session

logger = structlog.get_logger(__name__)

ALCANCES = ["https://www.googleapis.com/auth/spreadsheets"]

ENCABEZADOS = [
    "Sucursal",
    "Fecha",
    "Hora",
    "Paciente",
    "Teléfono",
    "¿Recordatorio por WhatsApp?",
    "Cancelar",
    "Estado (lo llena el bot)",
    "Folio",
    "control",  # oculta: huella de lo último que se intentó
]
SUCURSAL, FECHA, HORA, PACIENTE, TELEFONO, RECORDATORIO, CANCELAR, ESTADO, FOLIO, CONTROL = range(10)

# De dónde vino la cita. Sin recordatorio si el paciente no aceptó: WhatsApp
# exige que la persona haya aceptado mensajes del negocio, y alguien que
# llegó en persona nunca le escribió al número.
CANAL_SUCURSAL = "sucursal"
CANAL_SUCURSAL_SIN_RECORDATORIO = "sucursal_sin_recordatorio"

# Las fechas de Sheets son días desde el 30 de diciembre de 1899
EPOCA_SHEETS = datetime.date(1899, 12, 30)

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


# --- leer lo que escribió la sucursal ---

def _celda(fila: list, i: int) -> Any:
    return fila[i] if i < len(fila) else ""


def _texto(valor: Any) -> str:
    return " ".join(str(valor if valor is not None else "").split())


def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).strip()


def leer_fecha(valor: Any) -> datetime.date | None:
    """Número de serie de Sheets, "29/09/2026", "29-09-26" o "2026-09-29"."""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if valor < 1:
            return None
        return EPOCA_SHEETS + datetime.timedelta(days=int(valor))
    texto = _texto(valor)
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def leer_hora(valor: Any) -> datetime.time | None:
    """Fracción del día de Sheets (0.5), "12:00", "12", "4 pm", "16:00 hrs"."""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if 0 <= valor < 1:
            minutos = round(valor * 24 * 60)
            return datetime.time(minutos // 60 % 24, minutos % 60)
        if 1 <= valor < 24:
            return datetime.time(int(valor))
        return None
    texto = _sin_acentos(_texto(valor)).replace(".", "").replace("hrs", "").replace("h", "")
    tarde = "pm" in texto
    texto = texto.replace("pm", "").replace("am", "").strip()
    horas, _, minutos = texto.partition(":")
    if not horas.strip().isdigit() or (minutos and not minutos.strip().isdigit()):
        return None
    hora = int(horas)
    if tarde and hora < 12:
        hora += 12
    if not 0 <= hora < 24:
        return None
    return datetime.time(hora, int(minutos or 0))


def leer_telefono(valor: Any) -> str | None:
    """10 dígitos de celular mexicano → "+521XXXXXXXXXX".

    Con el mismo formato que usa WhatsApp: si ese paciente escribe después
    al bot, "¿cuándo es mi cita?" la encuentra por su número.
    """
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    digitos = "".join(c for c in str(valor or "") if c.isdigit())
    if len(digitos) < 10:
        return None
    return f"+521{digitos[-10:]}"


def dijo_que_si(valor: Any) -> bool:
    """Casilla marcada, "Sí", "si", "x". Vacío es no."""
    if isinstance(valor, bool):
        return valor
    return _sin_acentos(_texto(valor)) in {"si", "s", "x", "true", "verdadero", "1"}


def huella(fila: list) -> str:
    """Lo que se intentó agendar, para no reintentar lo mismo cada minuto."""
    datos = [_texto(_celda(fila, i)) for i in range(SUCURSAL, CANCELAR)]
    return hashlib.sha1(json.dumps(datos).encode()).hexdigest()[:12]


def que_hacer(fila: list) -> str | None:
    """"agendar", "cancelar" o None (nada nuevo en esta fila)."""
    folio = _texto(_celda(fila, FOLIO))
    estado = _texto(_celda(fila, ESTADO))
    if folio:
        if dijo_que_si(_celda(fila, CANCELAR)) and "Cancelada" not in estado:
            return "cancelar"
        return None
    if not any(_texto(_celda(fila, i)) for i in range(SUCURSAL, RECORDATORIO)):
        return None  # fila vacía
    if huella(fila) == _texto(_celda(fila, CONTROL)):
        return None  # ya se intentó así; espera a que la corrijan
    return "agendar"


# --- resultado que se escribe en la fila ---

@dataclass
class Resultado:
    estado: str
    folio: str = ""
    control: str = ""


def _legible(fecha: datetime.date, hora: datetime.time) -> str:
    return f"{DIAS[fecha.weekday()]} {fecha.day:02d}/{fecha.month:02d} {hora.strftime('%H:%M')}"


def _libres_texto(libres: list[str]) -> str:
    return f"Libres ese día: {', '.join(libres)}" if libres else "Ese día ya no quedan horarios"


MENSAJES_DE_ERROR = {
    "horario_pasado": "❌ Ese horario ya pasó",
    "sin_personal_ese_dia": "❌ Ese día no hay personal en la sucursal",
    "ya_tienes_cita_en_ese_horario": "❌ Ese paciente ya tiene cita a esa hora",
}


@dataclass
class Horario:
    """Lo que se encontró en la base para la fila."""

    slot_id: int | None
    sucursal: str | None
    libres: list[str]


BuscarHorario = Callable[[str, datetime.date, datetime.time], Awaitable[Horario]]
Agendar = Callable[[int, str, str, str], Awaitable[dict]]
Cancelar = Callable[[int, str], Awaitable[dict]]


async def procesar_fila(
    fila: list, buscar: BuscarHorario, agendar: Agendar, cancelar: Cancelar
) -> Resultado | None:
    """Lo que hay que escribir en la fila, o None si no hay nada que hacer."""
    accion = que_hacer(fila)
    if accion is None:
        return None

    if accion == "cancelar":
        folio = _texto(_celda(fila, FOLIO))
        telefono = leer_telefono(_celda(fila, TELEFONO))
        if not folio.isdigit() or telefono is None:
            return Resultado("❌ Para cancelar hace falta el folio y el teléfono", folio)
        hecho = await cancelar(int(folio), telefono)
        if hecho.get("ok"):
            return Resultado("🚫 Cancelada", folio, _texto(_celda(fila, CONTROL)))
        if hecho.get("error") == "cita_no_cancelable":
            return Resultado("🚫 Cancelada", folio, _texto(_celda(fila, CONTROL)))
        return Resultado("❌ No se pudo cancelar: el folio y el teléfono no coinciden", folio)

    marca = huella(fila)
    faltan = [
        nombre
        for nombre, i in (
            ("sucursal", SUCURSAL), ("fecha", FECHA), ("hora", HORA),
            ("paciente", PACIENTE), ("teléfono", TELEFONO),
        )
        if not _texto(_celda(fila, i))
    ]
    if faltan:
        return Resultado(f"❌ Falta: {', '.join(faltan)}", control=marca)

    fecha = leer_fecha(_celda(fila, FECHA))
    if fecha is None:
        return Resultado("❌ No entiendo la fecha. Escríbela como 29/09/2026", control=marca)
    hora = leer_hora(_celda(fila, HORA))
    if hora is None:
        return Resultado("❌ No entiendo la hora. Escríbela como 12:00", control=marca)
    telefono = leer_telefono(_celda(fila, TELEFONO))
    if telefono is None:
        return Resultado("❌ El teléfono necesita 10 dígitos", control=marca)

    horario = await buscar(_texto(_celda(fila, SUCURSAL)), fecha, hora)
    if horario.sucursal is None:
        return Resultado(
            f"❌ No reconozco la sucursal \"{_texto(_celda(fila, SUCURSAL))}\"", control=marca
        )
    if horario.slot_id is None:
        return Resultado(
            f"❌ No hay horario a las {hora.strftime('%H:%M')} ese día. "
            + _libres_texto(horario.libres),
            control=marca,
        )

    canal = (
        CANAL_SUCURSAL
        if dijo_que_si(_celda(fila, RECORDATORIO))
        else CANAL_SUCURSAL_SIN_RECORDATORIO
    )
    hecho = await agendar(horario.slot_id, _texto(_celda(fila, PACIENTE)), telefono, canal)
    if "folio" in hecho:
        return Resultado(f"✅ Agendada · {_legible(fecha, hora)}", str(hecho["folio"]), marca)

    error = hecho.get("error", "")
    if error in ("slot_no_disponible", "sin_personal_a_esa_hora"):
        motivo = "Ocupado" if error == "slot_no_disponible" else "A esa hora no hay personal"
        return Resultado(f"❌ {motivo}. {_libres_texto(horario.libres)}", control=marca)
    return Resultado(
        MENSAJES_DE_ERROR.get(error, f"❌ No se pudo agendar ({error or 'sin detalle'})"),
        control=marca,
    )


# --- la base ---

async def buscar_horario(
    sucursal_txt: str, fecha: datetime.date, hora: datetime.time
) -> Horario:
    """El horario pedido, y los que siguen libres ese día en esa sucursal."""
    buscada = _sin_acentos(sucursal_txt)
    async with get_session() as session:
        activas = (
            await session.execute(select(Sucursal).where(Sucursal.activa.is_(True)))
        ).scalars().all()
        sucursal = next(
            (s for s in activas if _sin_acentos(s.nombre) == buscada), None
        ) or next((s for s in activas if buscada and buscada in _sin_acentos(s.nombre)), None)
        if sucursal is None:
            return Horario(None, None, [])

        del_dia = (
            await session.execute(
                select(Slot)
                .where(Slot.sucursal_id == sucursal.id, Slot.fecha == fecha)
                .order_by(Slot.hora_inicio)
            )
        ).scalars().all()

    ahora = datetime.datetime.now(ZoneInfo(get_settings().tz))
    libres = [
        s.hora_inicio.strftime("%H:%M")
        for s in del_dia
        if s.reservados < s.capacidad
        and datetime.datetime.combine(s.fecha, s.hora_inicio, tzinfo=ahora.tzinfo) > ahora
    ]
    pedido = next((s for s in del_dia if s.hora_inicio == hora), None)
    return Horario(pedido.id if pedido else None, sucursal.nombre, libres)


# --- google sheets ---

def _servicio() -> Any:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(get_settings().google_credentials)
    credenciales = service_account.Credentials.from_service_account_info(
        info, scopes=ALCANCES
    )
    return build("sheets", "v4", credentials=credenciales, cache_discovery=False)


def _rango(pestana: str, columnas: str) -> str:
    return f"'{pestana}'!{columnas}"


def _leer_sync(hoja: str, pestana: str) -> list[list]:
    respuesta = (
        _servicio()
        .spreadsheets()
        .values()
        .get(
            spreadsheetId=hoja,
            range=_rango(pestana, "A2:J"),
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="SERIAL_NUMBER",
        )
        .execute()
    )
    return respuesta.get("values", [])


def _escribir_sync(hoja: str, pestana: str, cambios: list[tuple[int, Resultado]]) -> None:
    datos = [
        {
            "range": _rango(pestana, f"H{numero}:J{numero}"),
            "values": [[r.estado, r.folio, r.control]],
        }
        for numero, r in cambios
    ]
    (
        _servicio()
        .spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=hoja,
            body={"valueInputOption": "RAW", "data": datos},
        )
        .execute()
    )


async def revisar_una_vez() -> int:
    """Lee la pestaña, agenda o cancela lo nuevo y escribe el resultado."""
    from ..tools.citas import _agendar_cita, _cancelar_cita

    ajustes = get_settings()
    hoja, pestana = ajustes.citas_sheet_id, ajustes.citas_sheet_pestana
    filas = await asyncio.to_thread(_leer_sync, hoja, pestana)

    cambios: list[tuple[int, Resultado]] = []
    for indice, fila in enumerate(filas):
        try:
            resultado = await procesar_fila(fila, buscar_horario, _agendar_cita, _cancelar_cita)
        except Exception:
            logger.exception("error_en_fila_del_excel", fila=indice + 2)
            resultado = Resultado(
                "❌ Error del sistema; se vuelve a intentar sola", control=""
            )
        if resultado is not None:
            cambios.append((indice + 2, resultado))

    if cambios:
        await asyncio.to_thread(_escribir_sync, hoja, pestana, cambios)
        logger.info("excel_de_citas_procesado", filas=len(cambios))
    return len(cambios)


async def vigilar(cada_segundos: int, espera_inicial: float = 20.0) -> None:
    """Revisa la pestaña cada minuto mientras el servicio esté arriba."""
    ajustes = get_settings()
    if not ajustes.citas_sheet_id or not ajustes.google_credentials:
        logger.info("agenda_desde_excel_apagada")
        return
    await asyncio.sleep(espera_inicial)
    while True:
        try:
            await revisar_una_vez()
        except Exception:
            # Sin permiso, pestaña borrada, Google caído: se reintenta solo
            logger.exception("error_revisando_excel_de_citas")
        await asyncio.sleep(max(15, cada_segundos))
