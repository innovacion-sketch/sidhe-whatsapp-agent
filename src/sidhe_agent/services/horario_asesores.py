"""Cuándo hay alguien del equipo para contestar, y cómo decírselo al cliente.

El bot escalaba siempre con "un asesor te contactará pronto", fuera martes
a mediodía o domingo a las 11 de la noche. Una promesa que el bot no
controla y que de noche es falsa: el cliente espera, nadie aparece y la
molestia es con la marca, no con el bot.

Aquí vive lo único que hace falta para no prometer de más: si ahorita hay
asesores, y si no, cuándo vuelven, dicho como lo diría una persona
("mañana a partir de las 10:00").

El horario es por día porque así trabaja el equipo: entre semana de 10 a
6, y sábado y domingo solo hasta la 1. Se escribe como se diría:

    "lunes-viernes 10-18; sabado-domingo 10-13"
"""

import datetime
from zoneinfo import ZoneInfo

from ..config import get_settings

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_CON_ACENTO = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

Horario = dict[int, tuple[datetime.time, datetime.time]]


def _dia(nombre: str) -> int:
    limpio = nombre.strip().lower().replace("é", "e").replace("á", "a")
    if limpio not in DIAS:
        raise ValueError(f"día desconocido: {nombre!r}")
    return DIAS.index(limpio)


def _hora(texto: str) -> datetime.time:
    horas, _, minutos = texto.strip().partition(":")
    return datetime.time(int(horas), int(minutos or 0))


def leer_horario(texto: str) -> Horario:
    """"lunes-viernes 10-18; sabado-domingo 10-13" → {0: (10:00, 18:00), …}.

    Días sueltos con coma ("sabado,domingo") o en rango ("lunes-viernes").
    Si una regla no se entiende, revienta: un horario mal leído haría que
    el bot le diga a los clientes una hora falsa, y eso es peor que no
    arrancar.
    """
    horario: Horario = {}
    for regla in filter(None, (r.strip() for r in (texto or "").split(";"))):
        dias_txt, _, horas_txt = regla.rpartition(" ")
        inicio_txt, _, fin_txt = horas_txt.partition("-")
        inicio, fin = _hora(inicio_txt), _hora(fin_txt)
        if not dias_txt or fin <= inicio:
            raise ValueError(f"regla de horario inválida: {regla!r}")
        for parte in dias_txt.split(","):
            desde, _, hasta = parte.partition("-")
            primero = _dia(desde)
            ultimo = _dia(hasta) if hasta else primero
            for d in range(primero, ultimo + 1):
                horario[d] = (inicio, fin)
    return horario


def _horario(horario: str | None) -> Horario:
    return leer_horario(get_settings().asesores_horario if horario is None else horario)


def ahora_local() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(get_settings().tz))


def en_horario(momento: datetime.datetime, horario: str | None = None) -> bool:
    ventana = _horario(horario).get(momento.weekday())
    return bool(ventana) and ventana[0] <= momento.time() < ventana[1]


def proxima_apertura(
    momento: datetime.datetime, horario: str | None = None
) -> datetime.datetime:
    """El siguiente momento en que hay asesores. Si ya hay, es ahora."""
    if en_horario(momento, horario):
        return momento
    dias = _horario(horario)
    for adelante in range(8):
        dia = momento.date() + datetime.timedelta(days=adelante)
        if dia.weekday() not in dias:
            continue
        apertura = datetime.datetime.combine(
            dia, dias[dia.weekday()][0], tzinfo=momento.tzinfo
        )
        if apertura > momento:
            return apertura
    return momento  # sin ningún día de atención configurado


def cuando_contestan(momento: datetime.datetime, horario: str | None = None) -> str:
    """"hoy a partir de las 10:00", "mañana…", "el lunes…"; "" si ya hay."""
    if en_horario(momento, horario):
        return ""
    apertura = proxima_apertura(momento, horario)
    hora = apertura.strftime("%H:%M")
    faltan = (apertura.date() - momento.date()).days
    if faltan == 0:
        return f"hoy a partir de las {hora}"
    if faltan == 1:
        return f"mañana a partir de las {hora}"
    return f"el {DIAS_CON_ACENTO[apertura.weekday()]} a partir de las {hora}"


def _nombre_de_grupo(dias: list[int]) -> str:
    if dias == [5, 6]:
        return "sábados y domingos"
    if len(dias) == 1:
        nombre = DIAS_CON_ACENTO[dias[0]]
        return f"los {nombre if nombre.endswith('s') else nombre + 's'}"
    if len(dias) == 2:
        return f"{DIAS_CON_ACENTO[dias[0]]} y {DIAS_CON_ACENTO[dias[1]]}"
    return f"de {DIAS_CON_ACENTO[dias[0]]} a {DIAS_CON_ACENTO[dias[-1]]}"


def horario_legible(horario: str | None = None) -> str:
    """"de lunes a viernes de 10:00 a 18:00, y sábados y domingos de 10:00 a 13:00"."""
    dias = _horario(horario)
    if not dias:
        return ""
    # Días seguidos con la misma hora forman un grupo
    grupos: list[tuple[list[int], tuple]] = []
    for d in sorted(dias):
        if grupos and grupos[-1][1] == dias[d] and grupos[-1][0][-1] == d - 1:
            grupos[-1][0].append(d)
        else:
            grupos.append(([d], dias[d]))
    partes = [
        f"{_nombre_de_grupo(ds)} de {ini.strftime('%H:%M')} a {fin.strftime('%H:%M')}"
        for ds, (ini, fin) in grupos
    ]
    if len(partes) == 1:
        return partes[0]
    return ", ".join(partes[:-1]) + ", y " + partes[-1]


def horas_habiles_entre(
    desde: datetime.datetime, hasta: datetime.datetime, horario: str | None = None
) -> float:
    """Cuántas horas de atención hubo entre dos momentos.

    Para que "nadie atendió en 4 horas" cuente horas en que PODÍA haber
    alguien. Contando reloj corrido, un escalamiento del viernes a las 5:55
    se daba por abandonado a las 10 de la noche, y el bot volvía
    disculpándose porque nadie contestó, justo cuando le había dicho al
    cliente que le contestaban al día siguiente.
    """
    # El horario es de México y la base guarda en UTC: sin convertir, las
    # 10 de la mañana serían las 4 de la madrugada
    local = ZoneInfo(get_settings().tz)
    desde = desde.astimezone(local) if desde.tzinfo else desde.replace(tzinfo=local)
    hasta = hasta.astimezone(local) if hasta.tzinfo else hasta.replace(tzinfo=local)
    if hasta <= desde:
        return 0.0
    dias = _horario(horario)
    total = datetime.timedelta()
    dia = desde.date()
    while dia <= hasta.date():
        if dia.weekday() in dias:
            inicio, fin = dias[dia.weekday()]
            apertura = datetime.datetime.combine(dia, inicio, tzinfo=desde.tzinfo)
            cierre = datetime.datetime.combine(dia, fin, tzinfo=desde.tzinfo)
            tramo = min(cierre, hasta) - max(apertura, desde)
            if tramo > datetime.timedelta():
                total += tramo
        dia += datetime.timedelta(days=1)
    return total.total_seconds() / 3600
