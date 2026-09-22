"""El caché de respuestas: barato, pero nunca a costa de contestar mal.

Lo que se prueba aquí no es el ahorro, es que una respuesta de un cliente
jamás se le pueda dar a otro.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from sidhe_agent.services.cache_respuestas import (
    apta_para_guardar,
    es_pregunta_generica,
    fijar_prompt,
    huella_actual,
)

TURNO_SIN_TOOLS = [
    HumanMessage(content="¿a qué hora abren?"),
    AIMessage(content="Abrimos de 11:00 a 21:00 todos los días."),
]
TURNO_CON_TOOLS = [
    HumanMessage(content="¿ya están mis plantillas?"),
    AIMessage(content=""),
    ToolMessage(content='{"status": "listo"}', tool_call_id="1"),
    AIMessage(content="¡Ya están listas! Pasa a recogerlas."),
]


def test_preguntas_de_catalogo_si_se_pueden_reusar():
    for pregunta in [
        "¿a qué hora abren?",
        "cuanto cuestan las plantillas",
        "¿hacen envíos a provincia?",
        "¿cuánto tardan en hacerlas?",
        "donde tienen sucursales",
    ]:
        assert es_pregunta_generica(pregunta), pregunta


def test_lo_que_habla_de_lo_propio_nunca_se_reusa():
    """El error caro: contestarle a alguien con el pedido de otro."""
    for pregunta in [
        "ya estan mis plantillas?",
        "¿cuándo llega mi pedido?",
        "quiero cancelar mi cita",
        "necesito reagendar",
        "cuando me entregan mi orden",
        "mi estudio cuando estara",
    ]:
        assert not es_pregunta_generica(pregunta), pregunta


def test_mensajes_muy_cortos_o_muy_largos_no_entran():
    assert not es_pregunta_generica("hola")
    assert not es_pregunta_generica("precio")
    assert not es_pregunta_generica("a" * 250)


def test_se_guarda_una_respuesta_de_catalogo():
    assert apta_para_guardar(
        "¿a qué hora abren?",
        "Abrimos de 11:00 a 21:00 todos los días.",
        TURNO_SIN_TOOLS,
    )


def test_no_se_guarda_si_el_bot_consulto_algo():
    """Si tocó el pedido o la agenda, la respuesta es de esa persona."""
    assert not apta_para_guardar(
        "¿ya están listas?", "¡Ya están listas!", TURNO_CON_TOOLS
    )


def test_no_se_guarda_si_ofrecio_botones():
    assert not apta_para_guardar(
        "¿dónde tienen sucursales?", "Elige tu zona:", TURNO_SIN_TOOLS, hay_ui=True
    )


def test_no_se_guarda_una_conversacion_escalada():
    assert not apta_para_guardar(
        "¿tienen garantía?", "Te paso con un asesor.", TURNO_SIN_TOOLS, escalado=True
    )


def test_no_se_guarda_si_la_respuesta_nombra_al_cliente():
    """'Claro, Juan, abrimos a las 11' no sirve para nadie más."""
    assert not apta_para_guardar(
        "¿a qué hora abren?",
        "Claro, Juan, abrimos de 11:00 a 21:00.",
        TURNO_SIN_TOOLS,
        nombre_cliente="Juan Pérez",
    )


def test_el_nombre_se_detecta_sin_acentos():
    assert not apta_para_guardar(
        "¿a qué hora abren?",
        "Con gusto, Ramon: abrimos de 11:00 a 21:00.",
        TURNO_SIN_TOOLS,
        nombre_cliente="Ramón",
    )


def test_una_respuesta_vacia_no_se_guarda():
    assert not apta_para_guardar("¿a qué hora abren?", "   ", TURNO_SIN_TOOLS)


def test_la_huella_cambia_si_cambia_el_prompt():
    """Cambiar un precio en las FAQs tiene que invalidar lo guardado."""
    primera = fijar_prompt("FAQ: la plantilla cuesta $2,499")
    segunda = fijar_prompt("FAQ: la plantilla cuesta $2,799")
    assert primera != segunda
    assert huella_actual() == segunda
    assert len(segunda) <= 32
