"""Tests del normalizador de webhooks de Twilio → IncomingMessage."""

from sidhe_agent.channels.whatsapp_twilio import WhatsAppTwilioAdapter


def _adapter() -> WhatsAppTwilioAdapter:
    return WhatsAppTwilioAdapter(
        account_sid="ACtest",
        auth_token="token",
        from_number="whatsapp:+5215638955164",
    )


BASE = {
    "From": "whatsapp:+5215512345678",
    "To": "whatsapp:+5215638955164",
    "MessageSid": "SM123",
    "ProfileName": "Ana",
    "NumMedia": "0",
}


def test_texto_plano():
    mensaje = _adapter().parse_incoming({**BASE, "Body": "hola, ¿cuánto cuestan?"})
    assert mensaje.canal == "whatsapp"
    assert mensaje.user_id == "+5215512345678"
    assert mensaje.tipo == "texto"
    assert mensaje.contenido == "hola, ¿cuánto cuestan?"
    assert mensaje.message_sid == "SM123"
    assert mensaje.nombre_perfil == "Ana"


def test_audio():
    mensaje = _adapter().parse_incoming(
        {
            **BASE,
            "Body": "",
            "NumMedia": "1",
            "MediaContentType0": "audio/ogg",
            "MediaUrl0": "https://api.twilio.com/media/ME123",
        }
    )
    assert mensaje.tipo == "audio"
    assert mensaje.media_url == "https://api.twilio.com/media/ME123"
    assert mensaje.media_content_type == "audio/ogg"


def test_imagen_no_es_audio():
    mensaje = _adapter().parse_incoming(
        {
            **BASE,
            "Body": "mira mi zapato",
            "NumMedia": "1",
            "MediaContentType0": "image/jpeg",
            "MediaUrl0": "https://api.twilio.com/media/ME456",
        }
    )
    assert mensaje.tipo == "texto"
    assert mensaje.contenido == "mira mi zapato"


def test_seleccion_de_lista():
    mensaje = _adapter().parse_incoming(
        {**BASE, "Body": "Liverpool Perisur", "ListId": "suc_12", "ListTitle": "Liverpool Perisur"}
    )
    assert mensaje.tipo == "seleccion_interactiva"
    assert mensaje.item_id == "suc_12"
    assert mensaje.contenido == "Liverpool Perisur"


def test_seleccion_de_boton():
    mensaje = _adapter().parse_incoming(
        {**BASE, "Body": "Confirmar ✅", "ButtonPayload": "confirmar", "ButtonText": "Confirmar ✅"}
    )
    assert mensaje.tipo == "seleccion_interactiva"
    assert mensaje.item_id == "confirmar"
    assert mensaje.contenido == "Confirmar ✅"
