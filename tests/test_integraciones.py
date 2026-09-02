"""Tests de las integraciones externas (Google Calendar y n8n).

Ninguna debe romper el agendado si falla, y ambas quedan inactivas cuando no
estan configuradas — por eso el resto de la suite corre sin tocar la red.
"""

from unittest.mock import AsyncMock, patch

import datetime

from sidhe_agent.services import google_calendar, n8n


def test_desactivadas_sin_configuracion():
    assert google_calendar.sincronizacion_activa() is False
    assert n8n.webhook_activo() is False


async def test_crear_evento_sin_calendar_id_es_noop():
    assert await google_calendar.crear_evento(
        None,
        nombre_cliente="Ana",
        telefono="+52155",
        folio=1,
        fecha=datetime.date(2026, 9, 2),
        hora_inicio=datetime.time(11, 0),
        hora_fin=datetime.time(12, 0),
        direccion="Calle 1",
    ) is None


async def test_borrar_evento_sin_datos_es_noop():
    assert await google_calendar.borrar_evento(None, None) is False
    assert await google_calendar.borrar_evento("cal@gmail.com", None) is False


async def test_avisar_n8n_sin_url_no_envia():
    assert await n8n.avisar_cita("creada", {"folio": 1}) is False


async def test_fallo_de_n8n_no_propaga(monkeypatch):
    """Un webhook caido no debe tumbar el agendado."""
    monkeypatch.setattr(
        n8n.get_settings(), "n8n_webhook_citas", "https://n8n.test/webhook", raising=False
    )
    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=RuntimeError("caido"))):
        assert await n8n.avisar_cita("creada", {"folio": 1}) is False


def test_cuerpo_del_evento_incluye_datos_de_la_cita():
    cuerpo = google_calendar._cuerpo_evento(
        "Juan Correa", "+5215642934582", 7,
        datetime.date(2026, 9, 2), datetime.time(11, 0), datetime.time(12, 0),
        "Periferico Sur 4690",
    )
    assert cuerpo["summary"] == "Estudio de pisada - Juan Correa"
    assert "Folio: 7" in cuerpo["description"]
    assert "+5215642934582" in cuerpo["description"]
    assert cuerpo["location"] == "Periferico Sur 4690"
    assert cuerpo["start"]["dateTime"].startswith("2026-09-02T11:00")
    assert cuerpo["end"]["dateTime"].startswith("2026-09-02T12:00")
    assert cuerpo["start"]["timeZone"] == "America/Mexico_City"
