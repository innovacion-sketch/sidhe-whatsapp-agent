"""Tool de estado del pedido de plantillas.

Busca primero por el teléfono de la conversación (el sistema ya lo conoce,
así que el cliente no tiene que dar ningún dato) y solo pide nombre y
sucursal cuando ese teléfono no aparece en la hoja.

El agente nunca redacta el estado por su cuenta: la tool devuelve una
categoría cerrada y un texto sugerido. Solo "EN SUCURSAL" autoriza a decir
que ya puede pasar a recogerlas; todo lo demás sigue en fabricación.

Cuando no encontramos el pedido, el cliente no se queda sin salida: se le
pasa el teléfono de su sucursal, que es quien tiene el detalle a la mano.
"""

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import pedidos as servicio

# Qué debe comunicar el agente en cada categoría
GUIA = {
    servicio.LISTO: (
        "Las plantillas YA ESTÁN en la sucursal: el cliente puede pasar a "
        "recogerlas en el horario del stand."
    ),
    servicio.ENTREGADO: (
        "El pedido figura como ya entregado. Si el cliente dice que no lo "
        "recibió, escala a un asesor y dale el teléfono de su sucursal."
    ),
    servicio.ENVIADO: (
        "El pedido fue enviado a domicilio. Si el cliente necesita guía o "
        "fecha de entrega, dale el teléfono de su sucursal y escala."
    ),
    servicio.EN_PROCESO: (
        "Las plantillas siguen EN FABRICACIÓN: aún no puede pasar por ellas. "
        "No prometas una fecha exacta; menciona el tiempo estimado de las "
        "FAQs y ofrece avisarle por este chat en cuanto lleguen a la "
        "sucursal. Si insiste en una fecha, dale el teléfono de su sucursal."
    ),
    servicio.REVISION: (
        "El estado de este pedido necesita revisión de un asesor: NO se lo "
        "interpretes al cliente, usa escalar_a_humano y dale también el "
        "teléfono de su sucursal."
    ),
}

# Salida común cuando no hay pedido: la sucursal sí puede ayudarlo
PASAR_A_SUCURSAL = (
    "Ofrécele el teléfono de su sucursal para atención directa: úsalo con "
    "buscar_sucursal si aún no lo tienes."
)

# Ventas que no salieron de un stand: la sucursal móvil B2B (eventos y
# empresas) y plazas que no atendemos por este canal. Aunque el status diga
# que está listo, no sabemos a dónde mandar al cliente.
GUIA_SIN_SUCURSAL = (
    "Este pedido NO es de una sucursal: viene de una venta móvil o de "
    "convenio (B2B). No le digas que pase a recoger a ningún stand ni le des "
    "un teléfono de sucursal, porque no sabemos dónde recibirá sus "
    "plantillas. Usa escalar_a_humano para que un asesor se lo confirme."
)


async def _formatear(encontrados: list[dict]) -> dict:
    conocidas = await servicio.etiquetas_de_sucursales()
    pedidos = []
    for p in encontrados:
        de_sucursal = servicio.es_de_sucursal(p["sucursal"], conocidas)
        pedidos.append({
            **p,
            "es_de_sucursal": de_sucursal,
            "que_decir": (
                GUIA.get(p["status"], GUIA[servicio.REVISION])
                if de_sucursal
                else GUIA_SIN_SUCURSAL
            ),
        })
    return {"encontrado": True, "pedidos": pedidos}


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
        return await _formatear(encontrados)

    if nombre_completo and sucursal:
        encontrados = await servicio.buscar_por_nombre(nombre_completo, sucursal)
        if encontrados:
            return await _formatear(encontrados)
        return {
            "encontrado": False,
            "motivo": "no_encontrado_por_nombre",
            "que_decir": (
                "No aparece un pedido con ese nombre en esa sucursal. Puede "
                "ser que esté registrado distinto o que sea de hace varios "
                f"meses. {PASAR_A_SUCURSAL} Si el cliente prefiere que lo "
                "revisemos nosotros, usa escalar_a_humano."
            ),
        }

    return {
        "encontrado": False,
        "motivo": "no_encontrado_por_telefono",
        "que_decir": (
            "Este número no aparece en los pedidos recientes. Pídele al "
            "cliente su nombre completo y la sucursal donde se hizo el "
            "estudio, y vuelve a llamar esta tool con esos datos."
        ),
    }
