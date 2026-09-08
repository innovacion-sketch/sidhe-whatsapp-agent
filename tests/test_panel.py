"""Tests del panel de metricas: autenticacion y forma de la respuesta."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidhe_agent.config import get_settings
from sidhe_agent.main import app

CLAVE = "clave-interna-de-prueba"


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", CLAVE, raising=False)
    return TestClient(app)


def test_metricas_exige_clave(cliente):
    assert cliente.get("/internal/metricas").status_code == 401
    assert cliente.get(
        "/internal/metricas", headers={"X-API-Key": "otra"}
    ).status_code == 401


def test_metricas_valida_el_rango_de_dias(cliente):
    r = cliente.get("/internal/metricas?dias=0", headers={"X-API-Key": CLAVE})
    assert r.status_code == 400
    r = cliente.get("/internal/metricas?dias=999", headers={"X-API-Key": CLAVE})
    assert r.status_code == 400


def test_metricas_devuelve_los_bloques_del_panel(cliente):
    falsas = {
        "dias": 30,
        "resumen": {"clientes": 3, "mensajes": 40, "entrantes": 20,
                    "salientes": 20, "notas_de_voz": 1, "toques_de_boton": 5},
        "citas": {"total": 2, "confirmadas": 2, "canceladas": 0, "en_calendario": 1},
        "escalamientos": {"total": 1, "pendientes": 1},
        "por_canal": [{"canal": "whatsapp", "mensajes": 40, "clientes": 3}],
        "serie_diaria": [{"dia": "2026-09-01", "mensajes": 40, "clientes": 3}],
        "top_sucursales": [{"sucursal": "Liverpool Perisur", "citas": 2}],
        "proximas_citas": [],
        "primera_respuesta_seg": 4.2,
        "conversion": 66.7,
    }
    with patch("sidhe_agent.main.metricas.calcular", AsyncMock(return_value=falsas)):
        r = cliente.get("/internal/metricas?dias=30", headers={"X-API-Key": CLAVE})
    assert r.status_code == 200
    assert r.json()["conversion"] == 66.7
    assert r.json()["por_canal"][0]["canal"] == "whatsapp"


def test_panel_se_sirve_sin_secretos(cliente):
    """La pagina es publica; los datos los pide con la clave del usuario."""
    r = cliente.get("/panel")
    assert r.status_code == 200
    assert "Panel Sidhe" in r.text
    assert CLAVE not in r.text
