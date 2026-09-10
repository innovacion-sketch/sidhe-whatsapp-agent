"""Tool de estado del pedido de plantillas.

Busca primero por el teléfono de la conversación (el sistema ya lo conoce,
así que el cliente no tiene que dar ningún dato) y solo pide nombre y
sucursal cuando ese teléfono no aparece en la hoja.

El agente nunca redacta el estado por su cuenta: la tool devuelve una
categoría cerrada y un texto sugerido, y los estados que no son claros piden
explícitamente escalar a un asesor.
"""

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import pedidos as servicio

# Qué debe comunicar el agente en cada categoría
GUIA = {
    servicio.LISTO: (
        "Las plantillas ya están en la sucursal y el cliente puede pasar a "
        "recogerlas en el horario del stand."
    ),
    servicio.ENTREGADO: (
        "El pedido figura como ya entregado. Si el cliente dice que no lo "
        "recibió, escala a un asesor."
    ),
    servicio.ENVIADO: (
        "El pedido fue enviado a domicilio. Si el cliente necesita guía o "
        "fecha de entrega, escala a un asesor."
    ),
    servicio.EN_PROCESO: (
        "Las plantillas siguen en fabricación. Recuerda el tiempo estimado de "
        "las FAQs y ofrece avisar por este chat cuando estén listas."
    ),
    servicio.REVISION: (
        "El estado de este pedido necesita revisión de un asesor: NO se lo "
        "interpretes al cliente, usa escalar_a_humano."
    ),
}


def _formatear(encontrados: list[dict]) -> dict:
    return {
        "encontrado": True,
        "pedidos": [
            {**p, "que_decir": GUIA.get(p["status"], GUIA[servicio.REVISION])}
            for p in encontrados
        ],
    }


@tool
async def consultar_estado_pedido(
    state: Annotated[dict, InjectedState],
    nombre_completo: str = "",
    sucursal: str = "",
) -> dict:
    """Consulta en qué va el pedido de plantillas del cliente.

    Úsala cuando pregunten si sus plantillas ya están listas, por el estatus
    de su pedido o cuándo pueden recogerlas. NO la uses para precios ni
    tiempos generales de entrega: eso está en las preguntas frecuentes.

    Primero intenta sin argumentos: busca con el teléfono de esta
    conversación y normalmente basta. Solo si devuelve
    "no_encontrado_por_telefono", pídele al cliente su nombre completo y su
    sucursal y vuelve a llamarla con esos datos.

    Cada pedido trae un campo "que_decir" con lo que corresponde comunicar;
    síguelo y nunca inventes un estado ni una fecha de entrega.

    Args:
        nombre_completo: nombre y apellidos, solo si el teléfono no dio resultado.
        sucursal: sucursal donde se hizo el estudio, junto con el nombre.
    """
    telefono = state.get("user_id", "")
    encontrados = await servicio.buscar_por_telefono(telefono)
    if encontrados:
        return _formatear(encontrados)

    if nombre_completo and sucursal:
        encontrados = await servicio.buscar_por_nombre(nombre_completo, sucursal)
        if encontrados:
            return _formatear(encontrados)
        return {
            "encontrado": False,
            "motivo": "no_encontrado_por_nombre",
            "que_decir": (
                "No aparece un pedido con ese nombre en esa sucursal. Pide "
                "confirmar cómo quedó registrado el nombre, y si sigue sin "
                "aparecer usa escalar_a_humano."
            ),
        }

    return {
        "encontrado": False,
        "motivo": "no_encontrado_por_telefono",
        "que_decir": (
            "Este número no aparece en los pedidos. Pídele al cliente su "
            "nombre completo y la sucursal donde se hizo el estudio, y vuelve "
            "a llamar esta tool con esos datos."
        ),
    }
