"""WhatsApp directo con Meta: que hable el mismo idioma que el de Twilio.

Lo que estos tests protegen, en orden de lo caro que sale equivocarse:

1. El `user_id` tiene que salir idéntico al que venía de Twilio. Es la
   mitad de la clave del thread de memoria: si Meta lo escribe sin "+" y
   se guarda así, cada cliente empieza de cero el día del cambio.
2. Una selección tiene que traer el id EXACTO de la opción tocada. Si se
   pierde, el agente interpreta texto libre y agenda donde no es.
3. Los acuses de entrega y lectura no son mensajes. Tratarlos como tales
   es un bucle del bot consigo mismo.
"""

import hashlib
import hmac

import pytest

from sidhe_agent.channels.schemas import Opcion, OutgoingMessage, UIElement
from sidhe_agent.channels.whatsapp_cloud import (
    WhatsAppCloudAdapter,
    desglosar,
    validar_firma,
)

ADAPTADOR = WhatsAppCloudAdapter(phone_number_id="123", token="t")

METADATA = {"display_phone_number": "5215638955164", "phone_number_id": "123"}


def webhook(mensaje: dict, contactos: list | None = None) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": METADATA,
                            "contacts": contactos or [],
                            "messages": [mensaje],
                        }
                    }
                ]
            }
        ],
    }


async def _valor(x):
    return x


# --- entrada ---

def test_el_telefono_queda_igual_que_con_twilio():
    """Twilio manda '+521...' y Meta '521...'. Tiene que quedar uno solo.

    Si no, el thread de memoria cambia de nombre el día del cambio y todos
    los clientes amanecen sin historial.
    """
    partes = desglosar(webhook({
        "from": "5212283407867", "id": "wamid.1", "type": "text",
        "text": {"body": "hola"},
    }))
    entrante = ADAPTADOR.parse_incoming(partes[0])

    assert entrante.user_id == "+5212283407867"
    assert entrante.canal == "whatsapp"
    assert entrante.numero_negocio == "+5215638955164"
    assert entrante.contenido == "hola"


def test_una_seleccion_de_lista_trae_el_id_exacto():
    partes = desglosar(webhook({
        "from": "521228", "id": "wamid.2", "type": "interactive",
        "interactive": {
            "type": "list_reply",
            "list_reply": {"id": "suc_polanco", "title": "Liverpool Polanco"},
        },
    }))
    entrante = ADAPTADOR.parse_incoming(partes[0])

    assert entrante.tipo == "seleccion_interactiva"
    assert entrante.item_id == "suc_polanco"
    assert entrante.contenido == "Liverpool Polanco"


def test_una_seleccion_de_boton_tambien():
    partes = desglosar(webhook({
        "from": "521228", "id": "wamid.3", "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": "si", "title": "Sí, confirmar"},
        },
    }))
    assert ADAPTADOR.parse_incoming(partes[0]).item_id == "si"


def test_el_boton_de_una_plantilla_cuenta_como_seleccion():
    """El del recordatorio de cita: llega con otra forma."""
    partes = desglosar(webhook({
        "from": "521228", "id": "wamid.4", "type": "button",
        "button": {"payload": "confirmar", "text": "Confirmar"},
    }))
    entrante = ADAPTADOR.parse_incoming(partes[0])

    assert entrante.tipo == "seleccion_interactiva"
    assert entrante.item_id == "confirmar"


def test_una_nota_de_voz_trae_el_id_para_bajarla():
    """Meta no da la URL en el webhook, solo el id."""
    partes = desglosar(webhook({
        "from": "521228", "id": "wamid.5", "type": "audio",
        "audio": {"id": "media-999", "mime_type": "audio/ogg; codecs=opus"},
    }))
    entrante = ADAPTADOR.parse_incoming(partes[0])

    assert entrante.tipo == "audio"
    assert entrante.media_url == "media-999"


def test_el_nombre_del_perfil_llega_aunque_viva_en_otro_nivel():
    partes = desglosar(webhook(
        {"from": "521228", "id": "wamid.6", "type": "text", "text": {"body": "hola"}},
        contactos=[{"wa_id": "521228", "profile": {"name": "Diana"}}],
    ))
    assert ADAPTADOR.parse_incoming(partes[0]).nombre_perfil == "Diana"


def test_los_acuses_no_son_mensajes():
    """Entregado y leído llegan por el mismo webhook. Si se procesan, bucle."""
    acuse = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": METADATA,
                            "statuses": [{"id": "wamid.1", "status": "delivered"}],
                        }
                    }
                ]
            }
        ],
    }
    assert desglosar(acuse) == []


def test_lo_que_no_es_whatsapp_se_ignora():
    """Instagram y Messenger llegan igual y los atiende otro adaptador."""
    assert desglosar({"object": "instagram", "entry": []}) == []


def test_un_solo_post_puede_traer_varias_conversaciones():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [
            {"value": {"metadata": METADATA, "messages": [
                {"from": "521", "id": "a", "type": "text", "text": {"body": "1"}},
                {"from": "522", "id": "b", "type": "text", "text": {"body": "2"}},
            ]}},
        ]}],
    }
    assert len(desglosar(payload)) == 2


# --- salida ---

def test_un_texto_sale_como_texto():
    cuerpo = ADAPTADOR._cuerpo("+521228", OutgoingMessage(texto="Hola"))
    assert cuerpo["type"] == "text"
    assert cuerpo["text"]["body"] == "Hola"


def test_la_lista_viaja_en_el_mismo_mensaje():
    """Con Twilio había que crear un Content antes; aquí no hay nada que crear."""
    mensaje = OutgoingMessage(
        texto="Elige tu sucursal",
        ui=UIElement(tipo="lista", titulo="Sucursales", opciones=[
            Opcion(id="polanco", etiqueta="Polanco", descripcion="Liverpool Polanco"),
            Opcion(id="satelite", etiqueta="Satélite"),
        ]),
    )
    cuerpo = ADAPTADOR._cuerpo("+521228", mensaje)

    assert cuerpo["interactive"]["type"] == "list"
    filas = cuerpo["interactive"]["action"]["sections"][0]["rows"]
    assert [f["id"] for f in filas] == ["polanco", "satelite"]
    assert filas[0]["description"] == "Liverpool Polanco"
    assert "description" not in filas[1]


def test_los_botones_salen_como_botones():
    mensaje = OutgoingMessage(
        texto="¿Confirmas?",
        ui=UIElement(tipo="botones", titulo="Confirmar", opciones=[
            Opcion(id="si", etiqueta="Sí"), Opcion(id="no", etiqueta="No"),
        ]),
    )
    cuerpo = ADAPTADOR._cuerpo("+521228", mensaje)

    assert cuerpo["interactive"]["type"] == "button"
    botones = cuerpo["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in botones] == ["si", "no"]


def test_las_etiquetas_largas_se_recortan_al_limite_de_meta():
    """Meta rechaza el mensaje entero si una etiqueta se pasa."""
    mensaje = OutgoingMessage(
        texto="Elige",
        ui=UIElement(tipo="lista", titulo="T" * 40, opciones=[
            Opcion(id="x", etiqueta="E" * 60, descripcion="D" * 200),
        ]),
    )
    cuerpo = ADAPTADOR._cuerpo("+521228", mensaje)
    fila = cuerpo["interactive"]["action"]["sections"][0]["rows"][0]

    assert len(fila["title"]) <= 24
    assert len(fila["description"]) <= 72
    assert len(cuerpo["interactive"]["action"]["button"]) <= 20


# --- firma ---

def test_una_firma_valida_pasa_y_una_falsa_no():
    cuerpo = b'{"object":"whatsapp_business_account"}'
    firma = hmac.new(b"secreto", cuerpo, hashlib.sha256).hexdigest()

    assert validar_firma(cuerpo, f"sha256={firma}", "secreto")
    assert not validar_firma(cuerpo, f"sha256={firma}", "otro-secreto")
    assert not validar_firma(cuerpo, "", "secreto")
    assert not validar_firma(cuerpo, f"sha256={firma}", "")


def test_la_firma_se_calcula_sobre_el_cuerpo_crudo():
    """Reserializar el JSON cambia el hash y todo se rechazaría."""
    crudo = b'{"a":1, "b":2}'
    firma = hmac.new(b"s", crudo, hashlib.sha256).hexdigest()

    assert validar_firma(crudo, f"sha256={firma}", "s")
    assert not validar_firma(b'{"a": 1, "b": 2}', f"sha256={firma}", "s")


# --- varios numeros ---

@pytest.mark.asyncio
async def test_se_contesta_desde_el_numero_al_que_escribieron():
    """El cliente no tiene guardado el otro número: le llegaría de un extraño."""
    adaptador = WhatsAppCloudAdapter(
        phone_number_id="id-5164", token="t",
        numero_por_defecto="+5215638955164",
        resolver_remitente=lambda user_id: _valor("+5215512340202"),
    )
    adaptador.registrar_numero("+5215512340202", "id-0202")

    assert await adaptador._phone_id_para("+521228") == "id-0202"


@pytest.mark.asyncio
async def test_si_no_se_sabe_el_numero_se_usa_el_de_siempre():
    async def _revienta(user_id):
        raise RuntimeError("base caída")

    adaptador = WhatsAppCloudAdapter(
        phone_number_id="id-5164", token="t",
        numero_por_defecto="+5215638955164", resolver_remitente=_revienta,
    )
    adaptador.registrar_numero("+5215512340202", "id-0202")

    assert await adaptador._phone_id_para("+521228") == "id-5164"
