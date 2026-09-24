"""Qué le llega al agente cuando el cliente manda algo que no es texto.

Antes solo se entendían el texto y las notas de voz. Una foto sin pie le
llegaba al modelo como mensaje VACÍO, Anthropic lo rechazaba, y el cliente
recibía "tuve un problema técnico". En 30 días fueron 68 así, y suelen ser
de la gente con más intención: el que manda la foto de su estudio o de su
receta ya decidió.

El agente no ve las imágenes, y no hay que fingir que sí. Lo que recibe es
una nota que dice qué mandaron y el texto que venía con ello, para que
conteste en contexto: a una receta, el teléfono de su sucursal; a una
ubicación, la sucursal más cercana; a un sticker, lo que corresponda.

Los tres canales (Twilio, Meta directo e Instagram/Messenger) describen
igual, así que el prompt tiene una sola regla para todos.
"""

# El prefijo común es lo que reconoce el prompt: "[el cliente envió ..."
PREFIJO = "[el cliente "


def _que_es(mime: str) -> str:
    mime = (mime or "").lower()
    if mime == "image/webp":
        return "un sticker"
    if mime.startswith("image/"):
        return "una imagen"
    if mime.startswith("video/"):
        return "un video"
    if mime.startswith("audio/"):
        return "un audio"
    if mime in ("application/pdf",) or mime.startswith(
        ("application/", "text/")
    ):
        return "un documento"
    return "un archivo"


def describir_archivo(mime: str = "", texto: str = "", que: str = "") -> str:
    """Nota para un archivo. `que` fuerza la descripción cuando el canal ya
    dice el tipo (Meta manda "sticker" aunque el mime sea image/webp)."""
    nota = f"{PREFIJO}envió {que or _que_es(mime)}]"
    texto = (texto or "").strip()
    return f'{nota} con el texto: "{texto}"' if texto else nota


def describir_ubicacion(
    latitud: str | float | None = None,
    longitud: str | float | None = None,
    direccion: str = "",
    nombre: str = "",
) -> str:
    """Nota para una ubicación compartida.

    La dirección es lo que sirve: con ella el agente busca la sucursal por
    ciudad o zona. Las coordenadas solas no, porque las sucursales no
    tienen coordenadas guardadas; se pasan para que quede registro.
    """
    partes = [p.strip() for p in (nombre, direccion) if p and p.strip()]
    lugar = ", ".join(partes)
    coordenadas = (
        f" ({latitud}, {longitud})" if latitud not in (None, "") else ""
    )
    if lugar:
        return f"{PREFIJO}compartió su ubicación: {lugar}{coordenadas}]"
    return f"{PREFIJO}compartió su ubicación sin dirección{coordenadas}]"
