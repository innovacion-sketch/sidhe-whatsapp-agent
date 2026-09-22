"""Enlace con el sistema de asistencias.

La regla que se prueba aquí: sin datos NO se bloquea la agenda. Quedarse
sin citas porque el rol se subió tarde es peor que una cita sin personal,
que al menos dispara un aviso.
"""

import datetime
from unittest.mock import AsyncMock, patch

from sidhe_agent.services import alertas_citas, asistencias
from sidhe_agent.services.agenda import slots_faltantes

HOY = datetime.date(2026, 9, 22)
MANANA = datetime.date(2026, 9, 23)


def test_la_clave_con_caracteres_raros_no_rompe_la_conexion():
    """Una contraseña con @ o # solo se ve como 'password authentication failed'."""
    from sidhe_agent.config import Settings

    ajustes = Settings(
        asistencias_db_host="one_postgres",
        asistencias_db_user="asistencias_user",
        asistencias_db_password="Cl@ve#con/raros:1",
        asistencias_db_name="asistencias",
    )
    url = ajustes.asistencias_url

    assert url.startswith("postgresql+asyncpg://asistencias_user:")
    assert "@one_postgres:5432/asistencias" in url
    # El @ de la contraseña va codificado; el único @ literal separa el host
    assert url.count("@") == 1
    assert "Cl%40ve%23con%2Fraros%3A1" in url


def test_sin_datos_no_hay_url_de_asistencias():
    from sidhe_agent.config import Settings

    assert Settings().asistencias_url == ""


def test_el_dsn_completo_gana_si_se_da():
    from sidhe_agent.config import Settings

    ajustes = Settings(
        asistencias_database_url="postgres://u:p@host:5432/asistencias",
        asistencias_db_host="otro",
    )
    assert "postgresql+asyncpg://u:p@host:5432/asistencias" == ajustes.asistencias_url


async def test_sin_sistema_configurado_no_bloquea_nada():
    with patch.object(asistencias, "configurado", return_value=False):
        assert await asistencias.dias_sin_personal(HOY, MANANA) == set()


async def test_si_la_consulta_falla_tampoco_bloquea():
    """Una caída de la base de asistencias no puede dejar sin agenda al bot."""
    with (
        patch.object(asistencias, "configurado", return_value=True),
        patch.object(asistencias, "_motor", side_effect=RuntimeError("sin red")),
    ):
        assert await asistencias.dias_sin_personal(HOY, MANANA) == set()


async def test_si_la_consulta_falla_no_se_inventa_una_alerta():
    """None = no sé. Distinto de un conjunto vacío, que sí es 'todo cubierto'."""
    with (
        patch.object(asistencias, "configurado", return_value=True),
        patch.object(asistencias, "_motor", side_effect=RuntimeError("sin red")),
    ):
        assert await asistencias.sucursales_sin_checada(HOY, datetime.time(12)) is None


def test_los_dias_sin_personal_no_generan_horarios():
    """El filtro se aplica sobre las fechas, antes de crear los slots."""
    fechas = [HOY, MANANA]
    sin_personal = {("Liverpool Perisur", MANANA)}
    aptas = [f for f in fechas if ("Liverpool Perisur", f) not in sin_personal]

    slots = slots_faltantes(
        sucursal_id=1,
        apertura=datetime.time(11, 0),
        cierre=datetime.time(13, 0),
        dias_operacion=["lunes", "martes", "miercoles", "jueves", "viernes",
                        "sabado", "domingo"],
        fechas=aptas,
        existentes=set(),
        minutos=60,
    )
    assert {s["fecha"] for s in slots} == {HOY}


def test_el_correo_lista_a_quien_hay_que_hablarle():
    citas = [
        {"hora": "13:00", "cliente": "Dulce Estrada", "telefono": "+5215534462206", "folio": 29},
        {"hora": "15:00", "cliente": "Iván Cantoran", "telefono": "+5215580805212", "folio": 26},
    ]
    asunto, cuerpo = alertas_citas.redactar("Liverpool Coapa", HOY, citas, "11:30")

    assert "Liverpool Coapa" in asunto and "2 cita" in asunto
    # Lo que necesita quien recibe el correo: a quién llamar
    assert "+5215534462206" in cuerpo and "Iván Cantoran" in cuerpo
    assert "folio 29" in cuerpo and "11:30" in cuerpo


async def test_no_avisa_dos_veces_por_la_misma_sucursal_el_mismo_dia():
    alertas_citas._avisadas.clear()
    citas = {"Liverpool Coapa": [{"hora": "13:00", "cliente": "A", "telefono": "1", "folio": 1}]}
    with (
        patch.object(alertas_citas, "citas_de_hoy", AsyncMock(return_value=citas)),
        patch.object(
            alertas_citas.asistencias,
            "sucursales_sin_checada",
            AsyncMock(return_value={"Liverpool Coapa"}),
        ),
        patch.object(alertas_citas.correo, "enviar", AsyncMock(return_value=True)) as enviar,
    ):
        assert await alertas_citas.revisar() == 1
        assert await alertas_citas.revisar() == 0

    assert enviar.await_count == 1
    alertas_citas._avisadas.clear()


async def test_no_avisa_de_sucursales_sin_citas():
    """Que el stand no abra ya lo alerta el sistema de asistencias."""
    alertas_citas._avisadas.clear()
    citas = {"Liverpool Perisur": [{"hora": "13:00", "cliente": "A", "telefono": "1", "folio": 1}]}
    with (
        patch.object(alertas_citas, "citas_de_hoy", AsyncMock(return_value=citas)),
        patch.object(
            alertas_citas.asistencias,
            "sucursales_sin_checada",
            AsyncMock(return_value={"Liverpool Coapa"}),
        ),
        patch.object(alertas_citas.correo, "enviar", AsyncMock(return_value=True)) as enviar,
    ):
        assert await alertas_citas.revisar() == 0

    enviar.assert_not_awaited()
