"""Contratos agnósticos al canal: mensaje entrante normalizado y respuesta estructurada.

Límites de caracteres según WhatsApp (sección 6 del diseño):
- titulo de UI: máx 24
- etiqueta de opción: máx 24
- descripción de opción: máx 72
- lista: máx 10 opciones; botones: máx 3
"""

from typing import Literal

from pydantic import BaseModel, model_validator

TipoMensaje = Literal["texto", "audio", "seleccion_interactiva"]

MAX_TITULO = 24
MAX_ETIQUETA = 24
MAX_DESCRIPCION = 72
MAX_OPCIONES_LISTA = 10
MAX_OPCIONES_BOTONES = 3


class IncomingMessage(BaseModel):
    canal: str
    user_id: str
    tipo: TipoMensaje
    contenido: str = ""
    item_id: str | None = None
    message_sid: str | None = None
    media_url: str | None = None
    media_content_type: str | None = None
    nombre_perfil: str | None = None


class Opcion(BaseModel):
    id: str
    etiqueta: str
    descripcion: str | None = None


class UIElement(BaseModel):
    tipo: Literal["lista", "botones"]
    titulo: str
    opciones: list[Opcion]

    @model_validator(mode="after")
    def _validar_limites(self) -> "UIElement":
        maximo = MAX_OPCIONES_LISTA if self.tipo == "lista" else MAX_OPCIONES_BOTONES
        if len(self.opciones) > maximo:
            raise ValueError(f"UI '{self.tipo}' admite máximo {maximo} opciones")
        if not self.opciones:
            raise ValueError("UI sin opciones")
        return self


class OutgoingMessage(BaseModel):
    texto: str
    ui: UIElement | None = None
