"""Selección y calificación de los casos con los que se comparan modelos."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from comparar_modelos import calificar, categoria  # noqa: E402

PRECIOS_REALES = {"2499", "2899", "280"}


def test_reconoce_el_tipo_de_pregunta():
    casos = {
        "ya están mis plantillas?": "pedido",
        "cuándo llegan mis plantillas": "pedido",
        "¿cuánto cuesta un estudio?": "precio",
        "¿cuál es el costo de la valoración?": "precio",
        "quiero agendar una cita": "cita",
        "quiero cancelar mi cita": "cancelar",
        "¿dónde se ubican?": "sucursal",
        "¿dónde están ubicados?": "sucursal",
        "a qué hora abren": "horario",
        "hola buenas tardes": "otro",
    }
    for texto, esperado in casos.items():
        assert categoria(texto)[0] == esperado, texto


def test_una_pregunta_de_pedido_exige_consultar_el_pedido():
    """Contestar de memoria el estado de un pedido es el peor error posible."""
    assert categoria("ya están mis plantillas?")[1] == "consultar_estado_pedido"
    assert categoria("¿dónde se ubica la de Perisur?")[1] == "buscar_sucursal"
    # En precios no se exige herramienta: salen de las FAQs del prompt
    assert categoria("¿cuánto cuesta?")[1] is None


def test_detecta_un_precio_inventado():
    notas, inventados, _ = calificar(
        "La plantilla cuesta $2,499 y el envío $280.", [], None, PRECIOS_REALES
    )
    assert notas["no_inventa"] == 1.0 and inventados == []

    notas, inventados, _ = calificar(
        "Te la dejo en $9,999.", [], None, PRECIOS_REALES
    )
    assert notas["no_inventa"] == 0.0 and inventados == ["9999"]


def test_detecta_datos_bancarios_y_la_palabra_prohibida():
    notas, _, _ = calificar("La cuenta es 012180012345678901", [], None, PRECIOS_REALES)
    assert notas["no_inventa"] == 0.0

    notas, _, _ = calificar("Son muy confortables", [], None, PRECIOS_REALES)
    assert notas["sin_prohibidas"] == 0.0


def test_califica_la_herramienta_solo_cuando_se_espera_una():
    notas, _, _ = calificar(
        "déjame reviso", [{"name": "consultar_estado_pedido"}],
        "consultar_estado_pedido", PRECIOS_REALES,
    )
    assert notas["herramienta"] == 1.0

    notas, _, _ = calificar("ya están listas", [], "consultar_estado_pedido", PRECIOS_REALES)
    assert notas["herramienta"] == 0.0

    notas, _, _ = calificar("cuesta $2,499", [], None, PRECIOS_REALES)
    assert "herramienta" not in notas


def test_una_respuesta_larguisima_no_sirve_para_whatsapp():
    notas, _, _ = calificar("a" * 2000, [], None, PRECIOS_REALES)
    assert notas["largo_ok"] == 0.0


def test_sin_ciudad_no_se_exige_buscar_sucursal():
    """Preguntar de qué ciudad es lo correcto; buscar a ciegas, no."""
    from comparar_modelos import lugares

    conocidos = lugares()
    assert categoria("¿dónde se ubican?", conocidos) == ("sucursal", None)
    assert categoria("dónde están ubicados", conocidos) == ("sucursal", None)


def test_con_ciudad_si_se_exige():
    from comparar_modelos import lugares

    conocidos = lugares()
    assert categoria("tienen sucursal en Monterrey?", conocidos)[1] == "buscar_sucursal"
    assert categoria("dónde se ubica la de Perisur", conocidos)[1] == "buscar_sucursal"
