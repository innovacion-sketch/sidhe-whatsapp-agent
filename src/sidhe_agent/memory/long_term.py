"""Memoria de largo plazo sobre el Store de LangGraph.

Namespace por usuario: ("perfiles", user_id) con la clave "perfil".

El perfil se lee al inicio de cada turno (se inyecta como <perfil_cliente>) y
al final del turno un extractor con salida estructurada (patrón langmem:
extraer hechos → upsert al Store) captura hechos duraderos: nombre, sucursal
habitual, padecimiento mencionado y tipo de plantilla de interés. NO se
guardan datos médicos sensibles más allá de lo que el cliente dijo
explícitamente y sea necesario para atenderlo.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

NAMESPACE_PERFILES = "perfiles"
CLAVE_PERFIL = "perfil"


async def leer_perfil(store: BaseStore | None, user_id: str) -> dict[str, Any]:
    if store is None:
        return {}
    item = await store.aget((NAMESPACE_PERFILES, user_id), CLAVE_PERFIL)
    return dict(item.value) if item else {}


async def guardar_perfil(
    store: BaseStore | None, user_id: str, datos: dict[str, Any]
) -> None:
    """Upsert de campos del perfil (merge superficial sobre lo existente)."""
    if store is None or not datos:
        return
    actual = await leer_perfil(store, user_id)
    actual.update({k: v for k, v in datos.items() if v})
    await store.aput((NAMESPACE_PERFILES, user_id), CLAVE_PERFIL, actual)


class PerfilExtraido(BaseModel):
    """Hechos duraderos sobre el cliente extraídos de la conversación."""

    nombre: str | None = Field(
        default=None, description="Nombre del cliente si lo dijo él mismo"
    )
    sucursal_habitual: str | None = Field(
        default=None, description="Sucursal que visita o eligió para su cita"
    )
    padecimiento: str | None = Field(
        default=None,
        description="Molestia o padecimiento que el cliente mencionó explícitamente "
        "como motivo de consulta (ej. 'fascitis plantar', 'dolor de rodilla')",
    )
    tipo_plantilla_interes: str | None = Field(
        default=None,
        description="Tipo de plantilla que le interesa (suave, intermedia, rígida, "
        "deportiva, express, sandalias)",
    )


PROMPT_EXTRACCION = """\
Extrae hechos duraderos sobre el cliente a partir del fragmento de conversación \
de WhatsApp que te doy. Reglas estrictas:
- Solo registra lo que el CLIENTE dijo explícitamente sobre sí mismo; nunca \
inferencias ni datos dichos por el asistente.
- En padecimiento guarda únicamente lo que el cliente mencionó como motivo de \
consulta; ningún otro dato médico o sensible.
- Deja en null todo campo que no se haya mencionado en este fragmento."""


def crear_extractor(llm: BaseChatModel) -> Runnable:
    """Runnable que recibe mensajes y devuelve un PerfilExtraido."""
    return llm.with_structured_output(PerfilExtraido)
