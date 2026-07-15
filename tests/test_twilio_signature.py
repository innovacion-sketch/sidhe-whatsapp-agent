"""Tests de validación de la firma X-Twilio-Signature."""

from twilio.request_validator import RequestValidator

from sidhe_agent.channels.whatsapp_twilio import validar_firma

TOKEN = "secreto_de_prueba"
URL = "https://sidhe.example.com/webhooks/twilio/whatsapp"
PARAMS = {
    "From": "whatsapp:+5215512345678",
    "Body": "hola",
    "MessageSid": "SM123",
}


def test_firma_valida():
    firma = RequestValidator(TOKEN).compute_signature(URL, PARAMS)
    assert validar_firma(URL, PARAMS, firma, TOKEN) is True


def test_firma_invalida():
    assert validar_firma(URL, PARAMS, "firma-falsa", TOKEN) is False


def test_firma_de_otra_url():
    firma = RequestValidator(TOKEN).compute_signature("https://otra.example.com/x", PARAMS)
    assert validar_firma(URL, PARAMS, firma, TOKEN) is False


def test_sin_firma_o_sin_token():
    firma = RequestValidator(TOKEN).compute_signature(URL, PARAMS)
    assert validar_firma(URL, PARAMS, "", TOKEN) is False
    assert validar_firma(URL, PARAMS, firma, "") is False
