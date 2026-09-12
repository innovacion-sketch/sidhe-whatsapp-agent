"""Tests de Instagram DM y Facebook Messenger.

Sin red y sin credenciales de Meta: se simulan los webhooks tal como los
manda la Messenger Platform. Lo que se comprueba es lo que rompe estas
integraciones en produccion: la firma, los ecos, los reintentos y los
limites de las quick replies.
"""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidhe_agent.channels import meta
from sidhe_agent.channels.schemas import Opcion, OutgoingMessage, UIElement
from sidhe_agent.config import get_settings
from sidhe_agent.main import app

SECRETO = "secreto-de-la-app-de-meta"
VERIFY = "token-que-teclea-el-usuario"


def firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()


def mensaje_de_instagram(texto: str = "hola", mid: str = "mid.1") -> dict:
    return {
        "object": "instagram",
        "entry": [{
            "id": "17841400000000000",
            "time": 1757600000,
            "messaging": [{
                "sender": {"id": "IGSID_CLIENTE"},
                "recipient": {"id": "17841400000000000"},
                "timestamp": 1757600000,
                "message": {"mid": mid, "text": texto},
            }],
        }],
    }


@pytest.fixture
def cliente(monkeypatch):
    ajustes = get_settings()
    monkeypatch.setattr(ajustes, "meta_app_secret", SECRETO, raising=False)
    monkeypatch.setattr(ajustes, "meta_verify_token", VERIFY, raising=False)
    return TestClient(app)


# --- firma y verificacion ---

def test_la_firma_protege_el_webhook():
    cuerpo = b'{"object":"instagram"}'
    assert meta.validar_firma(cuerpo, firmar(cuerpo), SECRETO)
    assert not meta.validar_firma(cuerpo, firmar(b"otro cuerpo"), SECRETO)
    assert not meta.validar_firma(cuerpo, "sha256=loquesea", SECRETO)
    assert not meta.validar_firma(cuerpo, "", SECRETO)
    # Sin app secret configurado no se acepta NADA, ni con firma valida
    assert not meta.validar_firma(cuerpo, firmar(cuerpo), "")


def test_meta_da_de_alta_el_webhook_con_el_challenge(cliente):
    r = cliente.get("/webhooks/meta", params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFY,
        "hub.challenge": "1234567890",
    })
    assert r.status_code == 200
    assert r.text == "1234567890"


def test_rechaza_una_verificacion_con_otro_token(cliente):
    r = cliente.get("/webhooks/meta", params={
        "hub.mode": "subscribe", "hub.verify_token": "no-es",
        "hub.challenge": "1234567890",
    })
    assert r.status_code == 403


def test_un_webhook_sin_firma_no_entra(cliente):
    r = cliente.post("/webhooks/meta", json=mensaje_de_instagram())
    assert r.status_code == 403


# --- desglose de eventos ---

def test_ignora_los_ecos_de_nuestros_propios_mensajes():
    """Sin esto el bot se contesta a si mismo hasta el infinito."""
    payload = mensaje_de_instagram()
    payload["entry"][0]["messaging"][0]["message"]["is_echo"] = True
    assert meta.desglosar(payload) == []


def test_ignora_acuses_de_entrega_y_lectura():
    payload = {"object": "page", "entry": [{"messaging": [
        {"sender": {"id": "X"}, "delivery": {"mids": ["mid.1"]}},
        {"sender": {"id": "X"}, "read": {"watermark": 1757600000}},
    ]}]}
    assert meta.desglosar(payload) == []


def test_distingue_instagram_de_messenger():
    assert meta.desglosar(mensaje_de_instagram())[0][0] == "instagram"
    de_facebook = mensaje_de_instagram()
    de_facebook["object"] = "page"
    assert meta.desglosar(de_facebook)[0][0] == "messenger"
    ajeno = mensaje_de_instagram()
    ajeno["object"] = "whatsapp_business_account"
    assert meta.desglosar(ajeno) == []


def test_un_solo_post_puede_traer_varias_conversaciones():
    payload = mensaje_de_instagram()
    payload["entry"].append({
        "id": "17841400000000000",
        "messaging": [{
            "sender": {"id": "OTRO_CLIENTE"},
            "message": {"mid": "mid.2", "text": "buenas"},
        }],
    })
    assert len(meta.desglosar(payload)) == 2


# --- normalizacion ---

def test_normaliza_un_mensaje_de_texto():
    adaptador = meta.MetaAdapter("instagram", "TOKEN")
    _, evento = meta.desglosar(mensaje_de_instagram("¿ya están mis plantillas?"))[0]
    entrante = adaptador.parse_incoming(evento)
    assert entrante.canal == "instagram"
    assert entrante.user_id == "IGSID_CLIENTE"
    assert entrante.tipo == "texto"
    assert entrante.contenido == "¿ya están mis plantillas?"
    assert entrante.message_sid == "mid.1"


def test_una_quick_reply_llega_como_seleccion_y_no_como_texto():
    """El id exacto de la opcion; el agente no reinterpreta el texto."""
    payload = mensaje_de_instagram()
    payload["entry"][0]["messaging"][0]["message"]["quick_reply"] = {
        "payload": "suc_7"
    }
    _, evento = meta.desglosar(payload)[0]
    entrante = meta.MetaAdapter("instagram", "TOKEN").parse_incoming(evento)
    assert entrante.tipo == "seleccion_interactiva"
    assert entrante.item_id == "suc_7"


def test_una_nota_de_voz_llega_como_audio():
    payload = mensaje_de_instagram()
    payload["entry"][0]["messaging"][0]["message"]["attachments"] = [
        {"type": "audio", "payload": {"url": "https://cdn.meta/audio.mp4"}}
    ]
    _, evento = meta.desglosar(payload)[0]
    entrante = meta.MetaAdapter("instagram", "TOKEN").parse_incoming(evento)
    assert entrante.tipo == "audio"
    assert entrante.media_url == "https://cdn.meta/audio.mp4"


# --- envio ---

def test_la_ui_se_recorta_a_los_limites_de_meta():
    """Meta admite 13 quick replies y corta el titulo a 20 caracteres."""
    adaptador = meta.MetaAdapter("messenger", "TOKEN")
    opciones = [
        Opcion(id=f"fecha_{i}", etiqueta="Lunes 20 de julio 2026"[:24])
        for i in range(10)
    ]
    salida = OutgoingMessage(
        texto="¿Qué día te queda?",
        ui=UIElement(tipo="lista", titulo="Días", opciones=opciones),
    )
    cuerpo = adaptador._cuerpo("PSID", salida)
    respuestas = cuerpo["message"]["quick_replies"]
    assert len(respuestas) == 10
    assert all(len(r["title"]) <= 20 for r in respuestas)
    assert respuestas[0]["payload"] == "fecha_0"
    assert cuerpo["recipient"] == {"id": "PSID"}


def test_sin_ui_no_manda_quick_replies():
    adaptador = meta.MetaAdapter("instagram", "TOKEN")
    cuerpo = adaptador._cuerpo("IGSID", OutgoingMessage(texto="listo"))
    assert "quick_replies" not in cuerpo["message"]
    assert cuerpo["message"]["text"] == "listo"


def test_solo_se_registran_las_redes_con_token():
    assert meta.adaptadores_configurados("", "") == {}
    solo_ig = meta.adaptadores_configurados("TOKEN_IG", "")
    assert list(solo_ig) == ["instagram"]
    ambas = meta.adaptadores_configurados("TOKEN_IG", "TOKEN_FB")
    assert sorted(ambas) == ["instagram", "messenger"]
    assert ambas["messenger"].canal == "messenger"


# --- webhook completo ---

def test_un_mensaje_de_instagram_llega_al_agente(cliente):
    cuerpo = json.dumps(mensaje_de_instagram("hola")).encode()
    despachados = []

    with patch.object(
        app.state, "adapters",
        {"instagram": meta.MetaAdapter("instagram", "TOKEN")},
        create=True,
    ), patch(
        "sidhe_agent.main._mensaje_ya_procesado", AsyncMock(return_value=False)
    ), patch(
        "sidhe_agent.main._guardar_mensaje", AsyncMock()
    ), patch(
        "sidhe_agent.main.procesar_mensaje",
        AsyncMock(side_effect=lambda _app, e: despachados.append(e)),
    ):
        r = cliente.post(
            "/webhooks/meta", content=cuerpo,
            headers={"X-Hub-Signature-256": firmar(cuerpo)},
        )

    assert r.status_code == 200
    assert len(despachados) == 1
    assert despachados[0].canal == "instagram"
    assert despachados[0].user_id == "IGSID_CLIENTE"


def test_un_reintento_de_meta_no_contesta_dos_veces(cliente):
    cuerpo = json.dumps(mensaje_de_instagram()).encode()
    despachados = []

    with patch.object(
        app.state, "adapters",
        {"instagram": meta.MetaAdapter("instagram", "TOKEN")},
        create=True,
    ), patch(
        "sidhe_agent.main._mensaje_ya_procesado", AsyncMock(return_value=True)
    ), patch(
        "sidhe_agent.main.procesar_mensaje",
        AsyncMock(side_effect=lambda _app, e: despachados.append(e)),
    ):
        r = cliente.post(
            "/webhooks/meta", content=cuerpo,
            headers={"X-Hub-Signature-256": firmar(cuerpo)},
        )

    assert r.status_code == 200
    assert despachados == []


def test_un_mensaje_de_una_red_sin_token_no_truena(cliente):
    """Si llega de Messenger y solo configuramos Instagram, se registra y ya."""
    de_facebook = mensaje_de_instagram()
    de_facebook["object"] = "page"
    cuerpo = json.dumps(de_facebook).encode()

    with patch.object(
        app.state, "adapters",
        {"instagram": meta.MetaAdapter("instagram", "TOKEN")},
        create=True,
    ):
        r = cliente.post(
            "/webhooks/meta", content=cuerpo,
            headers={"X-Hub-Signature-256": firmar(cuerpo)},
        )
    assert r.status_code == 200
