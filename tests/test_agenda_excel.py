"""Agendar desde el Excel: una fila, una cita, y nunca dos.

Lo que cuidan estos tests, en orden de lo caro que sale fallar:
1. Una fila ya agendada no se vuelve a agendar cada minuto.
2. Una fila con error no se reintenta en bucle, pero sí en cuanto la
   corrigen.
3. Fechas, horas y teléfonos se entienden como los escribe la gente, y
   como los devuelve Sheets (números de serie).
"""

import datetime

from sidhe_agent.services import agenda_excel as ax
from sidhe_agent.services.agenda_excel import Horario, procesar_fila


def fila(**campos) -> list:
    base = {
        "sucursal": "Liverpool Polanco", "fecha": "29/09/2026", "hora": "12:00",
        "paciente": "Juan Pérez", "telefono": "55 1234 5678", "recordatorio": "Sí",
        "cancelar": False, "estado": "", "folio": "", "control": "",
    }
    base.update(campos)
    return list(base.values())


class Base:
    """La base de datos, de mentira: anota lo que se le pidió."""

    def __init__(self, slot_id=7, sucursal="Liverpool Polanco", libres=("13:00", "16:00"),
                 agendar=None, cancelar=None):
        self.horario = Horario(slot_id, sucursal, list(libres))
        self._agendar = agendar or {"folio": 1843}
        self._cancelar = cancelar or {"ok": True, "folio": 1843}
        self.agendadas = []
        self.canceladas = []

    async def buscar(self, sucursal, fecha, hora):
        self.buscado = (sucursal, fecha, hora)
        return self.horario

    async def agendar(self, slot_id, paciente, telefono, canal):
        self.agendadas.append((slot_id, paciente, telefono, canal))
        return self._agendar

    async def cancelar(self, folio, telefono):
        self.canceladas.append((folio, telefono))
        return self._cancelar


async def correr(f, base):
    return await procesar_fila(f, base.buscar, base.agendar, base.cancelar)


# --- el camino feliz ---

async def test_una_fila_nueva_se_agenda_y_contesta_con_folio():
    base = Base()
    r = await correr(fila(), base)

    assert r.folio == "1843"
    assert r.estado.startswith("✅ Agendada")
    assert base.agendadas == [(7, "Juan Pérez", "+5215512345678", ax.CANAL_SUCURSAL)]
    assert base.buscado[1] == datetime.date(2026, 9, 29)
    assert base.buscado[2] == datetime.time(12, 0)


async def test_sin_aceptar_recordatorio_la_cita_no_lo_recibe():
    """WhatsApp exige que el paciente haya aceptado; vacío es no."""
    for respuesta in ("No", ""):
        base = Base()
        await correr(fila(recordatorio=respuesta), base)
        assert base.agendadas[0][3] == ax.CANAL_SUCURSAL_SIN_RECORDATORIO


# --- nunca dos veces ---

async def test_una_fila_con_folio_no_se_vuelve_a_agendar():
    base = Base()
    assert await correr(fila(folio="1843", estado="✅ Agendada"), base) is None
    assert base.agendadas == []


async def test_una_fila_con_error_no_se_reintenta_igual_cada_minuto():
    base = Base(slot_id=None)
    primera = await correr(fila(), base)
    assert primera.estado.startswith("❌")

    # Siguiente vuelta: la fila trae el control que escribimos
    otra = fila(estado=primera.estado, control=primera.control)
    assert await correr(otra, Base()) is None


async def test_en_cuanto_la_corrigen_se_vuelve_a_intentar():
    base = Base(slot_id=None)
    primera = await correr(fila(hora="10:00"), base)

    corregida = fila(hora="13:00", estado=primera.estado, control=primera.control)
    otra_base = Base()
    r = await correr(corregida, otra_base)
    assert r.folio == "1843"


async def test_una_fila_vacia_no_hace_nada():
    assert await correr([""] * 10, Base()) is None
    assert await correr([], Base()) is None


# --- errores que tiene que entender la sucursal ---

async def test_si_el_bot_ya_dio_ese_horario_dice_cuales_quedan():
    base = Base(agendar={"error": "slot_no_disponible", "alternativas": []})
    r = await correr(fila(), base)
    assert r.estado == "❌ Ocupado. Libres ese día: 13:00, 16:00"
    assert r.folio == ""


async def test_un_horario_que_no_existe_ofrece_los_libres():
    r = await correr(fila(hora="10:00"), Base(slot_id=None))
    assert "No hay horario a las 10:00" in r.estado
    assert "13:00, 16:00" in r.estado


async def test_si_ya_no_queda_nada_ese_dia_lo_dice():
    r = await correr(fila(), Base(agendar={"error": "slot_no_disponible"}, libres=()))
    assert "ya no quedan horarios" in r.estado


async def test_falta_de_datos_se_nombra():
    r = await correr(fila(paciente="", telefono=""), Base())
    assert r.estado == "❌ Falta: paciente, teléfono"


async def test_sucursal_que_no_existe():
    r = await correr(fila(sucursal="Coapa"), Base(slot_id=None, sucursal=None))
    assert "No reconozco la sucursal" in r.estado


async def test_horario_pasado_y_sin_personal():
    r = await correr(fila(), Base(agendar={"error": "horario_pasado"}))
    assert r.estado == "❌ Ese horario ya pasó"
    r = await correr(fila(), Base(agendar={"error": "sin_personal_ese_dia"}))
    assert "no hay personal" in r.estado


async def test_telefono_corto():
    r = await correr(fila(telefono="551234"), Base())
    assert "10 dígitos" in r.estado


# --- cancelar ---

async def test_marcar_cancelar_en_una_fila_con_folio_la_cancela():
    base = Base()
    r = await correr(fila(folio="1843", estado="✅ Agendada", cancelar=True), base)
    assert r.estado == "🚫 Cancelada"
    assert base.canceladas == [(1843, "+5215512345678")]


async def test_una_cancelada_no_se_cancela_otra_vez():
    base = Base()
    assert await correr(fila(folio="1843", estado="🚫 Cancelada", cancelar=True), base) is None
    assert base.canceladas == []


async def test_si_el_folio_y_el_telefono_no_coinciden_se_avisa():
    base = Base(cancelar={"error": "cita_no_encontrada"})
    r = await correr(fila(folio="1843", estado="✅", cancelar=True), base)
    assert "no coinciden" in r.estado


# --- como escribe la gente y como devuelve Sheets ---

def test_fechas():
    assert ax.leer_fecha("29/09/2026") == datetime.date(2026, 9, 29)
    assert ax.leer_fecha("29/09/26") == datetime.date(2026, 9, 29)
    assert ax.leer_fecha("2026-09-29") == datetime.date(2026, 9, 29)
    # Sheets devuelve las fechas como número de serie
    assert ax.leer_fecha(46294) == datetime.date(2026, 9, 29)
    assert ax.leer_fecha("mañana") is None


def test_horas():
    assert ax.leer_hora("12:00") == datetime.time(12, 0)
    assert ax.leer_hora("16") == datetime.time(16, 0)
    assert ax.leer_hora("4 pm") == datetime.time(16, 0)
    assert ax.leer_hora("4:30 p.m.") == datetime.time(16, 30)
    assert ax.leer_hora("16:00 hrs") == datetime.time(16, 0)
    # Sheets devuelve las horas como fracción del día
    assert ax.leer_hora(0.5) == datetime.time(12, 0)
    assert ax.leer_hora("a mediodía") is None


def test_telefonos_quedan_como_los_de_whatsapp():
    """Así, si el paciente escribe después al bot, encuentra su cita."""
    assert ax.leer_telefono("55 1234 5678") == "+5215512345678"
    assert ax.leer_telefono("+52 1 55 1234 5678") == "+5215512345678"
    assert ax.leer_telefono(5512345678) == "+5215512345678"
    assert ax.leer_telefono(5512345678.0) == "+5215512345678"
    assert ax.leer_telefono("12345") is None


def test_casillas_y_si():
    assert ax.dijo_que_si(True) and ax.dijo_que_si("Sí") and ax.dijo_que_si("si")
    assert not ax.dijo_que_si(False) and not ax.dijo_que_si("") and not ax.dijo_que_si("No")
