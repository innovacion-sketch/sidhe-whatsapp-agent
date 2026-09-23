"""El repaso de los lunes: cuándo se manda y qué dice."""

import datetime
from unittest.mock import AsyncMock, patch

from sidhe_agent.services import alertas_citas
from sidhe_agent.services.alertas_citas import (
    redactar_revision,
    toca_revision_semanal,
)

TZ = datetime.timezone(datetime.timedelta(hours=-6))
LUNES_9 = datetime.datetime(2026, 9, 28, 9, 5, tzinfo=TZ)
LUNES_8 = datetime.datetime(2026, 9, 28, 8, 30, tzinfo=TZ)
MARTES_9 = datetime.datetime(2026, 9, 29, 9, 5, tzinfo=TZ)

PROBLEMAS = [
    {
        "estado": "FUERA", "fecha": datetime.date(2026, 9, 30), "hora": "11:00",
        "sucursal": "Liverpool Polanco", "cliente": "Gustavo Silva",
        "telefono": "+5215535109140", "folio": 8,
    },
    {
        "estado": "SIN ROL", "fecha": datetime.date(2026, 10, 5), "hora": "13:00",
        "sucursal": "Liverpool Perisur", "cliente": "Josefa Martínez",
        "telefono": "+5215510486659", "folio": 67,
    },
]


def test_se_manda_el_lunes_pasada_la_hora():
    assert toca_revision_semanal(LUNES_9, None, 0, 9) is True


def test_no_se_manda_antes_de_la_hora():
    assert toca_revision_semanal(LUNES_8, None, 0, 9) is False


def test_no_se_manda_otro_dia():
    assert toca_revision_semanal(MARTES_9, None, 0, 9) is False


def test_no_se_repite_el_mismo_lunes():
    """El ciclo despierta cada media hora; el correo sale una vez."""
    assert toca_revision_semanal(LUNES_9, LUNES_9.date(), 0, 9) is False
    tarde = LUNES_9.replace(hour=18)
    assert toca_revision_semanal(tarde, LUNES_9.date(), 0, 9) is False


def test_el_lunes_siguiente_si_se_manda_otra_vez():
    lunes_siguiente = LUNES_9 + datetime.timedelta(days=7)
    assert toca_revision_semanal(lunes_siguiente, LUNES_9.date(), 0, 9) is True


def test_el_correo_separa_lo_urgente_de_lo_que_falta_cargar():
    conteo = {"OK": 56, "FUERA": 1, "SIN ROL": 1}
    asunto, cuerpo = redactar_revision(PROBLEMAS, conteo, 21)

    assert "1 por atender de 58" in asunto
    # Lo accionable aparece con teléfono
    assert "+5215535109140" in cuerpo and "folio 8" in cuerpo
    # Lo que solo falta cargar no se lista como si fuera un problema
    assert "Josefa" not in cuerpo
    assert "todavía no se" in cuerpo


def test_cuando_todo_esta_bien_el_asunto_lo_dice():
    asunto, cuerpo = redactar_revision([], {"OK": 40}, 21)
    assert "las 40 tienen quién las atienda" in asunto
    assert "A revisar" not in cuerpo


async def test_no_manda_correo_si_no_hay_citas():
    with (
        patch.object(
            alertas_citas,
            "revisar_citas_futuras",
            AsyncMock(return_value=([], {})),
        ),
        patch.object(alertas_citas.correo, "enviar", AsyncMock()) as enviar,
    ):
        assert await alertas_citas.enviar_revision_semanal() is False
    enviar.assert_not_awaited()
