"""Instagram DM y Facebook Messenger por la API de Meta.

Los dos canales hablan el mismo protocolo (Messenger Platform), así que el
mismo adaptador sirve para ambos; lo único que cambia es el `object` del
webhook ("instagram" o "page") y el token con el que se responde.

Diferencias con WhatsApp que importan:
- La UI interactiva son *quick replies*: máximo 13, y el título se corta a 20
  caracteres (WhatsApp permite 24). Se recortan aquí, no en el agente.
- Meta reintenta los webhooks, y además nos devuelve nuestros propios
  mensajes marcados con `is_echo`. Ignorarlos es obligatorio: sin eso el bot
  se contesta a sí mismo en un bucle infinito.
- Un webhook puede traer varios eventos de varias conversaciones en un solo
  POST, por eso `desglosar` devuelve una lista.
"""

import hashlib
import hmac
from typing import Any

import httpx
import structlog

from .base import ChannelAdapter
from .schemas import IncomingMessage, OutgoingMessage

logger = structlog.get_logger(__name__)

API_VERSION = "v21.0"
BASE_GRAPH = "https://graph.facebook.com"

# El campo `object` del webhook dice de qué red viene
OBJETO_A_CANAL = {"instagram": "instagram", "page": "messenger"}

MAX_QUICK_REPLIES = 13
MAX_TITULO_QUICK_REPLY = 20
TIMEOUT = httpx.Timeout(15.0)


def validar_firma(cuerpo: bytes, cabecera: str, app_secret: str) -> bool:
    """Comprueba la X-Hub-Signature-256 que manda Meta.

    Sin esto, cualquiera que conozca la URL puede inyectar mensajes falsos.
    Se calcula sobre el cuerpo CRUDO: reserializar el JSON cambia el hash.
    """
    if not app_secret or not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(
        app_secret.encode("utf-8"), cuerpo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperado, cabecera[len("sha256="):])


def desglosar(payload: dict[str, Any]) -> list[tuple[str, dict]]:
    """Webhook completo → [(canal, evento de mensajería)].

    Descarta lo que no es un mensaje del cliente: ecos de lo que nosotros
    mandamos, acuses de entrega y de lectura.
    """
    canal = OBJETO_A_CANAL.get(payload.get("object", ""))
    if not canal:
        return []

    eventos = []
    for entrada in payload.get("entry", []):
        for evento in entrada.get("messaging", []):
            mensaje = evento.get("message") or {}
            if mensaje.get("is_echo"):
                continue  # es nuestro propio mensaje de vuelta
            if not mensaje and not evento.get("postback"):
                continue  # delivery, read, reaction…
            eventos.append((canal, evento))
    return eventos


class MetaAdapter(ChannelAdapter):
    """Un adaptador por red: comparten código pero no token ni canal."""

    def __init__(
        self,
        canal: str,
        page_access_token: str,
        api_version: str = API_VERSION,
    ) -> None:
        self.canal = canal
        self._token = page_access_token
        self._url = f"{BASE_GRAPH}/{api_version}/me/messages"

    def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage:
        """Un evento de mensajería (ya desglosado) → IncomingMessage."""
        remitente = (payload.get("sender") or {}).get("id", "")
        mensaje = payload.get("message") or {}
        postback = payload.get("postback") or {}

        # Botón del menú persistente: el id exacto viene en el payload
        if postback:
            return IncomingMessage(
                canal=self.canal,
                user_id=remitente,
                tipo="seleccion_interactiva",
                contenido=postback.get("title", ""),
                item_id=postback.get("payload"),
                message_sid=mensaje.get("mid"),
            )

        # Quick reply: el cliente tocó una opción que le ofrecimos
        quick_reply = mensaje.get("quick_reply") or {}
        if quick_reply.get("payload"):
            return IncomingMessage(
                canal=self.canal,
                user_id=remitente,
                tipo="seleccion_interactiva",
                contenido=mensaje.get("text", ""),
                item_id=quick_reply["payload"],
                message_sid=mensaje.get("mid"),
            )

        # Nota de voz: se transcribe antes de entrar al grafo
        for adjunto in mensaje.get("attachments") or []:
            if adjunto.get("type") == "audio":
                return IncomingMessage(
                    canal=self.canal,
                    user_id=remitente,
                    tipo="audio",
                    media_url=(adjunto.get("payload") or {}).get("url"),
                    media_content_type="audio/mp4",
                    message_sid=mensaje.get("mid"),
                )

        return IncomingMessage(
            canal=self.canal,
            user_id=remitente,
            tipo="texto",
            contenido=mensaje.get("text", ""),
            message_sid=mensaje.get("mid"),
        )

    def _cuerpo(self, user_id: str, mensaje: OutgoingMessage) -> dict:
        contenido: dict[str, Any] = {"text": mensaje.texto}
        if mensaje.ui:
            contenido["quick_replies"] = [
                {
                    "content_type": "text",
                    "title": opcion.etiqueta[:MAX_TITULO_QUICK_REPLY],
                    "payload": opcion.id,
                }
                for opcion in mensaje.ui.opciones[:MAX_QUICK_REPLIES]
            ]
        return {
            "recipient": {"id": user_id},
            "messaging_type": "RESPONSE",
            "message": contenido,
        }

    async def send(self, user_id: str, mensaje: OutgoingMessage) -> str | None:
        """Manda la respuesta. Devuelve el mid, o None si Meta la rechazó."""
        if not self._token:
            logger.warning("meta_sin_token", canal=self.canal)
            return None
        async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
            respuesta = await cliente.post(
                self._url,
                params={"access_token": self._token},
                json=self._cuerpo(user_id, mensaje),
            )
        if respuesta.status_code >= 400:
            # El motivo casi siempre es la ventana de 24 h vencida
            logger.warning(
                "meta_envio_rechazado",
                canal=self.canal,
                status=respuesta.status_code,
                detalle=respuesta.text[:300],
            )
            respuesta.raise_for_status()
        return respuesta.json().get("message_id")


def adaptadores_configurados(
    token_instagram: str, token_messenger: str
) -> dict[str, MetaAdapter]:
    """Solo se registran las redes que tienen token: lo demás no existe."""
    registro = {}
    if token_instagram:
        registro["instagram"] = MetaAdapter("instagram", token_instagram)
    if token_messenger:
        registro["messenger"] = MetaAdapter("messenger", token_messenger)
    return registro
