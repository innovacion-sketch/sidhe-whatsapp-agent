"""El caché de respuestas: barato, pero nunca a costa de contestar mal.

Lo que se prueba aquí no es el ahorro, es que una respuesta de un cliente
jamás se le pueda dar a otro.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from sidhe_agent.services.cache_respuestas import (
    apta_para_guardar,
    clave,
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


def test_lo_que_no_parece_pregunta_no_se_guarda():
    """Casos reales que se colaron al caché en producción."""
    for texto in [
        "Okey gracias",  # un acuse, no una pregunta
        "Si sobre los precios",  # respuesta a algo que preguntó el bot
        "si por favor",
        "el jueves esta bien",
        "muchas gracias entonces",
    ]:
        assert not es_pregunta_generica(texto), texto


def test_una_pregunta_sin_signos_si_se_reconoce():
    """En WhatsApp casi nadie escribe los signos de interrogación."""
    for texto in [
        "Un estudio de pisada cuanto sale",
        "cuanto cuestan las plantillas",
        "a que hora abren los domingos",
        "hacen envios a provincia",
        "tienen sucursal en monterrey",
    ]:
        assert es_pregunta_generica(texto), texto


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


def test_la_misma_pregunta_escrita_distinto_cae_en_la_misma_clave():
    """Así el caché sirve aunque no haya proveedor de embeddings."""
    igual = clave("¿A qué hora abren?")
    assert clave("a que hora abren") == igual
    assert clave("A QUE HORA ABREN!!") == igual
    assert clave("  a  qué   hora abren  ") == igual


def test_preguntas_distintas_no_comparten_clave():
    assert clave("¿a qué hora abren?") != clave("¿a qué hora cierran?")
    assert clave("cuanto cuesta la deportiva") != clave("cuanto cuesta la clasica")


def test_la_clave_no_se_pasa_del_tamano_de_la_columna():
    assert len(clave("pregunta larguísima " * 40)) <= 200


def test_la_huella_cambia_si_cambia_el_prompt():
    """Cambiar un precio en las FAQs tiene que invalidar lo guardado."""
    primera = fijar_prompt("FAQ: la plantilla cuesta $2,499")
    segunda = fijar_prompt("FAQ: la plantilla cuesta $2,799")
    assert primera != segunda
    assert huella_actual() == segunda
    assert len(segunda) <= 32


def test_no_se_guarda_una_conversacion_ya_cargada_de_contexto():
    """Casos reales que se colaron: citas de una persona servidas a otra."""
    from sidhe_agent.services.cache_respuestas import es_conversacion_temprana

    charla_larga = [HumanMessage(content=f"mensaje {i}") for i in range(5)]
    assert not es_conversacion_temprana(charla_larga)
    assert es_conversacion_temprana(TURNO_SIN_TOOLS)

    assert not apta_para_guardar(
        "¿hay problema con la edad?", "Perfecto, nos vemos el miércoles 23 a las 19:00.",
        TURNO_SIN_TOOLS,
    )


def test_una_respuesta_que_habla_de_esa_charla_no_se_guarda():
    for respuesta in [
        "Nos vemos el miércoles 23 a las 19:00",
        "Elige el día de las opciones que te mostré arriba",
        "Agendamos tu cita para el 8 de octubre",
        "Como te comenté, el precio es $2,199",
        "Ya tienes tu cita en Satélite",
    ]:
        assert not apta_para_guardar(
            "¿cuánto cuesta?", respuesta, TURNO_SIN_TOOLS
        ), respuesta


def test_una_tool_en_cualquier_momento_de_la_charla_invalida():
    """Antes solo se miraba el ultimo turno y el contexto se colaba igual."""
    charla = [
        HumanMessage(content="mis plantillas?"),
        ToolMessage(content="{}", tool_call_id="1"),
        AIMessage(content="siguen en proceso"),
        HumanMessage(content="¿y cuánto cuestan las nuevas?"),
    ]
    assert not apta_para_guardar("¿y cuánto cuestan?", "Cuestan $2,199", charla)


def test_un_reclamo_no_es_una_pregunta():
    """'no fue lo QUE me ofrecieron' entraba por la palabra 'que'."""
    assert not es_pregunta_generica("Realmente eso no fue lo que me ofrecieron")
    assert not es_pregunta_generica("eso no es lo que me dijeron ayer")
    # Una pregunta de verdad sigue entrando
    assert es_pregunta_generica("¿qué precio tienen las plantillas?")
    assert es_pregunta_generica("cuanto cuesta el estudio")


def test_una_pregunta_que_se_apoya_en_la_charla_no_se_guarda():
    """'Cuánto cuesta' a secas: el bot sabía de qué producto, el siguiente no.

    Se guardó con el precio de la Plantilla Inteligente porque esa charla
    venía hablando de un niño, y quedó como respuesta al precio de
    cualquier cosa.
    """
    assert not es_pregunta_generica("Cuánto cuesta")
    assert not es_pregunta_generica("y cuanto cuesta?")
    assert not es_pregunta_generica("¿cuánto sale?")
    # Diciendo de qué, sí sirve para cualquiera
    assert es_pregunta_generica("Cuánto cuesta el estudio de pisada")
    assert es_pregunta_generica("cuanto cuestan las plantillas")


def test_una_respuesta_que_supone_quien_es_el_paciente_no_se_guarda():
    """'Evalúan la pisada de tu hijo' venía de una charla sobre un niño."""
    for respuesta in [
        "La valoración no tiene costo: ahí evalúan la pisada de tu hijo.",
        "Con gusto revisamos a tu niña sin costo.",
        "Puede venir con tu esposa el día que gusten.",
    ]:
        assert not apta_para_guardar(
            "¿cuánto cuesta la consulta?", respuesta, TURNO_SIN_TOOLS
        ), respuesta
    # Hablarle de su propia pisada sigue siendo del catálogo
    assert apta_para_guardar(
        "¿cuánto cuesta la consulta?",
        "No tiene costo: el fisioterapeuta evalúa tu pisada.",
        TURNO_SIN_TOOLS,
    )
