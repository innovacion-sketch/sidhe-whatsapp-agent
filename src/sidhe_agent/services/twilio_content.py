"""Content API de Twilio: creación de contenidos interactivos para WhatsApp.

Dentro de la ventana de 24h los mensajes interactivos (twilio/list-picker y
twilio/quick-reply) no requieren aprobación de WhatsApp: se crea el content
al vuelo con los datos dinámicos (sucursales, fechas, horarios) y se envía
por messages.create(content_sid=...).

Límites reales de WhatsApp aplicados por truncamiento:
- botón que abre la lista y títulos de quick-reply: 20 chars
- etiqueta de ítem de lista: 24 chars
- descripción de ítem: 72 chars

Recordatorios de cita (fuera de la ventana de 24h): template `twilio/text`
con variables {{1}}=nombre, {{2}}=sucursal, {{3}}=fecha, {{4}}=hora, que debe
pre-aprobarse con WhatsApp (categoría UTILITY). Se crea una sola vez con
scripts/setup_recordatorio_template.py y su SID se fija en la env var
TWILIO_RECORDATORIO_CONTENT_SID.
"""

import asyncio
import json
import uuid

import httpx

from ..channels.schemas import OutgoingMessage

URL_CONTENT_API = "https://content.twilio.com/v1/Content"

MAX_BOTON = 20
MAX_ITEM = 24
MAX_DESCRIPCION = 72


def _truncar(texto: str, maximo: int) -> str:
    texto = texto.strip()
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def payload_list_picker(mensaje: OutgoingMessage) -> dict:
    assert mensaje.ui is not None and mensaje.ui.tipo == "lista"
    return {
        "friendly_name": f"sidhe_lista_{uuid.uuid4().hex[:12]}",
        "language": "es",
        "types": {
            "twilio/list-picker": {
                "body": mensaje.texto,
                "button": _truncar(mensaje.ui.titulo, MAX_BOTON),
                "items": [
                    {
                        "item": _truncar(opcion.etiqueta, MAX_ITEM),
                        "id": opcion.id,
                        **(
                            {"description": _truncar(opcion.descripcion, MAX_DESCRIPCION)}
                            if opcion.descripcion
                            else {}
                        ),
                    }
                    for opcion in mensaje.ui.opciones
                ],
            }
        },
    }


def payload_quick_reply(mensaje: OutgoingMessage) -> dict:
    assert mensaje.ui is not None and mensaje.ui.tipo == "botones"
    return {
        "friendly_name": f"sidhe_botones_{uuid.uuid4().hex[:12]}",
        "language": "es",
        "types": {
            "twilio/quick-reply": {
                "body": mensaje.texto,
                "actions": [
                    {"title": _truncar(opcion.etiqueta, MAX_BOTON), "id": opcion.id}
                    for opcion in mensaje.ui.opciones
                ],
            }
        },
    }


def payload_para_ui(mensaje: OutgoingMessage) -> dict:
    if mensaje.ui is None:
        raise ValueError("OutgoingMessage sin UI")
    if mensaje.ui.tipo == "lista":
        return payload_list_picker(mensaje)
    return payload_quick_reply(mensaje)


async def crear_content(payload: dict, account_sid: str, auth_token: str) -> str:
    """Crea el content en Twilio y devuelve su content_sid (HX...)."""
    async with httpx.AsyncClient(auth=(account_sid, auth_token), timeout=15.0) as client:
        respuesta = await client.post(URL_CONTENT_API, json=payload)
        respuesta.raise_for_status()
        return respuesta.json()["sid"]


async def crear_content_para_ui(
    mensaje: OutgoingMessage, account_sid: str, auth_token: str
) -> str:
    return await crear_content(payload_para_ui(mensaje), account_sid, auth_token)


# --- Recordatorios de cita (fuera de la ventana de 24h) --------------------

CUERPO_RECORDATORIO = (
    "Hola {{1}}, te recordamos tu cita de estudio de pisada en {{2}} "
    "el {{3}} a las {{4}}. Te recomendamos llegar 10 minutos antes. "
    "Si necesitas mover tu cita, responde a este mensaje."
)


def payload_template_recordatorio() -> dict:
    return {
        "friendly_name": "sidhe_recordatorio_cita",
        "language": "es",
        "variables": {"1": "nombre", "2": "sucursal", "3": "fecha", "4": "hora"},
        "types": {"twilio/text": {"body": CUERPO_RECORDATORIO}},
    }


async def solicitar_aprobacion_whatsapp(
    content_sid: str, account_sid: str, auth_token: str
) -> dict:
    """Somete el template a aprobación de WhatsApp (categoría UTILITY)."""
    url = f"{URL_CONTENT_API}/{content_sid}/ApprovalRequests/whatsapp"
    async with httpx.AsyncClient(auth=(account_sid, auth_token), timeout=15.0) as client:
        respuesta = await client.post(
            url, json={"name": "sidhe_recordatorio_cita", "category": "UTILITY"}
        )
        respuesta.raise_for_status()
        return respuesta.json()


async def enviar_recordatorio(
    client,
    from_number: str,
    telefono: str,
    content_sid: str,
    variables: dict[str, str],
) -> str:
    """Envía el template de recordatorio con sus variables. Devuelve el sid."""
    msg = await asyncio.to_thread(
        client.messages.create,
        from_=from_number,
        to=f"whatsapp:{telefono}",
        content_sid=content_sid,
        content_variables=json.dumps(variables, ensure_ascii=False),
    )
    return msg.sid
