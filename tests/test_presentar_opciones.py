"""Tests de la tool presentar_opciones (sin DB)."""

import json

from langgraph.types import Command

from sidhe_agent.tools.presentacion import presentar_opciones


def _invocar(tipo: str, titulo: str, opciones: list[dict]) -> Command:
    # Una tool que devuelve Command debe invocarse como ToolCall completo
    # (así la llama ToolNode en el grafo real).
    return presentar_opciones.invoke(
        {
            "name": "presentar_opciones",
            "type": "tool_call",
            "id": "tc_test",
            "args": {"tipo": tipo, "titulo": titulo, "opciones": opciones},
        }
    )


def test_lista_valida_actualiza_estado():
    opciones = [{"id": f"suc_{n}", "etiqueta": f"Sucursal {n}"} for n in range(3)]
    comando = _invocar("lista", "Elige sucursal", opciones)
    assert isinstance(comando, Command)
    ui = comando.update["ui_pendiente"]
    assert ui["tipo"] == "lista"
    assert ui["titulo"] == "Elige sucursal"
    assert [o["id"] for o in ui["opciones"]] == ["suc_0", "suc_1", "suc_2"]


def test_botones_validos():
    comando = _invocar(
        "botones",
        "Confirmación",
        [
            {"id": "confirmar", "etiqueta": "Confirmar ✅"},
            {"id": "cambiar", "etiqueta": "Cambiar"},
            {"id": "cancelar", "etiqueta": "Cancelar"},
        ],
    )
    assert comando.update["ui_pendiente"]["tipo"] == "botones"


def test_lista_con_mas_de_10_es_error():
    opciones = [{"id": f"x_{n}", "etiqueta": f"Opción {n}"} for n in range(11)]
    comando = _invocar("lista", "Demasiadas", opciones)
    assert "ui_pendiente" not in comando.update
    contenido = json.loads(comando.update["messages"][0].content)
    assert contenido["error"] == "ui_invalida"


def test_botones_con_mas_de_3_es_error():
    opciones = [{"id": f"x_{n}", "etiqueta": f"Opción {n}"} for n in range(4)]
    comando = _invocar("botones", "Demasiados", opciones)
    contenido = json.loads(comando.update["messages"][0].content)
    assert contenido["error"] == "ui_invalida"
