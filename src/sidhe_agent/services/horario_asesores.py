"""Cuándo hay alguien del equipo para contestar, y cómo decírselo al cliente.

El bot escalaba siempre con "un asesor te contactará pronto", fuera martes
a mediodía o domingo a las 11 de la noche. Una promesa que el bot no
controla y que de noche es falsa: el cliente espera, nadie aparece y la
molestia es con la marca, no con el bot.

Aquí vive lo único que hace falta para no prometer de más: si ahorita hay
asesores, y si no, cuándo vuelven, dicho como lo diría una persona
("mañana a partir de las 10:00").
"""

import datetime
from zoneinfo import ZoneInfo

from ..config import get_settings

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_CON_ACENTO = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _dias_de_atencion(texto: str) -> set[int]:
    """"lunes|martes|..." → {0, 1, ...}. Vacío o ilegible = todos los días."""
    elegidos = {
        DIAS.index(d.strip())
        for d in (texto or "").lower().replace("é", "e").replace("á", "a").split("|")
        if d.strip() in DIAS
    }
    return elegidos or set(range(7))


def _ajustes(inicio: int | None, fin: int | None, dias: str | None):
    ajustes = get_settings()
    return (
        ajustes.asesores_hora_inicio if inicio is None else inicio,
        ajustes.asesores_hora_fin if fin is None else fin,
        _dias_de_atencion(ajustes.asesores_dias if dias is None else dias),
    )


def ahora_local() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(get_settings().tz))


def en_horario(
    momento: datetime.datetime,
    inicio: int | None = None,
    fin: int | None = None,
    dias: str | None = None,
) -> bool:
    inicio, fin, abiertos = _ajustes(inicio, fin, dias)
    return momento.weekday() in abiertos and inicio <= momento.hour < fin


def proxima_apertura(
    momento: datetime.datetime,
    inicio: int | None = None,
    fin: int | None = None,
    dias: str | None = None,
) -> datetime.datetime:
    """El siguiente momento en que hay asesores. Si ya hay, es ahora."""
    hora_inicio, _, abiertos = _ajustes(inicio, fin, dias)
    if en_horario(momento, inicio, fin, dias):
        return momento
    for adelante in range(8):
        dia = momento.date() + datetime.timedelta(days=adelante)
        apertura = datetime.datetime.combine(
            dia, datetime.time(hora_inicio), tzinfo=momento.tzinfo
        )
        if dia.weekday() in abiertos and apertura > momento:
            return apertura
    return momento  # no debería pasar con al menos un día abierto


def cuando_contestan(
    momento: datetime.datetime,
    inicio: int | None = None,
    fin: int | None = None,
    dias: str | None = None,
) -> str:
    """"hoy a partir de las 10:00", "mañana…", "el lunes…"; "" si ya hay."""
    if en_horario(momento, inicio, fin, dias):
        return ""
    apertura = proxima_apertura(momento, inicio, fin, dias)
    hora = apertura.strftime("%H:%M")
    faltan = (apertura.date() - momento.date()).days
    if faltan == 0:
        return f"hoy a partir de las {hora}"
    if faltan == 1:
        return f"mañana a partir de las {hora}"
    return f"el {DIAS_CON_ACENTO[apertura.weekday()]} a partir de las {hora}"


def horario_legible(
    inicio: int | None = None, fin: int | None = None, dias: str | None = None
) -> str:
    hora_inicio, hora_fin, abiertos = _ajustes(inicio, fin, dias)
    if abiertos == set(range(7)):
        cuales = "todos los días"
    elif abiertos == set(range(6)):
        cuales = "de lunes a sábado"
    elif abiertos == set(range(5)):
        cuales = "de lunes a viernes"
    else:
        cuales = ", ".join(DIAS_CON_ACENTO[d] for d in sorted(abiertos))
    return f"{cuales} de {hora_inicio}:00 a {hora_fin}:00"
