"""Aviso cuando hay citas hoy en una sucursal donde nadie abrió.

El sistema de asistencias ya avisa que un stand está descubierto. Lo que no
sabe es que además hay clientes con cita para ese día: eso solo lo sabe el
bot. Este aviso junta las dos mitades y manda la lista con nombre y
teléfono, para que alguien pueda hablarles antes de que hagan el viaje.

Se manda **una vez por sucursal y por día**: la idea es que alguien actúe,
no llenar la bandeja cada media hora.
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from ..config import get_settings
from ..db.models import Cita, Slot, Sucursal
from ..db.session import get_session
from ..tools.citas import fecha_legible
from . import asistencias, correo
from .agenda import DIAS_SEMANA

logger = structlog.get_logger(__name__)

# Lo ya avisado, para no repetir: (fecha, sucursal) para el aviso del día y
# ("cerrado", fecha, sucursal) para el anticipado
_avisadas: set[tuple] = set()
# Día en que se mandó la última revisión semanal
_ultima_revision: datetime.date | None = None


def redactar(sucursal: str, fecha: datetime.date, citas: list[dict], hora: str) -> tuple[str, str]:
    """El asunto y el cuerpo del correo. Separado para poder probarlo."""
    asunto = f"⚠️ {len(citas)} cita(s) hoy en {sucursal} y el stand no ha abierto"
    lineas = [
        f"A las {hora} nadie ha marcado entrada en {sucursal},",
        f"y hay {len(citas)} cita(s) agendadas para hoy {fecha_legible(fecha)}:",
        "",
    ]
    for cita in citas:
        lineas.append(
            f"  {cita['hora']}  {cita['cliente']}  {cita['telefono']}  "
            f"(folio {cita['folio']})"
        )
    lineas += [
        "",
        "Conviene hablarles antes de que lleguen a una tienda cerrada.",
        "",
        "-- Bot de WhatsApp de Sidhe",
    ]
    return asunto, "\n".join(lineas)


def redactar_dia_cerrado(
    sucursal: str, fecha: datetime.date, citas: list[dict]
) -> tuple[str, str]:
    """Aviso anticipado: ese día el rol dice que esa sucursal no abre."""
    asunto = (
        f"⚠️ {len(citas)} cita(s) el {fecha.strftime('%d/%m')} en {sucursal}, "
        "sin personal programado"
    )
    lineas = [
        f"El rol de personal no tiene a nadie en {sucursal} "
        f"el {fecha_legible(fecha)},",
        f"y hay {len(citas)} cita(s) agendadas para ese día:",
        "",
    ]
    for cita in citas:
        lineas.append(
            f"  {cita['hora']}  {cita['cliente']}  {cita['telefono']}  "
            f"(folio {cita['folio']})"
        )
    lineas += [
        "",
        "Hay tiempo de reubicarlas o de asignar a alguien.",
        "El bot ya dejó de ofrecer ese día en esa sucursal.",
        "",
        "-- Bot de WhatsApp de Sidhe",
    ]
    return asunto, "\n".join(lineas)


async def _citas_confirmadas(desde: datetime.date, hasta: datetime.date) -> list[tuple]:
    async with get_session() as session:
        return (
            await session.execute(
                select(Cita, Slot, Sucursal)
                .join(Slot, Cita.slot_id == Slot.id)
                .join(Sucursal, Cita.sucursal_id == Sucursal.id)
                .where(
                    Cita.estado == "confirmada",
                    Slot.fecha >= desde,
                    Slot.fecha <= hasta,
                )
                .order_by(Slot.fecha, Slot.hora_inicio)
            )
        ).all()


def _resumir(cita: Cita, slot: Slot) -> dict:
    return {
        "hora": slot.hora_inicio.strftime("%H:%M"),
        "cliente": cita.cliente_nombre,
        "telefono": cita.cliente_telefono,
        "folio": cita.id,
    }


async def citas_de_hoy(hoy: datetime.date) -> dict[str, list[dict]]:
    """Citas confirmadas de hoy, agrupadas por nombre de sucursal."""
    por_sucursal: dict[str, list[dict]] = {}
    for cita, slot, sucursal in await _citas_confirmadas(hoy, hoy):
        por_sucursal.setdefault(sucursal.nombre, []).append(_resumir(cita, slot))
    return por_sucursal


async def avisar_dias_cerrados(dias: int = 21) -> int:
    """Citas que caen en días que el rol da por cerrados.

    Esas no las agarra la revisión de checadas: ahí nadie estaba programado,
    así que nadie "faltó" y ningún sistema lo nota. La cita existe porque se
    agendó antes de que se cargara el rol, y el cliente va a viajar a una
    tienda cerrada si nadie le habla.
    """
    hoy = datetime.datetime.now(ZoneInfo(get_settings().tz)).date()
    hasta = hoy + datetime.timedelta(days=dias)
    cerrados = await asistencias.dias_sin_personal(hoy, hasta)
    if not cerrados:
        return 0

    agrupadas: dict[tuple[str, datetime.date], list[dict]] = {}
    for cita, slot, sucursal in await _citas_confirmadas(hoy, hasta):
        if (sucursal.nombre, slot.fecha) in cerrados:
            agrupadas.setdefault((sucursal.nombre, slot.fecha), []).append(
                _resumir(cita, slot)
            )

    enviados = 0
    for (sucursal, fecha), citas in sorted(agrupadas.items()):
        if ("cerrado", fecha, sucursal) in _avisadas:
            continue
        asunto, cuerpo = redactar_dia_cerrado(sucursal, fecha, citas)
        if await correo.enviar(asunto, cuerpo):
            _avisadas.add(("cerrado", fecha, sucursal))
            enviados += 1
            logger.warning(
                "citas_en_dia_sin_personal",
                sucursal=sucursal,
                fecha=fecha.isoformat(),
                citas=len(citas),
            )
    return enviados


async def revisar() -> int:
    """Revisa y avisa. Devuelve cuántos correos mandó."""
    ajustes = get_settings()
    ahora = datetime.datetime.now(ZoneInfo(ajustes.tz))
    citas = await citas_de_hoy(ahora.date())
    if not citas:
        return 0
    descubiertas = await asistencias.sucursales_sin_checada(
        ahora.date(), ahora.time()
    )
    if not descubiertas:
        # None (no se pudo consultar) o vacío (todo cubierto): no se inventa
        return 0

    enviados = 0
    for sucursal in sorted(descubiertas & citas.keys()):
        if (ahora.date(), sucursal) in _avisadas:
            continue
        asunto, cuerpo = redactar(
            sucursal, ahora.date(), citas[sucursal], ahora.strftime("%H:%M")
        )
        if await correo.enviar(asunto, cuerpo):
            _avisadas.add((ahora.date(), sucursal))
            enviados += 1
            logger.warning(
                "citas_sin_personal_avisadas",
                sucursal=sucursal,
                citas=len(citas[sucursal]),
            )
    return enviados


ESTADOS = ("CERRADO", "DESCANSO", "FUERA", "SIN ROL", "OK")


def clasificar(
    nombre: str,
    fecha: datetime.date,
    hora: datetime.time,
    dias_operacion: list | None,
    sin_personal: set,
    turnos: dict | None,
) -> str:
    """En qué situación queda una cita frente al rol de personal.

    CERRADO  el rol no tiene a nadie ese día en esa sucursal
    DESCANSO día de descanso fijo de la sucursal
    FUERA    hay gente ese día, pero nadie a la hora de la cita
    SIN ROL  esa semana todavía no se carga: no se sabe
    OK       alguien programado cubre esa hora
    """
    if DIAS_SEMANA[fecha.weekday()] not in set(dias_operacion or DIAS_SEMANA):
        return "DESCANSO"
    if (nombre, fecha) in sin_personal:
        return "CERRADO"
    del_dia = (turnos or {}).get((nombre, fecha))
    if not del_dia:
        return "SIN ROL"
    return "OK" if asistencias.hay_quien_atienda(del_dia, hora) else "FUERA"


async def revisar_citas_futuras(dias: int = 21) -> tuple[list[dict], dict[str, int]]:
    """Clasifica cada cita futura. Devuelve (las problemáticas, conteos)."""
    hoy = datetime.datetime.now(ZoneInfo(get_settings().tz)).date()
    hasta = hoy + datetime.timedelta(days=dias)
    sin_personal = await asistencias.dias_sin_personal(hoy, hasta)
    turnos = await asistencias.turnos(hoy, hasta)

    problemas: list[dict] = []
    conteo: dict[str, int] = {}
    for cita, slot, sucursal in await _citas_confirmadas(hoy, hasta):
        estado = clasificar(
            sucursal.nombre,
            slot.fecha,
            slot.hora_inicio,
            sucursal.dias_operacion,
            sin_personal,
            turnos,
        )
        conteo[estado] = conteo.get(estado, 0) + 1
        if estado != "OK":
            problemas.append(
                {
                    "estado": estado,
                    "fecha": slot.fecha,
                    "sucursal": sucursal.nombre,
                    **_resumir(cita, slot),
                }
            )
    return problemas, conteo


def redactar_revision(problemas: list[dict], conteo: dict[str, int], dias: int):
    """Asunto y cuerpo de la revisión semanal."""
    con_problema = sum(v for k, v in conteo.items() if k not in ("OK", "SIN ROL"))
    total = sum(conteo.values())
    asunto = (
        f"Revisión semanal de citas: {con_problema} por atender de {total}"
        if con_problema
        else f"Revisión semanal de citas: las {total} tienen quién las atienda"
    )
    lineas = [f"Citas confirmadas en los próximos {dias} días: {total}", ""]
    for estado in ESTADOS:
        if conteo.get(estado):
            lineas.append(f"  {estado:9} {conteo[estado]}")
    if con_problema:
        lineas += ["", "A revisar:", ""]
        for p in problemas:
            if p["estado"] == "SIN ROL":
                continue
            lineas.append(
                f"  {p['estado']:9} {p['fecha']} {p['hora']}  {p['sucursal']}  "
                f"{p['cliente']}  {p['telefono']}  (folio {p['folio']})"
            )
    if conteo.get("SIN ROL"):
        lineas += [
            "",
            f"Hay {conteo['SIN ROL']} cita(s) en semanas cuyo rol todavía no se",
            "carga. No es que falte personal: es que aún no se sabe. Al cargar",
            "esas semanas conviene volver a revisar.",
        ]
    lineas += ["", "-- Bot de WhatsApp de Sidhe"]
    return asunto, "\n".join(lineas)


def toca_revision_semanal(
    ahora: datetime.datetime, ultima: datetime.date | None, dia: int, hora: int
) -> bool:
    """Si toca mandar la revisión: el día acordado, pasada la hora, una vez."""
    if ahora.weekday() != dia or ahora.hour < hora:
        return False
    return ultima != ahora.date()


async def enviar_revision_semanal(dias: int = 21) -> bool:
    problemas, conteo = await revisar_citas_futuras(dias)
    if not conteo:
        return False
    asunto, cuerpo = redactar_revision(problemas, conteo, dias)
    return await correo.enviar(asunto, cuerpo)


async def vigilar(cada_minutos: int = 30, espera_inicial: float = 60.0) -> None:
    """Revisa cada tanto durante el horario de tiendas. No muere por errores."""
    global _ultima_revision
    await asyncio.sleep(espera_inicial)
    ajustes = get_settings()
    while True:
        try:
            ahora = datetime.datetime.now(ZoneInfo(ajustes.tz))
            if ajustes.alerta_citas_desde <= ahora.hour <= ajustes.alerta_citas_hasta:
                await revisar()
                # Y los días que el rol ya dio por cerrados, con anticipación
                await avisar_dias_cerrados()
            # Repaso completo de la semana, el día acordado
            if toca_revision_semanal(
                ahora,
                _ultima_revision,
                ajustes.revision_semanal_dia,
                ajustes.revision_semanal_hora,
            ) and await enviar_revision_semanal(ajustes.revision_semanal_dias):
                _ultima_revision = ahora.date()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error_vigilando_citas_sin_personal")
        await asyncio.sleep(cada_minutos * 60)
