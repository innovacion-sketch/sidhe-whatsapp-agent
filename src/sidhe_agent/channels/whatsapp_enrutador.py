"""Un solo WhatsApp hacia adentro, dos proveedores hacia afuera.

La salida de Twilio es número por número: primero el 0202, y el 5164
cuando el 0202 lleve unos días sin problemas. Mientras tanto conviven los
dos, y a cada cliente hay que contestarle por el proveedor que hoy tiene el
número al que escribió. Si la respuesta a un cliente del 0202 saliera por
Twilio después de migrarlo, Twilio la rechaza: ese número ya no es suyo.

El resto del bot no se entera: habla con un solo adaptador de canal
"whatsapp", y este decide por dónde sale cada mensaje. Cuando el último
número esté en Meta y se quiten las credenciales de Twilio, todo sale por
Meta sin tocar nada más.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from .base import ChannelAdapter
from .schemas import IncomingMessage, OutgoingMessage
from .whatsapp_cloud import WhatsAppCloudAdapter, clave_numero

logger = structlog.get_logger(__name__)

ResolverRemitente = Callable[[str], Awaitable[str | None]]


class WhatsAppEnrutador(ChannelAdapter):
    canal = "whatsapp"

    def __init__(
        self,
        twilio: Any | None,
        cloud: WhatsAppCloudAdapter,
        numeros_en_meta: set[str],
        resolver_remitente: ResolverRemitente,
        numero_por_defecto: str = "",
    ) -> None:
        self.twilio = twilio
        self.cloud = cloud
        self._en_meta = {clave_numero(n) for n in numeros_en_meta}
        self._resolver = resolver_remitente
        self._por_defecto = clave_numero(numero_por_defecto)

    def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage:
        """Solo lo usa el webhook de Twilio; el de Meta usa su adaptador."""
        if self.twilio is None:
            raise RuntimeError("llegó un webhook de Twilio sin Twilio configurado")
        return self.twilio.parse_incoming(payload)

    async def proveedor_para(self, user_id: str) -> Any:
        """Twilio o Meta, según a qué número le escribió este cliente.

        Sin Twilio ya no hay nada que decidir. Si no se sabe a qué número
        escribió (o falla la consulta), manda el número por defecto.
        """
        if self.twilio is None:
            return self.cloud
        try:
            numero = clave_numero(await self._resolver(user_id) or "")
        except Exception:
            logger.exception("error_resolviendo_numero_del_negocio")
            numero = ""
        return self.cloud if (numero or self._por_defecto) in self._en_meta else self.twilio

    async def send(self, user_id: str, mensaje: OutgoingMessage) -> str | None:
        proveedor = await self.proveedor_para(user_id)
        return await proveedor.send(user_id, mensaje)

    async def enviar_recordatorio(self, telefono: str, datos: dict[str, str]) -> str | None:
        proveedor = await self.proveedor_para(telefono)
        return await proveedor.enviar_recordatorio(telefono, datos)

    async def bajar_audio(self, media_url: str) -> tuple[bytes, str] | None:
        """Las notas de voz de Meta llegan como id; las de Twilio, como URL."""
        if media_url.startswith("http"):
            return None  # Twilio: la baja transcription con su usuario
        return await self.cloud.descargar_media(media_url)
