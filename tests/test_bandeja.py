"""Tests de la bandeja del panel: autenticacion, envio y pausa del bot."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidhe_agent.channels.whatsapp_twilio import WhatsAppTwilioAdapter
from sidhe_agent.config import get_settings
from sidhe_agent.main import app
from sidhe_agent.services import conversaciones

CLAVE = "clave-interna-de-prueba"
USER = "+5215642934582"


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", CLAVE, raising=False)
    app.state.adapter = WhatsAppTwilioAdapter(
        account_sid="ACtest", auth_token="token", from_number="whatsapp:+521563"
    )
    return TestClient(app)


def test_bandeja_exige_clave(cliente):
    assert cliente.get("/internal/conversaciones").status_code == 401
    assert cliente.get(f"/internal/conversaciones/whatsapp/{USER}").status_code == 401
    assert cliente.post(
        f"/internal/conversaciones/whatsapp/{USER}/responder", json={"texto": "hola"}
    ).status_code == 401


def test_listar_conversaciones(cliente):
    falsas = [{
        "canal": "whatsapp", "user_id": USER, "ultimo_mensaje": "hola",
        "ultima_direccion": "in", "ultima_fecha": "2026-09-04T10:00:00",
        "mensajes": 12, "bot_pausado": False,
    }]
    with patch("sidhe_agent.main.conversaciones.listar", AsyncMock(return_value=falsas)):
        r = cliente.get("/internal/conversaciones", headers={"X-API-Key": CLAVE})
    assert r.status_code == 200
    assert r.json()["conversaciones"][0]["user_id"] == USER


def test_responder_envia_y_silencia_al_bot(cliente):
    """Responder implica tomar el control: si no, bot y humano se pisan."""
    with (
        patch("sidhe_agent.main.conversaciones.tomar_control",
              AsyncMock(return_value=True)) as tomar,
        patch.object(app.state.adapter, "send", AsyncMock(return_value="SM1")),
        patch("sidhe_agent.main._guardar_mensaje", AsyncMock()) as guardar,
    ):
        r = cliente.post(
            f"/internal/conversaciones/whatsapp/{USER}/responder",
            json={"texto": "  Te confirmo tu cita  "},
            headers={"X-API-Key": CLAVE},
        )
    assert r.status_code == 200
    assert r.json()["bot_pausado"] is True
    tomar.assert_awaited_once()
    # Se audita como mensaje de humano, no del bot
    assert guardar.await_args.args[3] == "humano"
    assert guardar.await_args.args[4] == "Te confirmo tu cita"


def test_responder_rechaza_texto_vacio(cliente):
    r = cliente.post(
        f"/internal/conversaciones/whatsapp/{USER}/responder",
        json={"texto": "   "}, headers={"X-API-Key": CLAVE},
    )
    assert r.status_code == 400


def test_responder_rechaza_canal_sin_adaptador(cliente):
    """Instagram todavia no tiene adaptador de envio: mejor decirlo claro."""
    r = cliente.post(
        f"/internal/conversaciones/instagram/{USER}/responder",
        json={"texto": "hola"}, headers={"X-API-Key": CLAVE},
    )
    assert r.status_code == 400
    assert "instagram" in r.json()["detail"]


def test_ventana_de_24h():
    """Fuera de la ventana, WhatsApp rechaza el texto libre: hay que avisarlo."""
    import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(get_settings().tz)
    reciente = datetime.datetime.now(tz) - datetime.timedelta(hours=2)
    viejo = datetime.datetime.now(tz) - datetime.timedelta(hours=30)
    assert conversaciones._ventana_abierta({"fecha": reciente.isoformat()}) is True
    assert conversaciones._ventana_abierta({"fecha": viejo.isoformat()}) is False
    assert conversaciones._ventana_abierta(None) is False
