"""ABC de adaptadores de canal. El núcleo del agente solo conoce estos contratos.

El grafo, las tools y la memoria son agnósticos al canal: todo lo específico
de WhatsApp/Twilio, Chatwoot (Messenger/Instagram) o futuros canales vive en
un ChannelAdapter. Para agregar un canal nuevo:

1. Implementa `parse_incoming(payload) -> IncomingMessage`, normalizando:
   - canal: identificador estable del canal (ej. "whatsapp", "chatwoot").
   - user_id: id único del usuario EN ese canal (teléfono E.164, contact id).
     El thread de memoria se deriva como f"{canal}:{user_id}", así que el par
     (canal, user_id) debe ser estable entre mensajes del mismo usuario.
   - tipo: "texto" | "audio" | "seleccion_interactiva". Para selecciones,
     item_id trae el id EXACTO de la opción tocada (el agente no interpreta
     texto libre); para audio, media_url/media_content_type permiten
     transcribir antes de entrar al grafo.

2. Implementa `send(user_id, mensaje)`. OutgoingMessage.ui (lista/botones) se
   renderiza al formato nativo del canal respetando sus límites — si el canal
   no soporta UI interactiva, degrada a texto numerado (ver
   WhatsAppTwilioAdapter._render_texto como referencia).

3. Expón un webhook en main.py que valide la autenticidad del canal,
   deduplique reintentos, guarde el mensaje en `mensajes` (auditoría) y
   despache a procesar_mensaje() en background.

El núcleo garantiza a cambio: memoria por thread, perfil de largo plazo,
FAQs/tools anti-alucinación y el flujo de citas — sin cambios por canal.
"""

from abc import ABC, abstractmethod
from typing import Any

from .schemas import IncomingMessage, OutgoingMessage


class ChannelAdapter(ABC):
    """Traduce entre el formato nativo del canal y los contratos internos."""

    canal: str

    @abstractmethod
    def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage:
        """Normaliza el payload crudo del webhook del canal a un IncomingMessage."""

    @abstractmethod
    async def send(self, user_id: str, mensaje: OutgoingMessage) -> str | None:
        """Envía la respuesta al usuario. Devuelve el id del mensaje en el proveedor."""
