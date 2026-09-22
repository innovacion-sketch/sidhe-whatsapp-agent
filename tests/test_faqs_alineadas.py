"""El bot y los asesores tienen que decir lo mismo.

El bot contesta con data/faqs.json; los asesores, con las respuestas rapidas
del documento del equipo (migraciones 0005 y 0006). Si un precio, un horario
o un tiempo de entrega cambia en un lado y no en el otro, el cliente recibe
dos versiones segun quien le conteste. Estos tests lo impiden.
"""

import csv
import importlib.util
import io
import json
import re
from pathlib import Path

RAIZ = Path(__file__).parent.parent
FAQS = {f["id"]: f for f in json.loads((RAIZ / "data" / "faqs.json").read_text(encoding="utf-8"))}


def _modulo(ruta: Path, nombre: str):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _respuestas_asesores() -> dict[str, str]:
    """Las respuestas rapidas del equipo tal como quedan tras la 0006."""
    versiones = RAIZ / "alembic" / "versions"
    respuestas = dict(_modulo(versiones / "0005_respuestas_del_equipo.py", "m5").RESPUESTAS_EQUIPO)
    m6 = _modulo(versiones / "0006_horario_plantillas_listas.py", "m6")
    respuestas[m6.ATAJO] = m6.texto_corregido()
    return respuestas


ASESORES = _respuestas_asesores()


def precios(texto: str) -> set[int]:
    return {int(p.replace(",", "")) for p in re.findall(r"\$\s?([\d,]{3,})", texto)}


def test_la_lista_de_precios_es_la_misma():
    assert precios(FAQS["faq_005"]["answer"]) == precios(ASESORES["costo"])
    assert len(precios(ASESORES["costo"])) == 7


def test_cada_producto_cuesta_lo_mismo_con_el_bot_y_con_el_asesor():
    pares = {
        "plantilla-3d": "faq_039",
        "plantilla-inteligente": "faq_037",
        "plantilla-deportiva": "faq_038",
        "plantilla-express": "faq_031",
        "plan-ortesico": "faq_036",
        "sandalia": "faq_030",
        "plan-familiar": "faq_035",
    }
    for atajo, faq in pares.items():
        assert precios(FAQS[faq]["answer"]) == precios(ASESORES[atajo]), atajo


def test_la_valoracion_inicial_no_tiene_costo_en_ambos_lados():
    assert "no tiene costo" in FAQS["faq_034"]["answer"]
    assert "no tiene costo" in ASESORES["costo"]


def test_el_horario_es_11_a_9_todos_los_dias_en_todos_lados():
    assert "11:00 a.m. a 9:00 p.m." in FAQS["faq_032"]["answer"]
    assert "lunes a domingo" in FAQS["faq_032"]["answer"]
    assert "11:00 am a 9:00 pm" in ASESORES["horario"]
    assert "11am a 9pm" in ASESORES["plantillas-listas"]
    assert "8pm" not in ASESORES["plantillas-listas"]

    # buscar_sucursal da el horario de la base: tambien tiene que cuadrar
    with io.open(RAIZ / "data" / "sucursales.csv", encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            assert (fila["horario_apertura"], fila["horario_cierre"]) == ("11:00", "21:00"), fila["nombre"]
            # Una sucursal puede tener descansos fijos (hoy Tijuana y
            # Monterrey). El horario de la FAQ sigue valiendo; lo que no
            # puede pasar es que el bot no lo sepa, y por eso buscar_sucursal
            # avisa cuales son (tools/sucursales.py::dias_de_descanso).
            dias = fila["dias_operacion"].split("|")
            assert 5 <= len(dias) <= 7, fila["nombre"]


def test_el_tiempo_de_entrega_es_el_mismo():
    assert "10 a 15 días hábiles" in FAQS["faq_006"]["answer"]
    assert "10 a 15 días hábiles" in ASESORES["tiempo-de-entrega"]


def test_la_express_es_en_todas_las_sucursales():
    express = FAQS["faq_031"]["answer"]
    assert "todas las sucursales" in express
    assert "Polanco" not in express
    assert "11:00 a.m. a 5:00 p.m." in express
    assert "11:00 a.m. a 5:00 p.m." in ASESORES["plantilla-express"]


def test_el_bot_no_usa_confort_ni_da_cuentas_bancarias():
    for faq in FAQS.values():
        texto = faq["question"] + " " + faq["answer"]
        assert "confort" not in texto.lower(), faq["id"]
        assert not re.search(r"\d{16,18}", texto), faq["id"]


def test_las_faqs_no_repiten_id():
    lista = json.loads((RAIZ / "data" / "faqs.json").read_text(encoding="utf-8"))
    ids = [f["id"] for f in lista]
    assert len(ids) == len(set(ids))


def test_el_prompt_del_bot_esta_compilado_con_las_faqs_actuales():
    """Editar faqs.json sin correr build_system_prompt.py no cambia al bot."""
    constructor = _modulo(RAIZ / "scripts" / "build_system_prompt.py", "constructor")
    compilado = (RAIZ / "src" / "sidhe_agent" / "graph" / "prompts" / "system.md").read_text(encoding="utf-8")
    assert compilado == constructor.construir(), (
        "system.md esta desactualizado: corre uv run python scripts/build_system_prompt.py"
    )
