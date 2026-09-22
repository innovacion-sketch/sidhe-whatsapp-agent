"""Acuses de recibo que no necesitan al modelo.

Un "gracias" al cierre de una conversación no requiere razonar nada, pero
contestarlo con el modelo cuesta una llamada completa con todo el contexto
encima. Aquí se atajan esos mensajes con una respuesta fija.

La regla es deliberadamente estricta, porque el error caro no es gastar una
llamada de más: es cortarle el paso a alguien que está agendando una cita.
Por eso solo entra un mensaje que sea EXACTAMENTE un acuse, y únicamente
cuando no hay nada abierto: ni botones esperando respuesta, ni una pregunta
del bot sin contestar, ni un escalamiento, ni el grafo a medio camino. Ante
la duda, contesta el modelo.
"""

import unicodedata
from typing import Any

RESPUESTA = "¡Con gusto! 🙌 Si necesitas algo más, aquí estoy."

# Solo frases que cierran. Nada que pueda ser una respuesta a una pregunta:
# "si", "no", "mañana" o "el jueves" jamás deben entrar aquí.
ACUSES = frozenset(
    {
        "gracias",
        "muchas gracias",
        "mil gracias",
        "muchisimas gracias",
        "gracias gracias",
        "ok gracias",
        "gracias ok",
        "okey gracias",
        "okay gracias",
        "va gracias",
        "sale gracias",
        "vale gracias",
        "ya gracias",
        "gracias muy amable",
        "listo gracias",
        "gracias por la informacion",
        "gracias por tu ayuda",
        "gracias por la ayuda",
        "muy amable",
        "muy amables",
        "excelente gracias",
        "perfecto gracias",
        "ok",
        "okay",
        "okey",
        "oka",
        "okis",
        "sale",
        "vale",
        "listo",
        "perfecto",
        "excelente",
        "entendido",
        "de acuerdo",
        "enterado",
        "enterada",
        "buenisimo",
        "que amable",
    }
)
# El bot pidió algo sin signo de pregunta ("mándame tu nombre completo"):
# ese "listo" del cliente es parte del trámite, no una despedida.
PETICIONES = (
    "mandame",
    "envianos",
    "enviame",
    "comparteme",
    "comparteme",
    "dime",
    "indicame",
    "escribeme",
    "confirma",
    "elige",
    "selecciona",
    "necesito que",
    "necesitamos",
    "avisame",
    "responde",
    "toca el boton",
)
EMOJIS_DE_ACUSE = frozenset("👍👌🙏🙌✅❤️😊🥰😁🤝")
# Variantes de presentación de emoji que no cambian el significado
INVISIBLES = "️‍"
MAX_CARACTERES = 40


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y sin signos: 'Gracias!!' -> 'gracias'."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    letras = [c if c.isalnum() else " " for c in sin_acentos]
    return " ".join("".join(letras).split())


def es_acuse(texto: str) -> bool:
    crudo = (texto or "").strip()
    if not crudo or len(crudo) > MAX_CARACTERES:
        return False
    solo_emojis = "".join(c for c in crudo if c not in INVISIBLES and not c.isspace())
    if solo_emojis and all(c in EMOJIS_DE_ACUSE for c in solo_emojis):
        return True
    return normalizar(crudo) in ACUSES


def _texto_del_ultimo_bot(mensajes: list) -> str:
    for mensaje in reversed(mensajes or []):
        if type(mensaje).__name__ != "AIMessage":
            continue
        contenido = mensaje.content
        if isinstance(contenido, str):
            return contenido
        return " ".join(
            bloque.get("text", "")
            for bloque in contenido
            if isinstance(bloque, dict) and bloque.get("type") == "text"
        )
    return ""


def hay_algo_abierto(valores: dict[str, Any], pasos_pendientes: bool) -> bool:
    """Si la conversación tiene algo esperando, contesta el modelo."""
    if pasos_pendientes:
        return True
    if valores.get("ui_pendiente") or valores.get("escalado"):
        return True
    ultimo = _texto_del_ultimo_bot(valores.get("messages", []))
    # El bot preguntó algo: ese "ok" puede ser la respuesta
    if "?" in ultimo or "¿" in ultimo:
        return True
    normalizado = normalizar(ultimo)
    return any(peticion in normalizado for peticion in PETICIONES)


def respuesta_si_es_acuse(
    texto: str, valores: dict[str, Any], pasos_pendientes: bool = False
) -> str | None:
    """La respuesta fija, o None si este mensaje lo tiene que ver el modelo."""
    if not es_acuse(texto):
        return None
    if hay_algo_abierto(valores, pasos_pendientes):
        return None
    return RESPUESTA
