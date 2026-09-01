"""Test del endpoint HTTP del webhook (no solo del normalizador).

Cubre el hueco que dejó pasar a producción la falta de python-multipart:
aquí se hace un POST real con form-encoded, tal como lo manda Twilio.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidhe_agent.channels.whatsapp_twilio import WhatsAppTwilioAdapter
from sidhe_agent.main import app

FORM_TWILIO = {
    "From": "whatsapp:+5215512345678",
    "To": "whatsapp:+5215638955164",
    "MessageSid": "SMtest123",
    "ProfileName": "Ana",
    "NumMedia": "0",
    "Body": "hola",
}


@pytest.fixture
def cliente():
    # Sin `with`: TestClient no dispara el lifespan, asi que no se conecta
    # a Postgres ni a Anthropic. El adapter se inyecta a mano.
    app.state.adapter = WhatsAppTwilioAdapter(
        account_sid="ACtest", auth_token="token", from_number="whatsapp:+5215638955164"
    )
    return TestClient(app)


def test_webhook_acepta_form_de_twilio(cliente):
    """El form-encoded de Twilio debe parsearse y despacharse a background."""
    with (
        patch("sidhe_agent.main._mensaje_ya_procesado", AsyncMock(return_value=False)),
        patch("sidhe_agent.main._guardar_mensaje", AsyncMock()) as guardar,
        patch("sidhe_agent.main.procesar_mensaje", AsyncMock()) as procesar,
    ):
        respuesta = cliente.post("/webhooks/twilio/whatsapp", data=FORM_TWILIO)

    assert respuesta.status_code == 200
    # Se auditó el mensaje entrante
    assert guardar.await_args.args[0] == "in"
    # Y se despachó el procesamiento en segundo plano
    entrante = procesar.await_args.args[1]
    assert entrante.user_id == "+5215512345678"
    assert entrante.contenido == "hola"


def test_webhook_ignora_duplicados(cliente):
    """Twilio reintenta: el mismo MessageSid no se procesa dos veces."""
    with (
        patch("sidhe_agent.main._mensaje_ya_procesado", AsyncMock(return_value=True)),
        patch("sidhe_agent.main._guardar_mensaje", AsyncMock()) as guardar,
        patch("sidhe_agent.main.procesar_mensaje", AsyncMock()) as procesar,
    ):
        respuesta = cliente.post("/webhooks/twilio/whatsapp", data=FORM_TWILIO)

    assert respuesta.status_code == 200
    guardar.assert_not_awaited()
    procesar.assert_not_awaited()


def test_webhook_seleccion_interactiva(cliente):
    """Un toque en un boton llega como seleccion con su id exacto."""
    form = {**FORM_TWILIO, "ListId": "suc_12", "ListTitle": "Liverpool Perisur"}
    with (
        patch("sidhe_agent.main._mensaje_ya_procesado", AsyncMock(return_value=False)),
        patch("sidhe_agent.main._guardar_mensaje", AsyncMock()),
        patch("sidhe_agent.main.procesar_mensaje", AsyncMock()) as procesar,
    ):
        respuesta = cliente.post("/webhooks/twilio/whatsapp", data=form)

    assert respuesta.status_code == 200
    entrante = procesar.await_args.args[1]
    assert entrante.tipo == "seleccion_interactiva"
    assert entrante.item_id == "suc_12"
