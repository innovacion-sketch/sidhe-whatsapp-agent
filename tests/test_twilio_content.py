"""Tests de los payloads de la Content API (sin red)."""

from sidhe_agent.channels.schemas import Opcion, OutgoingMessage, UIElement
from sidhe_agent.services.twilio_content import (
    payload_list_picker,
    payload_para_ui,
    payload_quick_reply,
)


def _mensaje_lista() -> OutgoingMessage:
    return OutgoingMessage(
        texto="Elige tu sucursal:",
        ui=UIElement(
            tipo="lista",
            titulo="Un título de botón demasiado largo para WhatsApp",
            opciones=[
                Opcion(
                    id="suc_1",
                    etiqueta="Una etiqueta larguísima que excede el límite",
                    descripcion="Una descripción realmente muy larga que definitivamente excede los setenta y dos caracteres permitidos",
                ),
                Opcion(id="suc_2", etiqueta="Polanco"),
            ],
        ),
    )


def test_list_picker_estructura_y_truncado():
    payload = payload_list_picker(_mensaje_lista())
    tipo = payload["types"]["twilio/list-picker"]
    assert payload["language"] == "es"
    assert tipo["body"] == "Elige tu sucursal:"
    assert len(tipo["button"]) <= 20
    item_largo = tipo["items"][0]
    assert len(item_largo["item"]) <= 24
    assert len(item_largo["description"]) <= 72
    assert item_largo["id"] == "suc_1"
    # La opción sin descripción no manda el campo
    assert "description" not in tipo["items"][1]


def test_quick_reply_estructura_y_truncado():
    mensaje = OutgoingMessage(
        texto="¿Confirmamos tu cita?",
        ui=UIElement(
            tipo="botones",
            titulo="Confirmación",
            opciones=[
                Opcion(id="confirmar", etiqueta="Confirmar cita del lunes ✅"),
                Opcion(id="cambiar", etiqueta="Cambiar"),
            ],
        ),
    )
    payload = payload_quick_reply(mensaje)
    tipo = payload["types"]["twilio/quick-reply"]
    assert tipo["body"] == "¿Confirmamos tu cita?"
    assert len(tipo["actions"]) == 2
    assert all(len(accion["title"]) <= 20 for accion in tipo["actions"])
    assert tipo["actions"][0]["id"] == "confirmar"


def test_payload_para_ui_enruta_por_tipo():
    assert "twilio/list-picker" in payload_para_ui(_mensaje_lista())["types"]
