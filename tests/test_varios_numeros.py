"""Con el 5164 y el 0202 activos, al cliente se le contesta desde el que usó.

Si la respuesta saliera siempre del número por defecto, a quien escribió al
0202 le llegaría de un contacto que no tiene guardado.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from sidhe_agent.channels.schemas import OutgoingMessage
from sidhe_agent.channels.whatsapp_twilio import WhatsAppTwilioAdapter
from sidhe_agent.main import app

DE_SIEMPRE = "whatsapp:+5215638955164"
NUEVO = "+5215590630202"
CLIENTE = "+5215512345678"

FORM = {
    "From": f"whatsapp:{CLIENTE}",
    "To": f"whatsapp:{NUEVO}",
    "MessageSid": "SMvarios1",
    "NumMedia": "0",
    "Body": "hola",
}


def _adapter(resolver=None) -> WhatsAppTwilioAdapter:
    return WhatsAppTwilioAdapter(
        account_sid="ACtest",
        auth_token="token",
        from_number=DE_SIEMPRE,
        resolver_remitente=resolver,
    )


def test_el_entrante_trae_el_numero_al_que_escribio():
    assert _adapter().parse_incoming(FORM).numero_negocio == NUEVO


def test_sin_to_no_inventa_numero():
    form = {k: v for k, v in FORM.items() if k != "To"}
    assert _adapter().parse_incoming(form).numero_negocio is None


async def test_contesta_desde_el_numero_al_que_escribio():
    adapter = _adapter(AsyncMock(return_value=NUEVO))
    assert await adapter.remitente_para(CLIENTE) == f"whatsapp:{NUEVO}"


async def test_no_duplica_el_prefijo():
    adapter = _adapter(AsyncMock(return_value=f"whatsapp:{NUEVO}"))
    assert await adapter.remitente_para(CLIENTE) == f"whatsapp:{NUEVO}"


async def test_sin_historial_sale_del_numero_de_siempre():
    adapter = _adapter(AsyncMock(return_value=None))
    assert await adapter.remitente_para(CLIENTE) == DE_SIEMPRE


async def test_sin_resolver_sale_del_numero_de_siempre():
    assert await _adapter().remitente_para(CLIENTE) == DE_SIEMPRE


async def test_si_falla_la_busqueda_igual_contesta():
    """Mejor contestar desde el número de siempre que dejar al cliente sin nada."""
    adapter = _adapter(AsyncMock(side_effect=RuntimeError("sin base")))
    assert await adapter.remitente_para(CLIENTE) == DE_SIEMPRE


async def test_el_envio_sale_del_numero_resuelto():
    adapter = _adapter(AsyncMock(return_value=NUEVO))
    cliente_twilio = MagicMock()
    cliente_twilio.messages.create.return_value = MagicMock(sid="SMsalida")
    adapter._client = cliente_twilio

    await adapter.send(CLIENTE, OutgoingMessage(texto="hola"))

    enviado = cliente_twilio.messages.create.call_args.kwargs
    assert enviado["from_"] == f"whatsapp:{NUEVO}"
    assert enviado["to"] == f"whatsapp:{CLIENTE}"


def test_el_webhook_guarda_a_que_numero_escribio():
    app.state.adapter = _adapter()
    with (
        patch("sidhe_agent.main._mensaje_ya_procesado", AsyncMock(return_value=False)),
        patch("sidhe_agent.main._guardar_mensaje", AsyncMock()) as guardar,
        patch("sidhe_agent.main.procesar_mensaje", AsyncMock()),
    ):
        respuesta = TestClient(app).post("/webhooks/twilio/whatsapp", data=FORM)

    assert respuesta.status_code == 200
    assert guardar.await_args.kwargs["numero_negocio"] == NUEVO
