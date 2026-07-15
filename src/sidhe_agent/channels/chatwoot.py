"""Esqueleto documentado del adaptador de Chatwoot (NO implementado).

Vía de entrada de Facebook Messenger, Instagram DM y comentarios públicos de
FB/IG: Meta → Chatwoot (inboxes conectados con Meta Graph API) → este bot como
AgentBot. El núcleo (grafo, tools, memoria) no cambia: solo se implementa este
adapter y su webhook.

## Plan de integración (cuando se implemente)

1. **Entrada — webhook AgentBot.** Crear el AgentBot en Chatwoot (Settings →
   AgentBots) apuntando a `POST /webhooks/chatwoot` de este servicio. Chatwoot
   envía eventos JSON; interesa `event == "message_created"` con
   `message_type == "incoming"`. Campos relevantes del payload:
       payload["conversation"]["id"]          → conversation_id (para responder)
       payload["conversation"]["inbox_id"]    → distingue Messenger/IG/comentarios
       payload["sender"]["id"] / ["name"]     → contacto
       payload["content"]                     → texto del mensaje
       payload["attachments"][n]["data_url"]  → media (audio → transcripción)
   Normalización propuesta:
       canal   = "chatwoot"
       user_id = f"{inbox_id}:{sender_id}"    (estable por contacto e inbox)
   La conversation_id NO es estable a largo plazo → persistir el mapeo
   user_id → conversation_id vigente (tabla pequeña o el propio Store) para
   poder responder y para los recordatorios.

2. **Salida — API de Chatwoot.** Responder con el token del AgentBot:
       POST {base_url}/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages
       Headers: api_access_token: <token>
       Body:    {"content": texto, "message_type": "outgoing"}
   UI interactiva: Chatwoot soporta `content_type: "input_select"` con
   `content_attributes: {"items": [{"title": ..., "value": ...}]}` — mapear
   UIElement (lista/botones) a items; la respuesta del usuario llega como
   mensaje entrante con el value elegido (tratar como seleccion_interactiva).

3. **Handoff a humano.** En vez del interrupt-silencio de WhatsApp, en
   Chatwoot el escalamiento nativo es cambiar el status de la conversación:
       POST .../conversations/{conversation_id}/toggle_status
       Body: {"status": "open"}
   Eso la saca del AgentBot y la asigna al equipo humano en el dashboard.

4. **Comentarios públicos FB/IG.** Llegan por inboxes específicos; misma
   mecánica, pero el system prompt deberá ajustar tono/privacidad (no pedir
   datos personales en público; invitar a DM para agendar).
"""

from typing import Any

from .base import ChannelAdapter
from .schemas import IncomingMessage, OutgoingMessage


class ChatwootAdapter(ChannelAdapter):
    canal = "chatwoot"

    def __init__(self, base_url: str, account_id: int, api_access_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._account_id = account_id
        self._token = api_access_token

    def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage:  # pragma: no cover
        # Ver plan (punto 1): filtrar message_created/incoming, mapear
        # inbox_id:sender_id → user_id y attachments de audio → tipo "audio".
        raise NotImplementedError("ChatwootAdapter.parse_incoming: ver plan en el docstring del módulo")

    async def send(self, user_id: str, mensaje: OutgoingMessage) -> str | None:  # pragma: no cover
        # Ver plan (punto 2): resolver conversation_id del user_id y POST a
        # /conversations/{id}/messages; UI → content_type input_select.
        raise NotImplementedError("ChatwootAdapter.send: ver plan en el docstring del módulo")

    async def handoff(self, conversation_id: int) -> None:  # pragma: no cover
        # Ver plan (punto 3): toggle_status → "open" asigna la conversación
        # al equipo humano.
        raise NotImplementedError("ChatwootAdapter.handoff: ver plan en el docstring del módulo")
