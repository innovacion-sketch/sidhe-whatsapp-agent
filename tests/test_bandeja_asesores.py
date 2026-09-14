"""Tests de la bandeja de asesores: estados por color, filtros, cierre de
conversaciones y respuestas rapidas.

La regla que protegen: ROJO significa "alguien te esta esperando ahorita".
Si un cliente queda en rojo sin estar esperando, el equipo deja de creerle al
color; si queda sin rojo estando esperando, se queda hablando al vacio.
"""

import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidhe_agent.channels.whatsapp_twilio import WhatsAppTwilioAdapter
from sidhe_agent.config import get_settings
from sidhe_agent.main import app
from sidhe_agent.services import conversaciones as conv
from sidhe_agent.services import respuestas_rapidas as rr

CLAVE = "clave-interna-de-prueba"
USER = "+5215642934582"


def t(minutos: int) -> datetime.datetime:
    """Un instante relativo, para escribir la secuencia de hechos legible."""
    return datetime.datetime(2026, 9, 14, 10, 0) + datetime.timedelta(minutes=minutos)


def estado(escalado=None, humano=None, entrante=None, cierre=None) -> str:
    return conv.calcular_estado(
        escalado_desde=escalado, ultimo_humano=humano,
        ultimo_entrante=entrante, cerrada_en=cierre,
    )


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", CLAVE, raising=False)
    app.state.adapter = WhatsAppTwilioAdapter(
        account_sid="ACtest", auth_token="token", from_number="whatsapp:+521563"
    )
    app.state.adapters = {app.state.adapter.canal: app.state.adapter}
    return TestClient(app)


# --- estados y colores ---

def test_sin_escalamiento_la_lleva_el_bot():
    assert estado(entrante=t(0)) == conv.CON_BOT


def test_el_bot_escalo_y_nadie_contesto_es_rojo():
    assert estado(entrante=t(0), escalado=t(1)) == conv.ESPERANDO_ASESOR


def test_el_asesor_contesto_y_fue_lo_ultimo_es_verde():
    assert estado(entrante=t(0), escalado=t(1), humano=t(5)) == conv.ATENDIDA


def test_si_el_cliente_vuelve_a_escribir_despues_del_asesor_regresa_a_rojo():
    """Lo mas importante: 'atendida' no es para siempre."""
    assert estado(escalado=t(1), humano=t(5), entrante=t(9)) == conv.ESPERANDO_ASESOR


def test_una_respuesta_humana_de_antes_del_escalamiento_no_cuenta():
    """Un asesor contesto la semana pasada; hoy el bot escalo de nuevo."""
    assert estado(humano=t(-5000), entrante=t(0), escalado=t(1)) == conv.ESPERANDO_ASESOR


def test_cerrada_es_verde_aunque_hubiera_escalamiento():
    assert estado(entrante=t(0), escalado=t(1), cierre=t(10)) == conv.CERRADA


def test_una_cerrada_se_reabre_sola_cuando_el_cliente_escribe():
    assert estado(entrante=t(20), cierre=t(10)) == conv.CON_BOT
    assert estado(entrante=t(20), cierre=t(10), escalado=t(21)) == conv.ESPERANDO_ASESOR


def test_desde_cuando_espera_el_cliente():
    assert conv.esperando_desde(t(1), None, t(0)) == t(1)
    # El asesor contesto y el cliente volvio a escribir: cuenta desde ese mensaje
    assert conv.esperando_desde(t(1), t(5), t(9)) == t(9)
    assert conv.esperando_desde(None, None, t(0)) is None


def _conversacion(user_id: str, estado_conv: str) -> dict:
    return {"canal": "whatsapp", "user_id": user_id, "estado": estado_conv}


def test_los_conteos_no_dependen_del_filtro():
    """La pestana 'Esperando asesor (2)' dice 2 aunque estes viendo otra."""
    todas = [
        _conversacion("a", conv.ESPERANDO_ASESOR),
        _conversacion("b", conv.ESPERANDO_ASESOR),
        _conversacion("c", conv.CON_BOT),
        _conversacion("d", conv.CERRADA),
    ]
    resultado = conv.filtrar(todas, conv.CON_BOT, limite=50)
    assert [c["user_id"] for c in resultado["conversaciones"]] == ["c"]
    assert resultado["conteos"] == {
        conv.ESPERANDO_ASESOR: 2, conv.ATENDIDA: 0, conv.CERRADA: 1,
        conv.CON_BOT: 1, "todas": 4,
    }


def test_un_filtro_desconocido_muestra_todas():
    todas = [_conversacion("a", conv.CON_BOT), _conversacion("b", conv.CERRADA)]
    assert len(conv.filtrar(todas, "inventado", 50)["conversaciones"]) == 2
    assert len(conv.filtrar(todas, "", 50)["conversaciones"]) == 2


def test_el_limite_se_aplica_despues_de_filtrar():
    todas = [_conversacion(str(i), conv.CON_BOT) for i in range(10)]
    todas.append(_conversacion("esperando", conv.ESPERANDO_ASESOR))
    resultado = conv.filtrar(todas, conv.ESPERANDO_ASESOR, limite=3)
    assert [c["user_id"] for c in resultado["conversaciones"]] == ["esperando"]


# --- cerrar conversacion ---

def test_cerrar_exige_clave(cliente):
    r = cliente.post(f"/internal/conversaciones/whatsapp/{USER}/cerrar")
    assert r.status_code == 401


def test_cerrar_resuelve_y_le_avisa_al_agente(cliente):
    with (
        patch("sidhe_agent.main.conversaciones.cerrar", AsyncMock(return_value=1)) as cerrar,
        patch("sidhe_agent.main._devolver_al_agente", AsyncMock(return_value=True)) as devolver,
    ):
        r = cliente.post(
            f"/internal/conversaciones/whatsapp/{USER}/cerrar",
            headers={"X-API-Key": CLAVE},
        )
    assert r.status_code == 200
    assert r.json()["estado"] == conv.CERRADA
    assert r.json()["escalamientos_resueltos"] == 1
    cerrar.assert_awaited_once_with("whatsapp", USER)
    nota = devolver.await_args.args[2]
    assert "consulta nueva" in nota


def test_cerrar_no_le_manda_nada_al_cliente(cliente):
    """Despedirse es decision del asesor (para eso esta /cierre)."""
    with (
        patch("sidhe_agent.main.conversaciones.cerrar", AsyncMock(return_value=0)),
        patch("sidhe_agent.main._devolver_al_agente", AsyncMock(return_value=False)),
        patch.object(app.state.adapter, "send", AsyncMock()) as envio,
    ):
        cliente.post(
            f"/internal/conversaciones/whatsapp/{USER}/cerrar",
            headers={"X-API-Key": CLAVE},
        )
    envio.assert_not_awaited()


def test_el_cierre_queda_aunque_falle_la_nota_al_agente(cliente):
    with (
        patch("sidhe_agent.main.conversaciones.cerrar", AsyncMock(return_value=0)),
        patch(
            "sidhe_agent.main._devolver_al_agente",
            AsyncMock(side_effect=RuntimeError("hilo sin checkpoint")),
        ),
    ):
        r = cliente.post(
            f"/internal/conversaciones/whatsapp/{USER}/cerrar",
            headers={"X-API-Key": CLAVE},
        )
    assert r.status_code == 200
    assert r.json()["nota_agregada"] is False


# --- respuestas rapidas ---

def test_el_atajo_se_normaliza_para_encontrarlo_siempre():
    assert rr.normalizar_atajo("/Garantía") == "garantia"
    assert rr.normalizar_atajo("  Plantillas Listas ") == "plantillas-listas"
    assert rr.normalizar_atajo("¿horario?") == "horario"
    assert rr.normalizar_atajo("x" * 80) == "x" * rr.MAX_ATAJO


def test_no_se_guarda_una_respuesta_incompleta():
    with pytest.raises(rr.ErrorRespuesta):
        rr.validar("", "hola")
    with pytest.raises(rr.ErrorRespuesta):
        rr.validar("!!!", "hola")  # queda vacio tras normalizar
    with pytest.raises(rr.ErrorRespuesta):
        rr.validar("saludo", "   ")
    with pytest.raises(rr.ErrorRespuesta):
        rr.validar("largo", "a" * (rr.MAX_TEXTO + 1))
    assert rr.validar("/Saludo", "  Hola  ") == ("saludo", "Hola")


def test_las_respuestas_rapidas_exigen_clave(cliente):
    assert cliente.get("/internal/respuestas-rapidas").status_code == 401
    assert cliente.post(
        "/internal/respuestas-rapidas", json={"atajo": "a", "texto": "b"}
    ).status_code == 401


def test_crear_una_respuesta_rapida(cliente):
    creada = {"id": 1, "atajo": "saludo", "texto": "Hola"}
    with patch("sidhe_agent.main.respuestas_rapidas.crear", AsyncMock(return_value=creada)):
        r = cliente.post(
            "/internal/respuestas-rapidas",
            json={"atajo": "/Saludo", "texto": "Hola"},
            headers={"X-API-Key": CLAVE},
        )
    assert r.status_code == 201
    assert r.json()["atajo"] == "saludo"


def test_un_atajo_repetido_se_explica_con_409(cliente):
    with patch(
        "sidhe_agent.main.respuestas_rapidas.crear",
        AsyncMock(side_effect=rr.AtajoDuplicado("Ya existe una respuesta con el atajo /saludo.")),
    ):
        r = cliente.post(
            "/internal/respuestas-rapidas",
            json={"atajo": "saludo", "texto": "Hola"},
            headers={"X-API-Key": CLAVE},
        )
    assert r.status_code == 409
    assert "/saludo" in r.json()["detail"]


def test_llegar_al_limite_se_explica_con_400(cliente):
    with patch(
        "sidhe_agent.main.respuestas_rapidas.crear",
        AsyncMock(side_effect=rr.LimiteAlcanzado("Ya tienes 100 respuestas rápidas.")),
    ):
        r = cliente.post(
            "/internal/respuestas-rapidas",
            json={"atajo": "otra", "texto": "Hola"},
            headers={"X-API-Key": CLAVE},
        )
    assert r.status_code == 400


def test_editar_o_borrar_una_que_ya_no_existe_da_404(cliente):
    with patch("sidhe_agent.main.respuestas_rapidas.actualizar", AsyncMock(return_value=None)):
        r = cliente.put(
            "/internal/respuestas-rapidas/99",
            json={"atajo": "a", "texto": "b"},
            headers={"X-API-Key": CLAVE},
        )
    assert r.status_code == 404
    with patch("sidhe_agent.main.respuestas_rapidas.borrar", AsyncMock(return_value=False)):
        r = cliente.delete("/internal/respuestas-rapidas/99", headers={"X-API-Key": CLAVE})
    assert r.status_code == 404


def test_las_respuestas_iniciales_no_traen_precios_ni_confort():
    """Los precios cambian y viven en las FAQs; 'confort' esta prohibido."""
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).parent.parent / "alembic" / "versions" / "0004_bandeja_asesores.py"
    spec = importlib.util.spec_from_file_location("migracion_0004", ruta)
    migracion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migracion)

    atajos = [a for a, _ in migracion.RESPUESTAS_INICIALES]
    assert len(atajos) == len(set(atajos)), "atajos repetidos en la semilla"
    for atajo, texto in migracion.RESPUESTAS_INICIALES:
        assert atajo == rr.normalizar_atajo(atajo), atajo
        assert "$" not in texto and "pesos" not in texto.lower(), atajo
        assert "confort" not in texto.lower(), atajo
