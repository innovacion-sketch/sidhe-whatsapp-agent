"""Escalar sin prometer lo que el bot no controla.

Antes: "un asesor te contactará pronto" siempre, también un domingo a las
11 de la noche, y también cuando el escalamiento ni se había guardado. Y
mientras esperaba, el cliente escribía "¿hola?" y no recibía nada.
"""

import datetime
import types
from zoneinfo import ZoneInfo

import pytest

from sidhe_agent import main
from sidhe_agent.channels.schemas import IncomingMessage
from sidhe_agent.services import horario_asesores as h
from sidhe_agent.tools.escalamiento import instruccion_para_el_cliente

MX = ZoneInfo("America/Mexico_City")
LUN_A_SAB = "lunes|martes|miercoles|jueves|viernes|sabado"


def en(dia: int, hora: int, minuto: int = 0) -> datetime.datetime:
    """Un momento de la semana del 21 de sep de 2026 (lunes 21 = dia 0)."""
    return datetime.datetime(2026, 9, 21 + dia, hora, minuto, tzinfo=MX)


# --- horario ---

def test_dentro_del_horario():
    assert h.en_horario(en(1, 12), 10, 19, LUN_A_SAB)


def test_fuera_por_la_hora_y_por_el_dia():
    assert not h.en_horario(en(1, 9, 59), 10, 19, LUN_A_SAB)
    assert not h.en_horario(en(1, 19), 10, 19, LUN_A_SAB)
    assert not h.en_horario(en(6, 12), 10, 19, LUN_A_SAB)  # domingo


def test_de_noche_contestan_manana():
    assert h.cuando_contestan(en(1, 23), 10, 19, LUN_A_SAB) == "mañana a partir de las 10:00"


def test_de_madrugada_contestan_hoy():
    assert h.cuando_contestan(en(1, 3), 10, 19, LUN_A_SAB) == "hoy a partir de las 10:00"


def test_el_sabado_en_la_noche_contestan_el_lunes():
    """Domingo no hay: decir "mañana" sería volver a prometer de más."""
    assert h.cuando_contestan(en(5, 22), 10, 19, LUN_A_SAB) == "el lunes a partir de las 10:00"


def test_en_horario_no_hay_cuando():
    assert h.cuando_contestan(en(2, 12), 10, 19, LUN_A_SAB) == ""


def test_el_horario_se_dice_como_lo_diria_una_persona():
    assert h.horario_legible(10, 19, LUN_A_SAB) == "de lunes a sábado de 10:00 a 19:00"
    assert h.horario_legible(9, 18, "") == "todos los días de 9:00 a 18:00"


# --- la promesa del escalamiento ---

def test_en_horario_se_puede_decir_en_breve(monkeypatch):
    monkeypatch.setattr(h, "cuando_contestan", lambda m: "")
    assert "en breve" in instruccion_para_el_cliente(True, en(1, 12))


def test_fuera_de_horario_se_dice_cuando_y_se_prohibe_pronto(monkeypatch):
    monkeypatch.setattr(h, "cuando_contestan", lambda m: "mañana a partir de las 10:00")
    instruccion = instruccion_para_el_cliente(True, en(1, 23))

    assert "mañana a partir de las 10:00" in instruccion
    assert "NO digas 'pronto'" in instruccion
    assert "TELÉFONO" in instruccion


def test_si_no_se_guardo_no_se_promete_nada():
    """Nadie se va a enterar: prometer un asesor sería mentirle."""
    instruccion = instruccion_para_el_cliente(False, en(1, 12))

    assert "NO le digas" in instruccion
    assert "TELÉFONO" in instruccion


# --- el acuse durante la pausa ---

class _Adaptador:
    canal = "whatsapp"

    def __init__(self):
        self.enviados = []

    async def send(self, user_id, mensaje):
        self.enviados.append(mensaje.texto)
        return "SID"


def _app(adaptador):
    return types.SimpleNamespace(state=types.SimpleNamespace(
        adapter=adaptador, adapters={"whatsapp": adaptador},
    ))


def _msg(texto):
    return IncomingMessage(canal="whatsapp", user_id="+521", tipo="texto", contenido=texto)


@pytest.fixture
def sin_base(monkeypatch):
    guardados = []

    async def _guardar(*args, **kwargs):
        guardados.append(args)

    monkeypatch.setattr(main, "_guardar_mensaje", _guardar)
    return guardados


async def test_al_que_escribe_durante_la_pausa_se_le_avisa(monkeypatch, sin_base):
    async def _toca(*a):
        return True

    monkeypatch.setattr(main.conversaciones, "toca_acusar_la_pausa", _toca)
    adaptador = _Adaptador()

    await main._acusar_pausa(_app(adaptador), _msg("¿hola?"), main.logger)

    assert len(adaptador.enviados) == 1
    assert "ya lo tiene un asesor" in adaptador.enviados[0]
    # Se guarda con su tipo: así la segunda vez ya no se manda
    assert sin_base[0][3] == main.conversaciones.TIPO_ACUSE_PAUSA


async def test_si_ya_se_aviso_o_ya_contesto_alguien_no_se_repite(monkeypatch, sin_base):
    async def _no_toca(*a):
        return False

    monkeypatch.setattr(main.conversaciones, "toca_acusar_la_pausa", _no_toca)
    adaptador = _Adaptador()

    await main._acusar_pausa(_app(adaptador), _msg("¿hola?"), main.logger)

    assert adaptador.enviados == []


async def test_a_un_gracias_no_se_le_contesta_que_esperamos(monkeypatch, sin_base):
    async def _toca(*a):
        return True

    monkeypatch.setattr(main.conversaciones, "toca_acusar_la_pausa", _toca)
    adaptador = _Adaptador()

    await main._acusar_pausa(_app(adaptador), _msg("gracias"), main.logger)

    assert adaptador.enviados == []


async def test_si_falla_el_aviso_la_pausa_sigue_igual(monkeypatch, sin_base):
    async def _revienta(*a):
        raise RuntimeError("base caída")

    monkeypatch.setattr(main.conversaciones, "toca_acusar_la_pausa", _revienta)

    await main._acusar_pausa(_app(_Adaptador()), _msg("¿hola?"), main.logger)


def test_fuera_de_horario_el_aviso_dice_cuando(monkeypatch):
    monkeypatch.setattr(h, "cuando_contestan", lambda m: "el lunes a partir de las 10:00")
    assert "el lunes a partir de las 10:00" in main.texto_acuse_pausa(en(5, 22))
