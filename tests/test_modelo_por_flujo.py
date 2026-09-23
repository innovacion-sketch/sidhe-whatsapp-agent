"""Qué conversaciones atiende el modelo grande y cuáles el barato.

Lo cotidiano —precios, horarios, estado de pedido— salió idéntico con
Haiku en la comparación de 30 casos reales. El flujo de citas es lo único
que esa prueba no midió, así que ahí sigue Sonnet.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from sidhe_agent.graph.nodes import es_conversacion_de_cita


def estado(*mensajes, ui=None) -> dict:
    return {"messages": list(mensajes), "ui_pendiente": ui}


def test_lo_cotidiano_va_al_modelo_barato():
    for texto in [
        "¿cuánto cuestan las plantillas?",
        "a qué hora abren",
        "ya están mis plantillas?",
        "¿dónde se ubican?",
        "hola buenas tardes",
        "¿hacen envíos?",
    ]:
        assert not es_conversacion_de_cita(estado(HumanMessage(content=texto))), texto


def test_pedir_cita_va_al_modelo_grande():
    for texto in [
        "quiero agendar una cita",
        "me gustaría agendar mi estudio de pisada",
        "necesito cancelar mi cita",
        "quiero reagendar",
        "¿qué horarios tienen disponibles?",
        "cuánto cuesta la valoración y cómo agendo",
    ]:
        assert es_conversacion_de_cita(estado(HumanMessage(content=texto))), texto


def test_con_botones_esperando_sigue_el_grande():
    """La respuesta del cliente a una lista de opciones es parte del flujo."""
    conversacion = estado(
        HumanMessage(content="sí"),
        ui={"tipo": "lista", "titulo": "Elige sucursal", "opciones": []},
    )
    assert es_conversacion_de_cita(conversacion)


def test_una_vez_en_el_flujo_no_se_suelta():
    """'el jueves a las 5' no dice 'cita', pero viene de una."""
    conversacion = estado(
        HumanMessage(content="quiero agendar"),
        AIMessage(content=""),
        ToolMessage(content="[]", tool_call_id="1", name="consultar_disponibilidad"),
        AIMessage(content="Tengo jueves y viernes"),
        HumanMessage(content="el jueves a las 5"),
    )
    assert es_conversacion_de_cita(conversacion)


def test_una_charla_vieja_de_citas_no_marca_para_siempre():
    """Pasados varios mensajes de otro tema, vuelve al modelo barato."""
    relleno = [HumanMessage(content=f"otra cosa {i}") for i in range(9)]
    conversacion = estado(
        HumanMessage(content="quiero agendar"),
        AIMessage(content="listo, quedó tu cita"),
        *relleno,
    )
    assert not es_conversacion_de_cita(conversacion)


def test_sin_mensajes_no_revienta():
    assert not es_conversacion_de_cita({})
    assert not es_conversacion_de_cita({"messages": None})


def test_el_bot_no_se_marca_a_si_mismo_con_sus_propias_palabras():
    """Lo que dejó la carga en Sonnet después de pasarla a Haiku.

    Casi toda respuesta de precios nombra "la valoración" y "el estudio de
    pisada". Contando esas palabras como intención de agendar, una simple
    pregunta de precio mandaba los ocho mensajes siguientes al modelo caro.
    """
    conversacion = estado(
        HumanMessage(content="¿cuánto cuestan las plantillas?"),
        AIMessage(content="La valoración del estudio de pisada no tiene costo."),
        HumanMessage(content="¿y hacen envíos?"),
    )
    assert not es_conversacion_de_cita(conversacion)


def test_si_el_bot_acaba_de_ofrecer_agendar_el_si_cuenta():
    """'Sí' no dice nada por sí solo; lo dice la pregunta que contesta."""
    conversacion = estado(
        HumanMessage(content="¿cuánto cuestan?"),
        AIMessage(content="Cuestan $2,199. ¿Quieres que te agende una cita?"),
        HumanMessage(content="sí"),
    )
    assert es_conversacion_de_cita(conversacion)


def test_preguntar_por_una_sucursal_no_es_agendar():
    """La pregunta más común que hay: no tiene por qué pagar el modelo caro."""
    conversacion = estado(
        HumanMessage(content="¿dónde se ubican?"),
        AIMessage(content=""),
        ToolMessage(content="[]", tool_call_id="1", name="buscar_sucursal"),
        AIMessage(content="Estamos en Liverpool Polanco."),
        HumanMessage(content="¿a qué hora abren?"),
    )
    assert not es_conversacion_de_cita(conversacion)
