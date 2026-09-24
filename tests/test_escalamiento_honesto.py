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
# El horario real del equipo
SIDHE = "lunes-viernes 10-18; sabado-domingo 10-13"


def en(dia: int, hora: int, minuto: int = 0) -> datetime.datetime:
    """Un momento de la semana del 21 de sep de 2026 (lunes 21 = dia 0)."""
    return datetime.datetime(2026, 9, 21 + dia, hora, minuto, tzinfo=MX)


# --- leer el horario ---

def test_se_lee_como_se_diria():
    horario = h.leer_horario(SIDHE)
    assert horario[0] == (datetime.time(10), datetime.time(18))  # lunes
    assert horario[4] == (datetime.time(10), datetime.time(18))  # viernes
    assert horario[5] == (datetime.time(10), datetime.time(13))  # sabado
    assert horario[6] == (datetime.time(10), datetime.time(13))  # domingo


def test_acepta_dias_sueltos_minutos_y_acentos():
    horario = h.leer_horario("miércoles,sábado 9:30-14")
    assert set(horario) == {2, 5}
    assert horario[2][0] == datetime.time(9, 30)


def test_un_horario_mal_escrito_no_arranca():
    """Leerlo mal haria que el bot le diga a los clientes una hora falsa."""
    for malo in ["lunes 18-10", "lunes-jueves", "lunez 10-18", "10-18"]:
        with pytest.raises(ValueError):
            h.leer_horario(malo)


# --- en horario ---

def test_entre_semana_hasta_las_6():
    assert h.en_horario(en(1, 17, 59), SIDHE)
    assert not h.en_horario(en(1, 18), SIDHE)
    assert not h.en_horario(en(1, 9, 59), SIDHE)


def test_fin_de_semana_solo_hasta_la_1():
    assert h.en_horario(en(5, 12, 30), SIDHE)
    assert not h.en_horario(en(5, 13), SIDHE)
    assert not h.en_horario(en(6, 15), SIDHE)


# --- cuando contestan ---

def test_entre_semana_de_noche_contestan_manana():
    assert h.cuando_contestan(en(1, 23), SIDHE) == "mañana a partir de las 10:00"


def test_de_madrugada_contestan_hoy():
    assert h.cuando_contestan(en(1, 3), SIDHE) == "hoy a partir de las 10:00"


def test_el_sabado_en_la_tarde_contestan_el_domingo():
    """A las 3 del sabado ya no hay nadie, pero el domingo si abren."""
    assert h.cuando_contestan(en(5, 15), SIDHE) == "mañana a partir de las 10:00"


def test_el_domingo_en_la_tarde_contestan_el_lunes():
    assert h.cuando_contestan(en(6, 14), SIDHE) == "mañana a partir de las 10:00"


def test_en_horario_no_hay_cuando():
    assert h.cuando_contestan(en(2, 12), SIDHE) == ""


def test_el_horario_se_dice_como_lo_diria_una_persona():
    assert h.horario_legible(SIDHE) == (
        "de lunes a viernes de 10:00 a 18:00, y sábados y domingos de 10:00 a 13:00"
    )
    assert h.horario_legible("sabado 10-14") == "los sábados de 10:00 a 14:00"


# --- horas de atencion para reactivar al bot ---

def test_de_noche_no_corren_las_horas():
    """Viernes 5:55 pm → sabado 10 am: solo cinco minutos en que habia alguien."""
    horas = h.horas_habiles_entre(en(4, 17, 55), en(5, 10), SIDHE)
    assert abs(horas - 5 / 60) < 1e-6


def test_el_fin_de_semana_corto_cuenta_lo_que_es():
    """Sabado 12 → lunes 11: 1 h del sabado + 3 del domingo + 1 del lunes."""
    assert h.horas_habiles_entre(en(5, 12), en(7, 11), SIDHE) == pytest.approx(5.0)


def test_la_base_guarda_en_utc_y_se_cuenta_en_hora_de_mexico():
    """Sin convertir, las 10 de la manana serian las 4 de la madrugada."""
    desde_utc = en(1, 10).astimezone(datetime.timezone.utc)
    assert h.horas_habiles_entre(desde_utc, en(1, 14), SIDHE) == pytest.approx(4.0)


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
