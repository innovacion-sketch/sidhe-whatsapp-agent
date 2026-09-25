"""WhatsApp directo con Meta (Cloud API), sin Twilio en medio.

Es la misma red que ya usamos, pero hablándole a Meta de frente. Twilio
cobra $0.005 por mensaje —de ida Y de vuelta— encima de lo que cobra Meta;
esa capa es lo que desaparece aquí.

Tres diferencias con el adaptador de Twilio que importan al escribir código:

- **La UI va en el mismo POST.** Twilio obliga a crear un "Content" antes de
  poder mandar una lista, así que cada menú eran dos llamadas y un objeto
  que se quedaba guardado en su cuenta. Aquí la lista viaja en el cuerpo del
  mensaje y no hay nada que crear ni que limpiar.
- **El audio se baja en dos pasos.** El webhook trae un id, no una URL: hay
  que pedir la URL y después bajarla, las dos veces con el token. Las URLs
  de Meta caducan en minutos y no sirven sin cabecera de autorización.
- **Un POST puede traer varios mensajes** de varias conversaciones, por eso
  `desglosar` devuelve una lista. También llegan acuses de entrega y de
  lectura, que se descartan: si no, el bot se contestaría a sí mismo.

Este adaptador conoce `phone_number_id`, que es como Meta identifica cada
número del negocio. Se le contesta al cliente SIEMPRE desde el número al que
escribió, igual que con Twilio.
"""

import hashlib
import hmac
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from .adjuntos import describir_archivo, describir_ubicacion
from .base import ChannelAdapter
from .schemas import (
    MAX_DESCRIPCION,
    MAX_ETIQUETA,
    MAX_TITULO,
    IncomingMessage,
    OutgoingMessage,
)

logger = structlog.get_logger(__name__)

API_VERSION = "v21.0"
BASE_GRAPH = "https://graph.facebook.com"
TIMEOUT = httpx.Timeout(15.0)

# El webhook de WhatsApp llega con este `object`; los de Instagram y
# Messenger llegan con otro y los atiende channels/meta.py.
OBJETO_WHATSAPP = "whatsapp_business_account"

# Meta corta el cuerpo de una lista en 1024 caracteres y el botón que la
# abre en 20. Se recorta aquí y no en el agente: es un límite del canal.
MAX_CUERPO = 1024
MAX_BOTON_LISTA = 20

# Lo que Meta manda como archivo, y cómo se le nombra al agente
ARCHIVOS = {
    "image": "una imagen",
    "sticker": "un sticker",
    "document": "un documento",
    "video": "un video",
}

# Dado un cliente, a qué número del negocio le escribió por última vez
ResolverRemitente = Callable[[str], Awaitable[str | None]]


def validar_firma(cuerpo: bytes, cabecera: str, app_secret: str) -> bool:
    """Comprueba la X-Hub-Signature-256 que manda Meta.

    Sin esto, cualquiera que conozca la URL puede inyectar mensajes falsos.
    Se calcula sobre el cuerpo CRUDO: reserializar el JSON cambia el hash.
    """
    if not app_secret or not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(app_secret.encode("utf-8"), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecera[len("sha256=") :])


def desglosar(payload: dict[str, Any]) -> list[dict]:
    """Webhook completo → un dict por mensaje de cliente.

    Descarta lo que no es un mensaje: acuses de entrega, de lectura y
    cambios de estado de la cuenta. Cada dict lleva ya el `metadata` del
    número al que escribieron y el perfil de quien escribe, porque en el
    payload viven en otro nivel y después no se alcanzan.
    """
    if payload.get("object") != OBJETO_WHATSAPP:
        return []

    mensajes = []
    for entrada in payload.get("entry", []):
        for cambio in entrada.get("changes", []):
            valor = cambio.get("value") or {}
            if not valor.get("messages"):
                continue  # statuses: entregado, leído, fallido
            perfiles = {
                contacto.get("wa_id"): (contacto.get("profile") or {}).get("name")
                for contacto in valor.get("contacts") or []
            }
            for mensaje in valor["messages"]:
                mensajes.append(
                    {
                        "mensaje": mensaje,
                        "metadata": valor.get("metadata") or {},
                        "nombre_perfil": perfiles.get(mensaje.get("from")),
                    }
                )
    return mensajes


# --- recordatorio de cita ---
#
# Las plantillas viven en la cuenta de WhatsApp (WABA), no en el número: la
# de Twilio se queda en la cuenta de Twilio y aquí hay que crearla de nuevo.
# Ya que hay que pasar otra vez por la aprobación, va con lo que faltaba:
# la dirección (para que nadie llegue al Liverpool equivocado) y tres
# botones, que es lo que más baja las inasistencias: cancelar con un toque
# libera el horario para alguien más.

CUERPO_RECORDATORIO = (
    "Hola {{1}}, te recordamos tu cita de estudio de pisada en {{2}} ({{3}}) "
    "el {{4}} a las {{5}}. Te recomendamos llegar 10 minutos antes y con ropa "
    "cómoda. ¿Nos confirmas tu asistencia?"
)
EJEMPLO_RECORDATORIO = [
    "Diana", "Liverpool Polanco", "Mariano Escobedo 425, Polanco", "vie 26 sep", "11:00",
]
# (id que llega de vuelta al tocarlo, texto del botón)
BOTONES_RECORDATORIO = [
    ("recordatorio_confirmar", "Confirmo asistencia"),
    ("recordatorio_reagendar", "Reagendar"),
    ("recordatorio_cancelar", "Cancelar cita"),
]


def plantilla_recordatorio(nombre: str, idioma: str) -> dict:
    """Lo que se manda a Meta para dar de alta la plantilla (una vez)."""
    return {
        "name": nombre,
        "language": idioma,
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": CUERPO_RECORDATORIO,
                "example": {"body_text": [EJEMPLO_RECORDATORIO]},
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {"type": "QUICK_REPLY", "text": texto}
                    for _, texto in BOTONES_RECORDATORIO
                ],
            },
        ],
    }


def _parametro(valor: str, respaldo: str) -> str:
    """Meta rechaza parámetros vacíos, con saltos de línea o muchos espacios."""
    limpio = " ".join(str(valor or "").split())
    return limpio or respaldo


def cuerpo_recordatorio(
    telefono: str, datos: dict[str, str], plantilla: str, idioma: str
) -> dict:
    """El envío de la plantilla a un cliente, con sus datos y los botones."""
    nombre = _parametro(datos.get("nombre", ""), "hola").split()[0]
    valores = [
        nombre,
        _parametro(datos.get("sucursal", ""), "Sidhe"),
        # Hay sucursales sin dirección capturada (Atizapán): mejor esto que
        # un parámetro vacío, que haría fallar el envío completo
        _parametro(datos.get("direccion", ""), "dentro de Liverpool"),
        _parametro(datos.get("fecha", ""), "tu fecha agendada"),
        _parametro(datos.get("hora", ""), "la hora agendada"),
    ]
    return {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "template",
        "template": {
            "name": plantilla,
            "language": {"code": idioma},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in valores],
                },
                *[
                    {
                        "type": "button",
                        "sub_type": "quick_reply",
                        "index": str(i),
                        "parameters": [{"type": "payload", "payload": id_boton}],
                    }
                    for i, (id_boton, _) in enumerate(BOTONES_RECORDATORIO)
                ],
            ],
        },
    }


def fallidos(payload: dict[str, Any]) -> list[dict]:
    """Envíos que Meta reporta como fallidos, para dejarlos en el log.

    Llegan por el mismo webhook, como estado de un mensaje nuestro. Sin
    esto un mensaje que no llegó (ventana de 24 h vencida, número que no
    tiene WhatsApp) no deja ningún rastro.
    """
    resultado = []
    for entrada in payload.get("entry", []):
        for cambio in entrada.get("changes", []):
            for estado in (cambio.get("value") or {}).get("statuses") or []:
                if estado.get("status") == "failed":
                    resultado.append(estado)
    return resultado


class WhatsAppCloudAdapter(ChannelAdapter):
    """WhatsApp por la API de Meta. Mismo `canal` que el de Twilio.

    Comparte el nombre de canal a propósito: el thread de memoria se deriva
    de (canal, user_id), así que una conversación que empezó por Twilio
    sigue exactamente donde iba al cambiar de proveedor. Si el canal
    cambiara, cada cliente arrancaría de cero.
    """

    canal = "whatsapp"

    def __init__(
        self,
        phone_number_id: str,
        token: str,
        numero_por_defecto: str = "",
        resolver_remitente: ResolverRemitente | None = None,
        api_version: str = API_VERSION,
    ) -> None:
        self._phone_id = phone_number_id
        self._token = token
        self._numero = numero_por_defecto
        self._resolver = resolver_remitente
        self._base = f"{BASE_GRAPH}/{api_version}"
        # phone_number_id de cada número del negocio, para contestar desde
        # el mismo al que le escribieron
        self._ids_por_numero: dict[str, str] = {}
        if numero_por_defecto and phone_number_id:
            self._ids_por_numero[clave_numero(numero_por_defecto)] = phone_number_id

    def registrar_numero(self, numero: str, phone_number_id: str) -> None:
        """Da de alta otro número del negocio (el 0202 junto al 5164)."""
        if numero and phone_number_id:
            self._ids_por_numero[clave_numero(numero)] = phone_number_id

    # --- entrada ---

    def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage:
        """Un mensaje ya desglosado → IncomingMessage."""
        mensaje = payload.get("mensaje") or {}
        metadata = payload.get("metadata") or {}
        comunes = {
            "canal": self.canal,
            "user_id": _normalizar(mensaje.get("from", "")),
            "message_sid": mensaje.get("id"),
            "nombre_perfil": payload.get("nombre_perfil"),
            "numero_negocio": _normalizar(
                metadata.get("display_phone_number", "")
            )
            or None,
        }

        tipo = mensaje.get("type")

        # El cliente tocó una opción: viene el id EXACTO que le mandamos
        if tipo == "interactive":
            interactivo = mensaje.get("interactive") or {}
            elegido = interactivo.get("list_reply") or interactivo.get(
                "button_reply"
            ) or {}
            return IncomingMessage(
                **comunes,
                tipo="seleccion_interactiva",
                contenido=elegido.get("title", ""),
                item_id=elegido.get("id"),
            )

        # Botón de una plantilla (el de los recordatorios)
        if tipo == "button":
            boton = mensaje.get("button") or {}
            return IncomingMessage(
                **comunes,
                tipo="seleccion_interactiva",
                contenido=boton.get("text", ""),
                item_id=boton.get("payload") or boton.get("text"),
            )

        # Nota de voz: solo viene el id, la descarga es aparte
        if tipo in ("audio", "voice"):
            audio = mensaje.get(tipo) or {}
            return IncomingMessage(
                **comunes,
                tipo="audio",
                media_url=audio.get("id"),
                media_content_type=audio.get("mime_type") or "audio/ogg",
            )

        # Foto, sticker, documento o video: se describe, porque el modelo no
        # lo ve y un mensaje vacio lo rechaza la API
        if tipo in ARCHIVOS:
            archivo = mensaje.get(tipo) or {}
            return IncomingMessage(
                **comunes,
                tipo="adjunto",
                contenido=describir_archivo(
                    archivo.get("mime_type", ""),
                    archivo.get("caption", ""),
                    que=ARCHIVOS[tipo],
                ),
            )

        if tipo == "location":
            lugar = mensaje.get("location") or {}
            return IncomingMessage(
                **comunes,
                tipo="adjunto",
                contenido=describir_ubicacion(
                    lugar.get("latitude"),
                    lugar.get("longitude"),
                    lugar.get("address", ""),
                    lugar.get("name", ""),
                ),
            )

        if tipo != "text":
            # Contactos, reacciones, encuestas... lo que no sabemos leer se
            # nombra en vez de llegar vacio
            return IncomingMessage(
                **comunes,
                tipo="adjunto",
                contenido=describir_archivo(que=f"un mensaje de tipo {tipo}"),
            )

        return IncomingMessage(
            **comunes,
            tipo="texto",
            contenido=(mensaje.get("text") or {}).get("body", ""),
        )

    # --- salida ---

    def _cuerpo(self, user_id: str, mensaje: OutgoingMessage) -> dict:
        base: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": user_id,
        }
        if mensaje.ui is None:
            return {**base, "type": "text", "text": {"body": mensaje.texto}}

        if mensaje.ui.tipo == "botones":
            accion = {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": opcion.id,
                            "title": opcion.etiqueta[:MAX_ETIQUETA],
                        },
                    }
                    for opcion in mensaje.ui.opciones
                ]
            }
        else:
            accion = {
                "button": mensaje.ui.titulo[:MAX_BOTON_LISTA],
                "sections": [
                    {
                        "title": mensaje.ui.titulo[:MAX_TITULO],
                        "rows": [
                            _fila(opcion) for opcion in mensaje.ui.opciones
                        ],
                    }
                ],
            }
        return {
            **base,
            "type": "interactive",
            "interactive": {
                "type": "button" if mensaje.ui.tipo == "botones" else "list",
                "body": {"text": mensaje.texto[:MAX_CUERPO]},
                "action": accion,
            },
        }

    async def send(self, user_id: str, mensaje: OutgoingMessage) -> str | None:
        """Manda la respuesta desde el número al que el cliente escribió.

        Si la UI interactiva la rechaza Meta, se reintenta en texto numerado
        antes de darse por vencido: que el cliente se quede sin respuesta es
        peor que perder los botones.
        """
        phone_id = await self._phone_id_para(user_id)
        if not phone_id or not self._token:
            logger.warning("whatsapp_cloud_sin_credenciales")
            return None

        enviado = await self._post(phone_id, self._cuerpo(user_id, mensaje))
        if enviado is None and mensaje.ui is not None:
            logger.warning("ui_rechazada_degradando_a_texto")
            enviado = await self._post(
                phone_id,
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": user_id,
                    "type": "text",
                    "text": {"body": _render_texto(mensaje)},
                },
            )
        return enviado

    async def _phone_id_para(self, user_id: str) -> str:
        """Desde qué número del negocio se le contesta a este cliente.

        Siempre desde el mismo al que escribió: si no, le llega la respuesta
        de un contacto que no tiene guardado. Ante cualquier duda sale del
        número por defecto, que es mejor que no contestar.
        """
        if self._resolver is None or not self._ids_por_numero:
            return self._phone_id
        try:
            numero = await self._resolver(user_id)
        except Exception:
            logger.exception("error_resolviendo_numero_del_negocio")
            return self._phone_id
        return self._ids_por_numero.get(clave_numero(numero or ""), self._phone_id)

    async def _post(self, phone_id: str, cuerpo: dict) -> str | None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
            respuesta = await cliente.post(
                f"{self._base}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {self._token}"},
                json=cuerpo,
            )
        if respuesta.status_code >= 400:
            # El motivo casi siempre es la ventana de 24 h vencida
            logger.warning(
                "whatsapp_cloud_envio_rechazado",
                status=respuesta.status_code,
                detalle=respuesta.text[:300],
            )
            return None
        mensajes = respuesta.json().get("messages") or [{}]
        return mensajes[0].get("id")

    async def enviar_recordatorio(self, telefono: str, datos: dict[str, str]) -> str:
        """Manda la plantilla de recordatorio. Revienta si Meta la rechaza,
        para que el endpoint la cuente como error y no como enviada."""
        from ..config import get_settings

        ajustes = get_settings()
        phone_id = await self._phone_id_para(telefono)
        enviado = await self._post(
            phone_id,
            cuerpo_recordatorio(
                telefono,
                datos,
                ajustes.whatsapp_cloud_plantilla_recordatorio,
                ajustes.whatsapp_cloud_idioma_plantilla,
            ),
        )
        if enviado is None:
            raise RuntimeError("Meta rechazó el recordatorio (ver whatsapp_cloud_envio_rechazado)")
        return enviado

    async def marcar_leido(self, message_id: str, phone_number_id: str = "") -> None:
        """Palomitas azules y "escribiendo…" mientras el bot arma la respuesta.

        Tapa la espera de la ráfaga y la del modelo: el cliente ve que alguien
        ya lo está atendiendo. No se cobra. Si falla, no pasa nada.
        """
        phone_id = phone_number_id or self._phone_id
        if not message_id or not phone_id or not self._token:
            return
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
                await cliente.post(
                    f"{self._base}/{phone_id}/messages",
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={
                        "messaging_product": "whatsapp",
                        "status": "read",
                        "message_id": message_id,
                        "typing_indicator": {"type": "text"},
                    },
                )
        except Exception:
            logger.info("no_se_pudo_marcar_leido")

    # --- audio ---

    async def descargar_media(self, media_id: str) -> tuple[bytes, str] | None:
        """(bytes, mime) de una nota de voz, o None si no se pudo bajar.

        Dos pasos porque Meta no da la URL en el webhook, y esa URL además
        caduca en minutos y no sirve sin el token en la cabecera.
        """
        if not media_id or not self._token:
            return None
        cabeceras = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
                ficha = await cliente.get(
                    f"{self._base}/{media_id}", headers=cabeceras
                )
                ficha.raise_for_status()
                datos = ficha.json()
                url = datos.get("url")
                if not url:
                    return None
                archivo = await cliente.get(url, headers=cabeceras)
                archivo.raise_for_status()
                return archivo.content, datos.get("mime_type", "audio/ogg")
        except Exception:
            logger.exception("no_se_pudo_bajar_el_audio_de_whatsapp")
            return None


def _fila(opcion) -> dict:
    fila = {"id": opcion.id, "title": opcion.etiqueta[:MAX_ETIQUETA]}
    if opcion.descripcion:
        fila["description"] = opcion.descripcion[:MAX_DESCRIPCION]
    return fila


def _normalizar(numero: str) -> str:
    """Meta manda los números sin '+' y Twilio con él. Se guarda con '+'.

    El user_id es la mitad de la clave del thread de memoria: si el mismo
    cliente llega escrito distinto según el proveedor, se le abre una
    conversación nueva y el bot pierde todo lo que sabía de él.
    """
    limpio = "".join(c for c in (numero or "") if c.isdigit())
    return f"+{limpio}" if limpio else ""


def clave_numero(numero: str) -> str:
    """Para COMPARAR números del negocio, no para guardarlos.

    En México el mismo celular aparece como +52 1 56… o +52 56…, según quién
    lo reporte (el "1" de los celulares que WhatsApp arrastra). Si Twilio lo
    guardó de una forma y Meta lo manda de otra, sin esto la respuesta
    saldría por el proveedor equivocado. El id del cliente NO pasa por aquí:
    es la llave de su memoria y se queda exactamente como llega.
    """
    digitos = "".join(c for c in (numero or "") if c.isdigit())
    if digitos.startswith("521") and len(digitos) == 13:
        digitos = "52" + digitos[3:]
    return digitos


def _render_texto(mensaje: OutgoingMessage) -> str:
    """La UI degradada a lista numerada, igual que hace el de Twilio."""
    if mensaje.ui is None:
        return mensaje.texto
    lineas = [mensaje.texto, "", mensaje.ui.titulo]
    for i, opcion in enumerate(mensaje.ui.opciones, start=1):
        linea = f"{i}. {opcion.etiqueta}"
        if opcion.descripcion:
            linea += f" — {opcion.descripcion}"
        lineas.append(linea)
    return "\n".join(lineas)
