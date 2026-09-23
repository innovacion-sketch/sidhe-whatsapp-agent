"""Solo se ofrecen horas que alguien pueda atender.

La tienda abre de 11 a 21, pero los turnos son más cortos. Ofrecer las 11
cuando el turno empieza a las 12 manda al cliente a un stand vacío.
"""

import datetime

from sidhe_agent.services.agenda import (
    hay_turno_que_cubra,
    horas_del_dia,
    horas_ofrecibles,
    slots_faltantes,
)

DIA = datetime.date(2026, 9, 24)  # jueves
TODOS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
BLOQUES = horas_del_dia(datetime.time(11), datetime.time(21), 60)
TURNO_CORTO = [(datetime.time(12), datetime.time(17))]


def test_un_bloque_cuenta_solo_si_cabe_completo():
    """16:30 a 17:30 con el turno terminando a las 17 deja al cliente a medias."""
    assert hay_turno_que_cubra(TURNO_CORTO, datetime.time(13), datetime.time(14))
    assert not hay_turno_que_cubra(TURNO_CORTO, datetime.time(16, 30), datetime.time(17, 30))
    assert not hay_turno_que_cubra(TURNO_CORTO, datetime.time(11), datetime.time(12))


def test_solo_se_ofrecen_las_horas_del_turno():
    ofrecibles = horas_ofrecibles(BLOQUES, TURNO_CORTO)
    assert [b[0].strftime("%H:%M") for b in ofrecibles] == [
        "12:00", "13:00", "14:00", "15:00", "16:00",
    ]


def test_dos_turnos_cubren_sus_dos_ventanas():
    turnos = [
        (datetime.time(11), datetime.time(14)),
        (datetime.time(18), datetime.time(21)),
    ]
    horas = [b[0].strftime("%H:%M") for b in horas_ofrecibles(BLOQUES, turnos)]
    assert horas == ["11:00", "12:00", "13:00", "18:00", "19:00", "20:00"]


def test_sin_rol_cargado_se_ofrece_el_horario_completo():
    """Recortar por falta de datos vaciaría la agenda."""
    assert horas_ofrecibles(BLOQUES, None) == BLOQUES
    assert horas_ofrecibles(BLOQUES, []) == BLOQUES


def test_los_slots_nuevos_respetan_el_turno():
    nuevos = slots_faltantes(
        sucursal_id=1,
        apertura=datetime.time(11),
        cierre=datetime.time(21),
        dias_operacion=TODOS,
        fechas=[DIA],
        existentes=set(),
        minutos=60,
        turnos={DIA: TURNO_CORTO},
    )
    assert [s["hora_inicio"].strftime("%H:%M") for s in nuevos] == [
        "12:00", "13:00", "14:00", "15:00", "16:00",
    ]


def test_un_dia_sin_turnos_en_el_mapa_se_ofrece_completo():
    nuevos = slots_faltantes(
        sucursal_id=1,
        apertura=datetime.time(11),
        cierre=datetime.time(21),
        dias_operacion=TODOS,
        fechas=[DIA],
        existentes=set(),
        minutos=60,
        turnos={DIA: []},
    )
    assert len(nuevos) == len(BLOQUES) == 10
