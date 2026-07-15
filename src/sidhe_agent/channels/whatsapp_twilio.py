"""Adaptador de WhatsApp vía Twilio: normalización de webhooks y envío de mensajes.

Los mensajes con UI se envían como list-picker/quick-reply vía Content API
(services/twilio_content.py). Si la Content API falla, la UI se degrada a
texto numerado para que el cliente nunca se quede sin respuesta.
"""

import asyncio
from typing import Any

import structlog
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from .base import ChannelAdapter
from .schemas import IncomingMessage, OutgoingMessage

logger = structlog.get_logger(__name__)

PREFIJO_WHATSAPP = "whatsapp:"


def validar_firma(url: str, params: dict[str, Any], firma: str, auth_token: str) -> bool:
    """Valida la cabecera X-Twilio-Signature de un webhook."""
    if not firma or not auth_token:
        return False
    return RequestValidator(auth_token).validate(url, params, firma)


class WhatsAppTwilioAdapter(ChannelAdapter):
    canal = "whatsapp"

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from = from_number
        self._client: Client | None = None

    @property
    def client(self) -> Client:
        # Lazy: permite instanciar el adapter en tests sin credenciales reales.
        if self._client is None:
            self._client = Client(self._account_sid, self._auth_token)
        return self._client

    def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage:
        user_id = payload.get("From", "").removeprefix(PREFIJO_WHATSAPP)
        message_sid = payload.get("MessageSid") or payload.get("SmsMessageSid")
        nombre_perfil = payload.get("ProfileName") or None

        # Selección interactiva: Twilio manda el id exacto del ítem tocado
        # (ListId para list-picker, ButtonPayload para quick-reply).
        item_id = payload.get("ListId") or payload.get("ButtonPayload")
        if item_id:
            etiqueta = (
                payload.get("ListTitle")
                or payload.get("ButtonText")
                or payload.get("Body", "")
            )
            return IncomingMessage(
                canal=self.canal,
                user_id=user_id,
                tipo="seleccion_interactiva",
                contenido=etiqueta,
                item_id=item_id,
                message_sid=message_sid,
                nombre_perfil=nombre_perfil,
            )

        # Nota de voz / audio adjunto.
        num_media = int(payload.get("NumMedia", "0") or 0)
        content_type = payload.get("MediaContentType0", "")
        if num_media > 0 and content_type.startswith("audio"):
            return IncomingMessage(
                canal=self.canal,
                user_id=user_id,
                tipo="audio",
                contenido=payload.get("Body", ""),
                media_url=payload.get("MediaUrl0"),
                media_content_type=content_type,
                message_sid=message_sid,
                nombre_perfil=nombre_perfil,
            )

        return IncomingMessage(
            canal=self.canal,
            user_id=user_id,
            tipo="texto",
            contenido=payload.get("Body", ""),
            message_sid=message_sid,
            nombre_perfil=nombre_perfil,
        )

    async def send(self, user_id: str, mensaje: OutgoingMessage) -> str | None:
        to = f"{PREFIJO_WHATSAPP}{user_id}"
        if mensaje.ui is not None:
            try:
                return await self._send_interactivo(to, mensaje)
            except Exception:
                logger.exception("content_api_fallo_degradando_a_texto")
        # El SDK de Twilio es síncrono; se ejecuta fuera del event loop.
        msg = await asyncio.to_thread(
            self.client.messages.create,
            from_=self._from,
            to=to,
            body=self._render_texto(mensaje),
        )
        return msg.sid

    async def _send_interactivo(self, to: str, mensaje: OutgoingMessage) -> str | None:
        from ..services.twilio_content import crear_content_para_ui

        content_sid = await crear_content_para_ui(
            mensaje, self._account_sid, self._auth_token
        )
        msg = await asyncio.to_thread(
            self.client.messages.create,
            from_=self._from,
            to=to,
            content_sid=content_sid,
        )
        return msg.sid

    def _render_texto(self, mensaje: OutgoingMessage) -> str:
        if mensaje.ui is None:
            return mensaje.texto
        # Fallback: la UI interactiva se degrada a lista numerada en texto.
        lineas = [mensaje.texto, "", mensaje.ui.titulo]
        for i, opcion in enumerate(mensaje.ui.opciones, start=1):
            linea = f"{i}. {opcion.etiqueta}"
            if opcion.descripcion:
                linea += f" — {opcion.descripcion}"
            lineas.append(linea)
        return "\n".join(lineas)
