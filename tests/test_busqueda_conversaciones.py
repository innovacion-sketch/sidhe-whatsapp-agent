"""Buscar una palabra dentro de todas las conversaciones del panel."""

from sidhe_agent.services.conversaciones import recorte

LARGO = (
    "Buenas tardes, fui a la sucursal el sábado y me dijeron que mis "
    "plantillas estarían listas en diez días hábiles, pero ya pasaron "
    "quince y nadie me ha marcado para avisarme si ya puedo pasar."
)


def test_recorta_alrededor_de_la_palabra():
    fragmento = recorte(LARGO, "quince")
    assert "quince" in fragmento
    assert len(fragmento) < len(LARGO)
    assert fragmento.startswith("…")


def test_no_le_importan_los_acentos():
    """Quien busca escribe 'sabado', el cliente escribió 'sábado'."""
    assert "sábado" in recorte(LARGO, "sabado")


def test_ni_las_mayusculas():
    assert "plantillas" in recorte(LARGO, "PLANTILLAS")


def test_mensaje_corto_se_devuelve_completo():
    assert recorte("ya están listas?", "listas") == "ya están listas?"


def test_sin_coincidencia_devuelve_el_principio():
    fragmento = recorte(LARGO, "electroestimulador")
    assert fragmento.startswith("Buenas tardes")
    assert not fragmento.startswith("…")


def test_texto_vacio_no_revienta():
    assert recorte("", "hola") == ""
    assert recorte(None, "hola") == ""


def test_puntos_suspensivos_solo_donde_se_corto():
    inicio = recorte(LARGO, "Buenas")
    assert not inicio.startswith("…")
    assert inicio.endswith("…")
