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
