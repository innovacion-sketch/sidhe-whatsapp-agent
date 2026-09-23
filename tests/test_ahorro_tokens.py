"""Lo que evita que la factura de Claude se dispare sin quitar funciones."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from sidhe_agent.config import Settings
from sidhe_agent.graph.nodes import CADA_CUANTOS_MENSAJES, toca_extraer
from sidhe_agent.main import crear_llm


def _conversacion(mensajes_del_cliente: int) -> list:
    """Conversación con N mensajes del cliente y su respuesta del bot."""
    turnos: list = [SystemMessage(content="contexto")]
    for i in range(mensajes_del_cliente):
        turnos.append(HumanMessage(content=f"mensaje {i}"))
        turnos.append(AIMessage(content=f"respuesta {i}"))
    return turnos


def test_el_primer_mensaje_si_extrae_perfil():
    """Si no, se llegaría tarde al nombre del cliente."""
    assert toca_extraer(_conversacion(1)) is True


def test_no_extrae_en_cada_mensaje():
    assert toca_extraer(_conversacion(2)) is False
    assert toca_extraer(_conversacion(4)) is False
    assert toca_extraer(_conversacion(5)) is False


def test_extrae_cada_tres_mensajes():
    assert toca_extraer(_conversacion(3)) is True
    assert toca_extraer(_conversacion(6)) is True
    assert toca_extraer(_conversacion(9)) is True


def test_sin_mensajes_del_cliente_no_hay_nada_que_extraer():
    assert toca_extraer([SystemMessage(content="solo contexto")]) is False
    assert toca_extraer([]) is False


def test_la_ventana_del_extractor_cubre_lo_que_se_salta():
    """Lo dicho entre extracciones no se pierde: entra en la siguiente pasada."""
    from sidhe_agent.graph.nodes import MENSAJES_PARA_EXTRACCION

    assert MENSAJES_PARA_EXTRACCION >= CADA_CUANTOS_MENSAJES * 2


def test_el_llm_principal_no_manda_temperature():
    """Los modelos 4.6 en adelante la rechazan con error 400."""
    llm = crear_llm(SimpleNamespace(anthropic_api_key="test"), "claude-sonnet-5")
    assert llm.temperature is None
    assert llm.thinking == {"type": "disabled"}


def test_el_rag_descarta_fragmentos_que_no_vienen_al_caso():
    """Un fragmento poco parecido no ayuda y se reenvía en cada mensaje."""
    from sidhe_agent.tools.conocimiento import MAX_RESULTADOS, filtrar_resultados

    filas = [
        ("La garantía cubre 90 días", "Guía", 0.05),  # relevancia 0.95
        ("Las plantillas son de TPU", "Guía", 0.40),  # 0.60
        ("Horario de Liverpool Polanco", "Guía", 0.80),  # 0.20, fuera
    ]
    resultados = filtrar_resultados(filas)

    assert [r["relevancia"] for r in resultados] == [0.95, 0.6]
    assert len(resultados) <= MAX_RESULTADOS


def test_el_rag_no_devuelve_relleno_cuando_nada_coincide():
    from sidhe_agent.tools.conocimiento import filtrar_resultados

    assert filtrar_resultados([("texto cualquiera", "Guía", 0.9)]) == []


def test_las_tareas_internas_usan_un_modelo_barato():
    """El extractor y el resumidor escriben para el bot, no para el cliente."""
    ajustes = Settings(anthropic_api_key="test")
    assert "haiku" in ajustes.anthropic_model_utilitario


def test_lo_cotidiano_va_en_haiku_y_las_citas_en_sonnet():
    """Medido con 30 preguntas reales: empate en lo cotidiano, y el flujo
    de citas -que no se pudo medir- se queda con el modelo grande."""
    ajustes = Settings(anthropic_api_key="test")
    assert "haiku" in ajustes.anthropic_model
    assert "sonnet" in ajustes.anthropic_model_agenda
