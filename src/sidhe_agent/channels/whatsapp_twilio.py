"""Adaptador de WhatsApp vía Twilio: normalización de webhooks y envío de mensajes.

Los mensajes con UI se envían como list-picker/quick-reply vía Content API
(services/twilio_content.py). Si la Content API falla, la UI se degrada a
texto numerado para que el cliente nunca se quede sin respuesta.

Varios números: el negocio puede tener más de un número de WhatsApp en la
misma cuenta de Twilio. Al cliente se le contesta SIEMPRE desde el número al
que escribió (el `To` de su último mensaje); si no, recibiría la respuesta de
un contacto que no tiene guardado. Cuando no se sabe, sale del número por
defecto.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from .adjuntos import describir_archivo, describir_ubicacion
from .base import ChannelAdapter
from .schemas import IncomingMessage, OutgoingMessage

logger = structlog.get_logger(__name__)

PREFIJO_WHATSAPP = "whatsapp:"

# Dado un cliente, a qué número del negocio le escribió por última vez
ResolverRemitente = Callable[[str], Awaitable[str | None]]


def validar_firma(url: str, params: dict[str, Any], firma: str, auth_token: str) -> bool:
    """Valida la cabecera X-Twilio-Signature de un webhook."""
    if not firma or not auth_token:
        return False
    return RequestValidator(auth_token).validate(url, params, firma)


def _con_prefijo(numero: str) -> str:
    return numero if numero.startswith(PREFIJO_WHATSAPP) else f"{PREFIJO_WHATSAPP}{numero}"


class WhatsAppTwilioAdapter(ChannelAdapter):
    canal = "whatsapp"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        resolver_remitente: ResolverRemitente | None = None,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from = from_number
        self._resolver = resolver_remitente
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
        numero_negocio = payload.get("To", "").removeprefix(PREFIJO_WHATSAPP) or None
        comunes = {
            "canal": self.canal,
            "user_id": user_id,
            "message_sid": message_sid,
            "nombre_perfil": nombre_perfil,
            "numero_negocio": numero_negocio,
        }

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
                **comunes,
                tipo="seleccion_interactiva",
                contenido=etiqueta,
                item_id=item_id,
            )

        # Nota de voz / audio adjunto.
        num_media = int(payload.get("NumMedia", "0") or 0)
        content_type = payload.get("MediaContentType0", "")
        if num_media > 0 and content_type.startswith("audio"):
            return IncomingMessage(
                **comunes,
                tipo="audio",
                contenido=payload.get("Body", ""),
                media_url=payload.get("MediaUrl0"),
                media_content_type=content_type,
            )

        # Foto, sticker, documento o video: se describe, porque el modelo no
        # lo ve y un mensaje vacio lo rechaza la API.
        if num_media > 0:
            return IncomingMessage(
                **comunes,
                tipo="adjunto",
                contenido=describir_archivo(content_type, payload.get("Body", "")),
            )

        # Ubicacion compartida: Twilio la manda sin media, en campos propios
        if payload.get("Latitude"):
            return IncomingMessage(
                **comunes,
                tipo="adjunto",
                contenido=describir_ubicacion(
                    payload.get("Latitude"),
                    payload.get("Longitude"),
                    payload.get("Address", ""),
                    payload.get("Label", ""),
                ),
            )

        return IncomingMessage(**comunes, tipo="texto", contenido=payload.get("Body", ""))

    async def remitente_para(self, user_id: str) -> str:
        """Número desde el que se le contesta a este cliente."""
        if self._resolver is None:
            return self._from
        try:
            numero = await self._resolver(user_id)
        except Exception:
            # Mejor contestar desde el número por defecto que no contestar
            logger.exception("error_resolviendo_numero_del_negocio")
            return self._from
        return _con_prefijo(numero) if numero else self._from

    async def send(self, user_id: str, mensaje: OutgoingMessage) -> str | None:
        to = f"{PREFIJO_WHATSAPP}{user_id}"
        desde = await self.remitente_para(user_id)
        if mensaje.ui is not None:
            try:
                return await self._send_interactivo(to, desde, mensaje)
            except Exception:
                logger.exception("content_api_fallo_degradando_a_texto")
        # El SDK de Twilio es síncrono; se ejecuta fuera del event loop.
        msg = await asyncio.to_thread(
            self.client.messages.create,
            from_=desde,
            to=to,
            body=self._render_texto(mensaje),
        )
        return msg.sid

    async def _send_interactivo(
        self, to: str, desde: str, mensaje: OutgoingMessage
    ) -> str | None:
        from ..services.twilio_content import crear_content_para_ui

        content_sid = await crear_content_para_ui(
            mensaje, self._account_sid, self._auth_token
        )
        msg = await asyncio.to_thread(
            self.client.messages.create,
            from_=desde,
            to=to,
            content_sid=content_sid,
        )
        return msg.sid

    async def enviar_recordatorio(self, telefono: str, datos: dict[str, str]) -> str:
        """La plantilla de Twilio: 4 variables, sin dirección ni botones.

        Es la que ya está aprobada en la cuenta de Twilio; la nueva, con
        botones, vive en la cuenta de Meta (channels/whatsapp_cloud.py).
        """
        from ..config import get_settings
        from ..services.twilio_content import enviar_recordatorio

        content_sid = get_settings().twilio_recordatorio_content_sid
        if not content_sid:
            raise RuntimeError(
                "Falta TWILIO_RECORDATORIO_CONTENT_SID "
                "(ejecuta scripts/setup_recordatorio_template.py)"
            )
        return await enviar_recordatorio(
            self.client,
            # Desde el número por el que agendó, no siempre el de siempre
            await self.remitente_para(telefono),
            telefono,
            content_sid,
            {
                "1": datos.get("nombre", ""),
                "2": datos.get("sucursal", ""),
                "3": datos.get("fecha", ""),
                "4": datos.get("hora", ""),
            },
        )

    async def bajar_audio(self, media_url: str) -> tuple[bytes, str] | None:
        """None: el audio de Twilio lo baja transcription con su usuario."""
        return None

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
