"""Fotos, stickers, documentos y ubicaciones: que nunca lleguen vacíos.

Antes una foto sin pie le llegaba al modelo como mensaje vacío, la API lo
rechazaba y el cliente leía "tuve un problema técnico". 68 veces en 30 días,
casi siempre gente mandando su estudio o su receta.
"""

import types

import pytest

from sidhe_agent import main
from sidhe_agent.channels import meta
from sidhe_agent.channels.adjuntos import describir_archivo, describir_ubicacion
from sidhe_agent.channels.schemas import IncomingMessage
from sidhe_agent.channels.whatsapp_cloud import WhatsAppCloudAdapter, desglosar
from sidhe_agent.channels.whatsapp_twilio import WhatsAppTwilioAdapter

TWILIO = WhatsAppTwilioAdapter(account_sid="AC", auth_token="t", from_number="whatsapp:+521")
CLOUD = WhatsAppCloudAdapter(phone_number_id="1", token="t")
BASE_TWILIO = {"From": "whatsapp:+5215512345678", "To": "whatsapp:+5215638955164",
               "MessageSid": "SM1", "Body": ""}


# --- la descripcion ---

def test_cada_archivo_se_nombra_por_lo_que_es():
    assert describir_archivo("image/jpeg") == "[el cliente envió una imagen]"
    assert describir_archivo("image/webp") == "[el cliente envió un sticker]"
    assert describir_archivo("application/pdf") == "[el cliente envió un documento]"
    assert describir_archivo("video/mp4") == "[el cliente envió un video]"


def test_el_pie_de_foto_viaja_con_la_nota():
    """Si escribió algo con la foto, eso es lo que hay que contestar."""
    assert describir_archivo("image/jpeg", "esta es mi receta") == (
        '[el cliente envió una imagen] con el texto: "esta es mi receta"'
    )


def test_una_ubicacion_con_direccion_sirve_para_buscar_sucursal():
    nota = describir_ubicacion("19.43", "-99.19", "Av. Ejército Nacional, Polanco", "Casa")
    assert "Polanco" in nota
    assert nota.startswith("[el cliente compartió su ubicación:")


def test_una_ubicacion_sin_direccion_lo_dice():
    """Para que el agente sepa que tiene que preguntar la ciudad."""
    assert "sin dirección" in describir_ubicacion("19.43", "-99.19")


# --- twilio ---

def test_twilio_una_foto_ya_no_llega_vacia():
    entrante = TWILIO.parse_incoming({
        **BASE_TWILIO, "NumMedia": "1", "MediaContentType0": "image/jpeg",
        "MediaUrl0": "https://api.twilio.com/x",
    })
    assert entrante.tipo == "adjunto"
    assert entrante.contenido == "[el cliente envió una imagen]"


def test_twilio_un_sticker():
    entrante = TWILIO.parse_incoming({
        **BASE_TWILIO, "NumMedia": "1", "MediaContentType0": "image/webp",
    })
    assert entrante.contenido == "[el cliente envió un sticker]"


def test_twilio_la_nota_de_voz_sigue_siendo_audio():
    """Lo nuevo no puede comerse lo que ya funcionaba."""
    entrante = TWILIO.parse_incoming({
        **BASE_TWILIO, "NumMedia": "1", "MediaContentType0": "audio/ogg",
        "MediaUrl0": "https://api.twilio.com/a",
    })
    assert entrante.tipo == "audio"


def test_twilio_una_ubicacion():
    """Twilio la manda sin media, en campos propios."""
    entrante = TWILIO.parse_incoming({
        **BASE_TWILIO, "Latitude": "19.43", "Longitude": "-99.19",
        "Address": "Polanco, CDMX",
    })
    assert entrante.tipo == "adjunto"
    assert "Polanco" in entrante.contenido


def test_twilio_el_texto_normal_no_cambia():
    entrante = TWILIO.parse_incoming({**BASE_TWILIO, "Body": "hola"})
    assert entrante.tipo == "texto"
    assert entrante.contenido == "hola"


# --- meta directo ---

def _cloud(mensaje: dict) -> IncomingMessage:
    partes = desglosar({
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"display_phone_number": "5215638955164"},
            "messages": [{"from": "521228", "id": "w", **mensaje}],
        }}]}],
    })
    return CLOUD.parse_incoming(partes[0])


def test_cloud_una_imagen_con_pie():
    entrante = _cloud({"type": "image",
                       "image": {"id": "m", "mime_type": "image/jpeg", "caption": "mi estudio"}})
    assert entrante.tipo == "adjunto"
    assert 'imagen] con el texto: "mi estudio"' in entrante.contenido


def test_cloud_un_sticker_aunque_el_mime_sea_de_imagen():
    entrante = _cloud({"type": "sticker", "sticker": {"id": "m", "mime_type": "image/webp"}})
    assert entrante.contenido == "[el cliente envió un sticker]"


def test_cloud_una_ubicacion():
    entrante = _cloud({"type": "location", "location": {
        "latitude": 19.43, "longitude": -99.19, "name": "Liverpool Polanco",
    }})
    assert "Liverpool Polanco" in entrante.contenido


def test_cloud_lo_que_no_sabemos_leer_se_nombra():
    """Contactos, reacciones... mejor nombrarlos que mandarlos vacíos."""
    entrante = _cloud({"type": "contacts", "contacts": [{}]})
    assert entrante.tipo == "adjunto"
    assert entrante.contenido.strip()


# --- instagram / messenger ---

def test_instagram_una_foto_ya_no_llega_vacia():
    adaptador = meta.MetaAdapter("instagram", "TOKEN")
    entrante = adaptador.parse_incoming({
        "sender": {"id": "IG1"},
        "message": {"mid": "m1", "attachments": [{"type": "image", "payload": {"url": "x"}}]},
    })
    assert entrante.tipo == "adjunto"
    assert entrante.contenido == "[el cliente envió una imagen]"


def test_instagram_el_audio_sigue_siendo_audio():
    adaptador = meta.MetaAdapter("instagram", "TOKEN")
    entrante = adaptador.parse_incoming({
        "sender": {"id": "IG1"},
        "message": {"mid": "m1", "attachments": [{"type": "audio", "payload": {"url": "x"}}]},
    })
    assert entrante.tipo == "audio"


# --- ultima red ---

@pytest.mark.asyncio
async def test_si_algo_llega_vacio_se_contesta_sin_llamar_al_modelo(monkeypatch):
    """Nunca más "problema técnico" por mandar algo que no es texto."""
    enviados, invocado = [], []

    class Adaptador:
        canal = "whatsapp"

        async def send(self, user_id, mensaje):
            enviados.append(mensaje.texto)
            return "SID"

    class Grafo:
        async def aget_state(self, config):
            return types.SimpleNamespace(tasks=[], values={}, next=())

        async def ainvoke(self, *a, **k):
            invocado.append(True)

    async def _activo(*a):
        return main.conversaciones.ACTIVO

    async def _nada(*a, **k):
        return None

    monkeypatch.setattr(main.conversaciones, "revisar_pausa", _activo)
    monkeypatch.setattr(main, "_guardar_mensaje", _nada)
    adaptador = Adaptador()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        graph=Grafo(), adapter=adaptador, adapters={"whatsapp": adaptador},
    ))

    await main.procesar_mensaje(app, IncomingMessage(
        canal="whatsapp", user_id="+521", tipo="adjunto", contenido="",
    ))

    assert invocado == []
    assert enviados == [main.MENSAJE_VACIO]
