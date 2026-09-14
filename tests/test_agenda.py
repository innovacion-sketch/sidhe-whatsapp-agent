"""Tests de la agenda que se mantiene abierta sola.

El caso real que los motiva: los horarios se generaron una vez para 14 dias,
nadie los volvio a generar, y el bot termino ofreciendo solo "lunes y martes"
sin dejar agendar la semana siguiente.
"""

import asyncio
import datetime
from unittest.mock import AsyncMock, patch

import pytest

from sidhe_agent.services import agenda

ONCE = datetime.time(11, 0)
NUEVE = datetime.time(21, 0)
LUNES = datetime.date(2026, 9, 14)


def _semanas(n: int) -> list[datetime.date]:
    return [LUNES + datetime.timedelta(days=d) for d in range(7 * n)]


def test_un_stand_de_11_a_21_tiene_10_horarios():
    """Coincide con lo que ve el cliente: '10 horarios disponibles'."""
    bloques = agenda.horas_del_dia(ONCE, NUEVE, 60)
    assert len(bloques) == 10
    assert bloques[0] == (datetime.time(11, 0), datetime.time(12, 0))
    assert bloques[-1] == (datetime.time(20, 0), datetime.time(21, 0))


def test_no_se_crea_un_bloque_que_se_pase_del_cierre():
    bloques = agenda.horas_del_dia(ONCE, datetime.time(12, 30), 60)
    assert bloques == [(datetime.time(11, 0), datetime.time(12, 0))]


def test_rellena_la_semana_siguiente_completa():
    """El bug: con la agenda vieja, la siguiente semana no existia."""
    nuevos = agenda.slots_faltantes(
        sucursal_id=1, apertura=ONCE, cierre=NUEVE, dias_operacion=None,
        fechas=_semanas(3), existentes=set(), minutos=60,
    )
    fechas = {n["fecha"] for n in nuevos}
    assert len(fechas) == 21
    assert LUNES + datetime.timedelta(days=7) in fechas   # lunes siguiente
    assert LUNES + datetime.timedelta(days=13) in fechas  # domingo siguiente
    assert len(nuevos) == 21 * 10


def test_nunca_duplica_ni_toca_lo_que_ya_existe():
    """Incluye los reservados: una cita agendada no se puede pisar."""
    existentes = {(LUNES, datetime.time(h, 0)) for h in range(11, 21)}
    nuevos = agenda.slots_faltantes(
        sucursal_id=1, apertura=ONCE, cierre=NUEVE, dias_operacion=None,
        fechas=[LUNES, LUNES + datetime.timedelta(days=1)],
        existentes=existentes, minutos=60,
    )
    assert all(n["fecha"] != LUNES for n in nuevos)
    assert len(nuevos) == 10  # solo el martes


def test_correrlo_dos_veces_no_crea_nada_la_segunda():
    primera = agenda.slots_faltantes(
        sucursal_id=1, apertura=ONCE, cierre=NUEVE, dias_operacion=None,
        fechas=_semanas(1), existentes=set(), minutos=60,
    )
    ya_hechos = {(n["fecha"], n["hora_inicio"]) for n in primera}
    segunda = agenda.slots_faltantes(
        sucursal_id=1, apertura=ONCE, cierre=NUEVE, dias_operacion=None,
        fechas=_semanas(1), existentes=ya_hechos, minutos=60,
    )
    assert segunda == []


def test_respeta_los_dias_que_cierra_el_stand():
    lunes_a_sabado = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"]
    nuevos = agenda.slots_faltantes(
        sucursal_id=1, apertura=ONCE, cierre=NUEVE, dias_operacion=lunes_a_sabado,
        fechas=_semanas(1), existentes=set(), minutos=60,
    )
    domingo = LUNES + datetime.timedelta(days=6)
    assert domingo not in {n["fecha"] for n in nuevos}
    assert len({n["fecha"] for n in nuevos}) == 6


def test_cada_horario_nuevo_nace_libre():
    nuevos = agenda.slots_faltantes(
        sucursal_id=7, apertura=ONCE, cierre=NUEVE, dias_operacion=None,
        fechas=[LUNES], existentes=set(), minutos=60,
    )
    assert all(n["reservados"] == 0 and n["capacidad"] == 1 for n in nuevos)
    assert all(n["sucursal_id"] == 7 for n in nuevos)


async def test_la_agenda_se_sigue_rellenando_aunque_falle_una_vez():
    intentos = []

    async def falla_y_luego_funciona(dias, minutos):
        intentos.append(dias)
        if len(intentos) == 1:
            raise RuntimeError("la base no responde")
        return 0

    with patch.object(
        agenda, "asegurar_slots", AsyncMock(side_effect=falla_y_luego_funciona)
    ):
        tarea = asyncio.create_task(
            agenda.mantener_agenda_abierta(
                dias=21, minutos=60, cada_horas=0.0001, espera_inicial=0
            )
        )
        await asyncio.sleep(0.5)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea

    assert len(intentos) > 1, "un fallo detuvo el relleno de la agenda"
    assert intentos[0] == 21


async def test_con_cero_dias_la_agenda_automatica_no_arranca():
    with patch.object(agenda, "asegurar_slots", AsyncMock()) as rellenar:
        await agenda.mantener_agenda_abierta(dias=0, minutos=60, espera_inicial=0)
    rellenar.assert_not_awaited()
