"""Días de descanso fijos por sucursal y retiro de horarios ya generados."""

import csv
import datetime
from pathlib import Path

from sidhe_agent.services.agenda import DIAS_SEMANA, dias_a_retirar

RUTA_CSV = Path(__file__).parent.parent / "data" / "sucursales.csv"

# Lunes 21 a domingo 27 de septiembre de 2026
SEMANA = [datetime.date(2026, 9, 21) + datetime.timedelta(days=n) for n in range(7)]
LUNES, MARTES, MIERCOLES, JUEVES = SEMANA[0], SEMANA[1], SEMANA[2], SEMANA[3]


def _dias_de(nombre: str) -> list[str]:
    with RUTA_CSV.open(encoding="utf-8") as archivo:
        for fila in csv.DictReader(archivo):
            if fila["nombre"].strip() == nombre:
                return [d for d in fila["dias_operacion"].split("|") if d]
    raise AssertionError(f"no está {nombre} en el CSV")


def test_tijuana_descansa_lunes_y_jueves():
    dias = _dias_de("Liverpool Península Tijuana")
    assert "lunes" not in dias and "jueves" not in dias
    assert {"martes", "miercoles", "viernes", "sabado", "domingo"} == set(dias)


def test_monterrey_descansa_lunes_y_miercoles():
    dias = _dias_de("Liverpool Galerías Monterrey")
    assert "lunes" not in dias and "miercoles" not in dias
    assert {"martes", "jueves", "viernes", "sabado", "domingo"} == set(dias)


def test_las_demas_siguen_abriendo_todos_los_dias():
    for nombre in ("Liverpool Perisur", "Liverpool Polanco"):
        assert set(_dias_de(nombre)) == set(DIAS_SEMANA)


def test_se_retiran_los_dias_de_descanso():
    retirar = dias_a_retirar(
        "Liverpool Península Tijuana",
        SEMANA,
        set(),
        ["martes", "miercoles", "viernes", "sabado", "domingo"],
    )
    assert retirar == [LUNES, JUEVES]


def test_se_retiran_tambien_los_dias_que_cerro_el_rol():
    retirar = dias_a_retirar(
        "Liverpool Galerías Monterrey",
        SEMANA,
        {("Liverpool Galerías Monterrey", MARTES)},
        ["martes", "jueves", "viernes", "sabado", "domingo"],
    )
    assert retirar == [LUNES, MARTES, MIERCOLES]


def test_una_lista_vacia_significa_abre_todos_los_dias():
    """Lo contrario borraría la agenda completa de esa sucursal."""
    assert dias_a_retirar("Liverpool Perisur", SEMANA, set(), []) == []
    assert dias_a_retirar("Liverpool Perisur", SEMANA, set(), None) == []


def test_el_bot_le_avisa_al_cliente_que_esa_sucursal_descansa():
    """Si no, mandaría a alguien un lunes a una sucursal cerrada."""
    from types import SimpleNamespace

    from sidhe_agent.tools.sucursales import _a_dict, dias_de_descanso

    assert dias_de_descanso(["martes", "miercoles", "viernes", "sabado", "domingo"]) == (
        "lunes y jueves"
    )
    assert dias_de_descanso(["lunes", "martes", "miercoles", "jueves", "viernes",
                             "sabado"]) == "domingo"
    assert dias_de_descanso(None) == ""
    assert dias_de_descanso(DIAS_SEMANA) == ""

    tijuana = SimpleNamespace(
        id=17, nombre="Liverpool Península Tijuana", ciudad="Tijuana",
        zona="Noroeste", direccion="...", telefono="664...",
        horario_apertura=datetime.time(11), horario_cierre=datetime.time(21),
        dias_operacion=["martes", "miercoles", "viernes", "sabado", "domingo"],
    )
    datos = _a_dict(tijuana)
    assert datos["descansa"] == "lunes y jueves"
    assert "lunes y jueves" in datos["que_decir"]

    # En las que abren todos los días no aparece el campo
    perisur = SimpleNamespace(**{**tijuana.__dict__, "dias_operacion": DIAS_SEMANA})
    assert "descansa" not in _a_dict(perisur)


def test_una_cita_fuera_del_turno_no_tiene_quien_la_atienda():
    """Hay gente ese día, pero la cita cae cuando ya se fueron."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from citas_sin_personal import clasificar

    from sidhe_agent.services.asistencias import hay_quien_atienda

    turno = [(datetime.time(11, 0), datetime.time(17, 0))]
    assert hay_quien_atienda(turno, datetime.time(13, 0))
    assert not hay_quien_atienda(turno, datetime.time(19, 0))

    comunes = dict(nombre="Liverpool Perisur", fecha=MARTES, dias_operacion=None,
                   sin_personal=set(), turnos={("Liverpool Perisur", MARTES): turno})
    assert clasificar(hora=datetime.time(13, 0), **comunes) == "OK"
    assert clasificar(hora=datetime.time(19, 0), **comunes) == "FUERA"


def test_sin_rol_cargado_no_se_confunde_con_sin_personal():
    """Distinguirlos importa: uno es un problema y el otro es no saber."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from citas_sin_personal import clasificar

    comunes = dict(nombre="Liverpool Perisur", fecha=MARTES,
                   hora=datetime.time(13, 0), dias_operacion=None)
    assert clasificar(sin_personal=set(), turnos={}, **comunes) == "SIN ROL"
    assert clasificar(sin_personal=set(), turnos=None, **comunes) == "SIN ROL"
    assert clasificar(
        sin_personal={("Liverpool Perisur", MARTES)}, turnos={}, **comunes
    ) == "CERRADO"


def test_el_rol_de_otra_sucursal_no_afecta():
    retirar = dias_a_retirar(
        "Liverpool Perisur", SEMANA, {("Liverpool Coapa", MARTES)}, None
    )
    assert retirar == []
