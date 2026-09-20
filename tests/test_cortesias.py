"""El atajo de los acuses: barato, pero sobre todo que no corte una cita."""

from langchain_core.messages import AIMessage, HumanMessage

from sidhe_agent.services.cortesias import (
    RESPUESTA,
    es_acuse,
    respuesta_si_es_acuse,
)

SIN_NADA_ABIERTO: dict = {
    "messages": [
        HumanMessage(content="¿cuánto cuestan?"),
        AIMessage(content="La Plantilla Clásica cuesta $2,499."),
    ]
}


def test_reconoce_acuses():
    for texto in [
        "gracias",
        "Gracias!",
        "MUCHAS GRACIAS",
        "ok",
        "Ok.",
        "listo",
        "perfecto",
        "de acuerdo",
        "muy amable",
        "👍",
        "🙏🙏",
    ]:
        assert es_acuse(texto), texto


def test_no_confunde_un_mensaje_con_pregunta():
    for texto in [
        "gracias, ¿a qué hora abren?",
        "ok pero quiero cambiar mi cita",
        "listo para agendar",
        "ok agenda el jueves",
        "gracias pero no me sirvieron las plantillas",
    ]:
        assert not es_acuse(texto), texto


def test_no_atrapa_respuestas_a_preguntas():
    """'sí', 'no' o una fecha pueden ser la respuesta a lo que preguntó el bot."""
    for texto in ["si", "sí", "no", "mañana", "el jueves", "Perisur", "11:00"]:
        assert not es_acuse(texto), texto


def test_mensaje_vacio_o_largo_no_es_acuse():
    assert not es_acuse("")
    assert not es_acuse("   ")
    assert not es_acuse("gracias " * 10)


def test_contesta_solo_cuando_no_hay_nada_abierto():
    assert respuesta_si_es_acuse("gracias", SIN_NADA_ABIERTO) == RESPUESTA


def test_no_contesta_si_el_bot_hizo_una_pregunta():
    """Lo caro no es la llamada: es cortar a alguien a medio agendar."""
    valores = {
        "messages": [AIMessage(content="¿Te agendo el jueves a las 11:00?")],
    }
    assert respuesta_si_es_acuse("ok", valores) is None


def test_no_contesta_con_botones_esperando():
    valores = {**SIN_NADA_ABIERTO, "ui_pendiente": {"tipo": "lista", "titulo": "x"}}
    assert respuesta_si_es_acuse("ok", valores) is None


def test_no_contesta_si_la_conversacion_esta_escalada():
    valores = {**SIN_NADA_ABIERTO, "escalado": True}
    assert respuesta_si_es_acuse("gracias", valores) is None


def test_no_contesta_si_el_grafo_quedo_a_medias():
    assert respuesta_si_es_acuse("gracias", SIN_NADA_ABIERTO, True) is None


def test_no_contesta_si_el_bot_pidio_algo_sin_preguntar():
    """'Mándame tu nombre completo' no lleva signo, pero espera respuesta."""
    for peticion in [
        "Mándame tu nombre completo y la sucursal.",
        "Indícame la fecha que prefieres.",
        "Elige una de las opciones de abajo.",
        "Confirma que los datos están bien.",
    ]:
        valores = {"messages": [AIMessage(content=peticion)]}
        assert respuesta_si_es_acuse("listo", valores) is None, peticion


def test_conversacion_nueva_sin_historial():
    assert respuesta_si_es_acuse("gracias", {}) == RESPUESTA


def test_la_respuesta_no_usa_la_palabra_prohibida():
    assert "confort" not in RESPUESTA.lower()
