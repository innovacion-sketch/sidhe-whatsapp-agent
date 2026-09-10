"""Test de la red de seguridad: el bot retoma si nadie atiende."""

import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidhe_agent.config import get_settings
from sidhe_agent.main import app
from sidhe_agent.services import conversaciones

CLAVE = "clave-interna-de-prueba"


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", CLAVE, raising=False)
    return TestClient(app)


def test_pendientes_exige_clave(cliente):
    assert cliente.get("/internal/escalamientos/pendientes").status_code == 401


def test_pendientes_reporta_horas_de_espera(cliente):
    """n8n usa esto para avisar al equipo antes de que el cliente se canse."""
    falsos = [{
        "id": 1, "canal": "whatsapp", "user_id": "+52155",
        "motivo": "estado de pedido",
        "creado_en": "2026-09-04T10:00:00", "horas_esperando": 5.2,
    }]
    with patch(
        "sidhe_agent.main.conversaciones.pendientes_detallados",
        AsyncMock(return_value=falsos),
    ):
        r = cliente.get(
            "/internal/escalamientos/pendientes", headers={"X-API-Key": CLAVE}
        )
    assert r.status_code == 200
    datos = r.json()
    assert datos["total"] == 1
    assert datos["escalamientos"][0]["horas_esperando"] == 5.2
    assert datos["horas_para_reactivar_bot"] == get_settings().horas_reactivar_bot


def test_estados_de_pausa_son_tres():
    assert {conversaciones.ACTIVO, conversaciones.PAUSADO,
            conversaciones.REACTIVADO} == {"activo", "pausado", "reactivado"}
