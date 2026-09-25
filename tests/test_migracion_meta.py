"""Salir de Twilio número por número sin que se pierda una respuesta.

Durante la migración conviven los dos proveedores: el 0202 ya en Meta y el
5164 todavía en Twilio. Lo que estos tests cuidan es que cada respuesta,
cada recordatorio y cada nota de voz vayan por el proveedor que HOY tiene
el número al que escribió el cliente. Si la respuesta de un cliente del
0202 saliera por Twilio después de migrarlo, Twilio la rechaza y el
cliente se queda sin respuesta.
"""

import hashlib
import hmac
import json
import types
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidhe_agent import main
from sidhe_agent.channels.whatsapp_cloud import (
    BOTONES_RECORDATORIO,
    EJEMPLO_RECORDATORIO,
    WhatsAppCloudAdapter,
    clave_numero,
    cuerpo_recordatorio,
    fallidos,
    plantilla_recordatorio,
)
from sidhe_agent.channels.whatsapp_enrutador import WhatsAppEnrutador
from sidhe_agent.config import Settings, get_settings

NUM_0202 = "+5215638950202"   # ya en Meta
NUM_5164 = "+5215638955164"   # sigue en Twilio


class _Proveedor:
    """Twilio o Meta de mentira: anota lo que se le pidió mandar."""

    def __init__(self, nombre):
        self.nombre = nombre
        self.enviados = []
        self.recordatorios = []

    async def send(self, user_id, mensaje):
        self.enviados.append(user_id)
        return f"{self.nombre}-sid"

    async def enviar_recordatorio(self, telefono, datos):
        self.recordatorios.append((telefono, datos))
        return f"{self.nombre}-rec"

    async def descargar_media(self, media_id):
        return (b"audio", "audio/ogg")

    def parse_incoming(self, payload):
        return "parseado por twilio"


def _enrutador(escribio_a, twilio=True):
    async def resolver(user_id):
        if isinstance(escribio_a, Exception):
            raise escribio_a
        return escribio_a

    t, c = _Proveedor("twilio"), _Proveedor("meta")
    return WhatsAppEnrutador(
        t if twilio else None, c, {NUM_0202}, resolver, numero_por_defecto=NUM_5164
    ), t, c


# --- por donde sale cada cosa ---

async def test_al_cliente_del_numero_migrado_se_le_contesta_por_meta():
    enrutador, twilio, meta = _enrutador(NUM_0202)
    await enrutador.send("+5212283407867", None)
    assert meta.enviados == ["+5212283407867"] and twilio.enviados == []


async def test_al_cliente_del_numero_sin_migrar_se_le_contesta_por_twilio():
    enrutador, twilio, meta = _enrutador(NUM_5164)
    await enrutador.send("+521228", None)
    assert twilio.enviados == ["+521228"] and meta.enviados == []


async def test_el_uno_de_los_celulares_de_mexico_no_confunde_al_enrutador():
    """Twilio pudo guardar +52 1 56… y Meta reportar +52 56…: es el mismo."""
    enrutador, twilio, meta = _enrutador("+525638950202")
    await enrutador.send("+521228", None)
    assert meta.enviados == ["+521228"]


async def test_si_no_se_sabe_a_que_numero_escribio_manda_el_de_siempre():
    enrutador, twilio, meta = _enrutador(None)
    await enrutador.send("+521228", None)
    assert twilio.enviados == ["+521228"]  # el por defecto (5164) sigue en Twilio


async def test_si_falla_la_consulta_igual_sale_por_algun_lado():
    enrutador, twilio, meta = _enrutador(RuntimeError("base caída"))
    await enrutador.send("+521228", None)
    assert twilio.enviados == ["+521228"]


async def test_sin_twilio_todo_sale_por_meta():
    """El día que se quiten las credenciales de Twilio, no hay que tocar nada."""
    enrutador, _, meta = _enrutador(NUM_5164, twilio=False)
    await enrutador.send("+521228", None)
    assert meta.enviados == ["+521228"]


async def test_el_recordatorio_sale_por_el_mismo_proveedor():
    enrutador, twilio, meta = _enrutador(NUM_0202)
    await enrutador.enviar_recordatorio("+521228", {"nombre": "Diana"})
    assert meta.recordatorios == [("+521228", {"nombre": "Diana"})]
    assert twilio.recordatorios == []


async def test_la_nota_de_voz_de_meta_se_canjea_y_la_de_twilio_no():
    enrutador, _, _ = _enrutador(NUM_0202)
    assert await enrutador.bajar_audio("wamid-media-123") == (b"audio", "audio/ogg")
    assert await enrutador.bajar_audio("https://api.twilio.com/media/1") is None


def test_el_webhook_de_twilio_sigue_parseando_con_twilio():
    enrutador, _, _ = _enrutador(NUM_5164)
    assert enrutador.parse_incoming({}) == "parseado por twilio"


def test_clave_numero():
    assert clave_numero("+5215638950202") == clave_numero("+52 56 3895 0202")
    assert clave_numero("whatsapp:+5215638950202") == "525638950202"
    # Un número que no es celular mexicano se queda como está
    assert clave_numero("+14155550100") == "14155550100"


# --- la plantilla nueva ---

def test_la_plantilla_trae_ejemplo_para_cada_variable():
    """Meta la rechaza si el ejemplo no cubre todas las variables."""
    plantilla = plantilla_recordatorio("sidhe_recordatorio_cita", "es_MX")
    cuerpo = plantilla["components"][0]
    variables = cuerpo["text"].count("{{")
    assert variables == 5
    assert len(cuerpo["example"]["body_text"][0]) == variables == len(EJEMPLO_RECORDATORIO)
    assert plantilla["category"] == "UTILITY"
    assert [b["text"] for b in plantilla["components"][1]["buttons"]] == [
        texto for _, texto in BOTONES_RECORDATORIO
    ]


def test_el_recordatorio_usa_solo_el_primer_nombre_y_la_direccion():
    cuerpo = cuerpo_recordatorio(
        "+521228",
        {"nombre": "Diana Kristal Bravo", "sucursal": "Liverpool Xalapa",
         "direccion": "Carr. Xalapa-Veracruz Km 2", "fecha": "sáb 27 sep", "hora": "11:00"},
        "sidhe_recordatorio_cita", "es_MX",
    )
    valores = [p["text"] for p in cuerpo["template"]["components"][0]["parameters"]]
    assert valores == ["Diana", "Liverpool Xalapa", "Carr. Xalapa-Veracruz Km 2", "sáb 27 sep", "11:00"]


def test_sin_direccion_no_se_manda_un_parametro_vacio():
    """Meta rechaza el envío completo con un parámetro vacío (Atizapán no tiene)."""
    cuerpo = cuerpo_recordatorio("+521", {"nombre": "Ana", "direccion": ""}, "p", "es_MX")
    valores = [p["text"] for p in cuerpo["template"]["components"][0]["parameters"]]
    assert all(v.strip() for v in valores)


def test_los_saltos_de_linea_se_limpian():
    """Meta rechaza parámetros con saltos de línea o muchos espacios."""
    cuerpo = cuerpo_recordatorio(
        "+521", {"nombre": "Ana", "direccion": "Calle 1\nCol.     Centro"}, "p", "es_MX"
    )
    assert cuerpo["template"]["components"][0]["parameters"][2]["text"] == "Calle 1 Col. Centro"


def test_cada_boton_devuelve_el_id_que_entiende_el_bot():
    cuerpo = cuerpo_recordatorio("+521", {"nombre": "Ana"}, "p", "es_MX")
    botones = cuerpo["template"]["components"][1:]
    assert [b["parameters"][0]["payload"] for b in botones] == [
        "recordatorio_confirmar", "recordatorio_reagendar", "recordatorio_cancelar",
    ]


def test_los_envios_fallidos_se_detectan():
    payload = {"entry": [{"changes": [{"value": {"statuses": [
        {"status": "delivered", "recipient_id": "1"},
        {"status": "failed", "recipient_id": "2", "errors": [{"code": 131047}]},
    ]}}]}]}
    assert [e["recipient_id"] for e in fallidos(payload)] == ["2"]


# --- configuracion ---

def test_los_numeros_en_meta_se_leen_de_una_variable():
    ajustes = Settings(whatsapp_cloud_numeros="+52 1 56 3895 0202=111; +5215638955164=222")
    assert ajustes.whatsapp_cloud_mapa == {NUM_0202: "111", NUM_5164: "222"}


def test_un_par_mal_escrito_no_se_lee_a_medias():
    """Leído a medias, las respuestas de ese número saldrían por Twilio."""
    with pytest.raises(ValueError):
        _ = Settings(whatsapp_cloud_numeros="+5215638950202=abc").whatsapp_cloud_mapa


def test_sin_numeros_en_meta_todo_sigue_como_siempre():
    twilio = object()
    ajustes = Settings(whatsapp_cloud_token="t", whatsapp_cloud_numeros="")
    assert main.adaptador_whatsapp(ajustes, twilio) is twilio


def test_con_un_numero_en_meta_se_arma_el_enrutador():
    ajustes = Settings(whatsapp_cloud_token="t", whatsapp_cloud_numeros=f"{NUM_0202}=111")
    adaptador = main.adaptador_whatsapp(ajustes, object())
    assert isinstance(adaptador, WhatsAppEnrutador)
    assert isinstance(adaptador.cloud, WhatsAppCloudAdapter)


# --- el webhook ---

SECRETO = "secreto-de-prueba"


def _firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()


def _mensaje(texto="hola"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"display_phone_number": "5215638950202", "phone_number_id": "111"},
            "contacts": [{"wa_id": "5212283407867", "profile": {"name": "Diana"}}],
            "messages": [{"from": "5212283407867", "id": "wamid.X", "type": "text",
                          "text": {"body": texto}}],
        }}]}],
    }


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(get_settings(), "meta_app_secret", SECRETO, raising=False)
    monkeypatch.setattr(get_settings(), "meta_verify_token", "palabra", raising=False)
    cloud = WhatsAppCloudAdapter(phone_number_id="111", token="t")
    cloud.marcar_leido = AsyncMock()
    enrutador = WhatsAppEnrutador(object(), cloud, {NUM_0202}, AsyncMock(return_value=None))
    monkeypatch.setattr(main.app.state, "adapter", enrutador, raising=False)
    return TestClient(main.app), cloud


def test_meta_puede_dar_de_alta_la_url(cliente):
    tc, _ = cliente
    r = tc.get("/webhooks/whatsapp/cloud", params={
        "hub.mode": "subscribe", "hub.verify_token": "palabra", "hub.challenge": "42",
    })
    assert r.status_code == 200 and r.text == "42"


def test_una_firma_falsa_se_rechaza(cliente):
    tc, _ = cliente
    cuerpo = json.dumps(_mensaje()).encode()
    r = tc.post("/webhooks/whatsapp/cloud", content=cuerpo,
                headers={"X-Hub-Signature-256": "sha256=falsa"})
    assert r.status_code == 403


def test_un_mensaje_se_guarda_con_su_numero_y_se_despacha(cliente):
    tc, cloud = cliente
    cuerpo = json.dumps(_mensaje()).encode()
    with (
        patch("sidhe_agent.main._mensaje_ya_procesado", AsyncMock(return_value=False)),
        patch("sidhe_agent.main._guardar_mensaje", AsyncMock()) as guardar,
        patch("sidhe_agent.main.procesar_mensaje", AsyncMock()) as procesar,
    ):
        r = tc.post("/webhooks/whatsapp/cloud", content=cuerpo,
                    headers={"X-Hub-Signature-256": _firmar(cuerpo)})

    assert r.status_code == 200
    # El número al que escribió queda guardado: de ahí sale el enrutamiento
    assert guardar.await_args.kwargs["numero_negocio"] == "+5215638950202"
    entrante = procesar.await_args.args[1]
    assert entrante.user_id == "+5212283407867"
    assert entrante.contenido == "hola"
    cloud.marcar_leido.assert_awaited_once_with("wamid.X", "111")


def test_un_reintento_no_se_contesta_dos_veces(cliente):
    tc, _ = cliente
    cuerpo = json.dumps(_mensaje()).encode()
    with (
        patch("sidhe_agent.main._mensaje_ya_procesado", AsyncMock(return_value=True)),
        patch("sidhe_agent.main.procesar_mensaje", AsyncMock()) as procesar,
    ):
        tc.post("/webhooks/whatsapp/cloud", content=cuerpo,
                headers={"X-Hub-Signature-256": _firmar(cuerpo)})
    procesar.assert_not_awaited()


def test_sin_numeros_en_meta_el_webhook_no_revienta(cliente, monkeypatch):
    tc, _ = cliente
    monkeypatch.setattr(main.app.state, "adapter", types.SimpleNamespace(), raising=False)
    cuerpo = json.dumps(_mensaje()).encode()
    with patch("sidhe_agent.main.procesar_mensaje", AsyncMock()) as procesar:
        r = tc.post("/webhooks/whatsapp/cloud", content=cuerpo,
                    headers={"X-Hub-Signature-256": _firmar(cuerpo)})
    assert r.status_code == 200
    procesar.assert_not_awaited()
