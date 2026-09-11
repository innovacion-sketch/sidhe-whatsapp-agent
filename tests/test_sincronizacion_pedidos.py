"""Tests de la sincronizacion con el Google Sheet de operaciones.

Sin red: se simula la respuesta de la API de Sheets. Lo que se comprueba es
la regla de oro — si la hoja no se puede leer o viene mal, la copia que ya
esta en la base NO se toca y el bot sigue contestando con ella.
"""

import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidhe_agent.config import get_settings
from sidhe_agent.main import app
from sidhe_agent.services import google_sheets, pedidos

CLAVE = "clave-interna-de-prueba"

ENCABEZADO = [
    "Fecha", "Nombre", "Sucursal", "Envio", "Direccion de envio a domicilio",
    "Telefono", "Status", "Lugar de Impresion", "Localizacion Final",
]


def _hoja(filas: list[list]) -> list[list]:
    return [ENCABEZADO, *filas]


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", CLAVE, raising=False)
    return TestClient(app)


async def test_sincroniza_y_resume_lo_guardado():
    hoja = _hoja([
        [46274, "ANA LOPEZ GARCIA", "POLANCO", "", "", "5537049963",
         "EN SUCURSAL", "OFICINA", "POLANCO"],
        [46273, "BEA RUIZ SOTO", "PERISUR", "", "", "", "PEGADO"],
        [40000, "VIEJO HISTORICO", "POLANCO", "", "", "", "ENTREGADO"],
    ])
    guardados = []
    with patch.object(
        google_sheets, "descargar_status", AsyncMock(return_value=hoja)
    ), patch.object(
        pedidos, "reemplazar", AsyncMock(side_effect=lambda r: guardados.extend(r))
    ):
        datos = await google_sheets.sincronizar(meses=3)

    # El historico de 2009 (serial 40000) queda fuera: ni siquiera pasa el
    # filtro de anios validos de la hoja
    assert datos["pedidos"] == 2
    assert datos["filas_en_hoja"] == 3
    assert datos["con_telefono"] == 1
    assert datos["hasta"] == "2026-09-09"
    assert [r["nombre"] for r in guardados] == ["ANA LOPEZ GARCIA", "BEA RUIZ SOTO"]
    assert guardados[0]["status"] == pedidos.LISTO
    assert guardados[1]["status"] == pedidos.EN_PROCESO


async def test_no_borra_nada_si_la_hoja_es_la_equivocada():
    """Si alguien cambia el rango, preferimos fallar a vaciar la tabla."""
    hoja = [["Producto", "Monto"], ["Plantilla", 2899]]
    reemplazar = AsyncMock()
    with patch.object(
        google_sheets, "descargar_status", AsyncMock(return_value=hoja)
    ), patch.object(pedidos, "reemplazar", reemplazar):
        with pytest.raises(google_sheets.ErrorSincronizacion):
            await google_sheets.sincronizar(meses=3)
    reemplazar.assert_not_awaited()


async def test_no_borra_nada_si_la_ventana_queda_vacia():
    hoja = _hoja([["texto sin fecha", "ANA LOPEZ", "POLANCO"]])
    reemplazar = AsyncMock()
    with patch.object(
        google_sheets, "descargar_status", AsyncMock(return_value=hoja)
    ), patch.object(pedidos, "reemplazar", reemplazar):
        with pytest.raises(google_sheets.ErrorSincronizacion):
            await google_sheets.sincronizar(meses=3)
    reemplazar.assert_not_awaited()


async def test_sin_credenciales_avisa_en_vez_de_reventar():
    with pytest.raises(google_sheets.ErrorSincronizacion):
        await google_sheets.descargar_status()


def test_endpoint_de_sincronizacion_exige_clave(cliente):
    assert cliente.post("/internal/pedidos/sincronizar").status_code == 401


def test_endpoint_devuelve_el_resumen(cliente):
    resumen = {
        "pedidos": 4942, "con_telefono": 4690, "desde": "2025-12-23",
        "hasta": "2026-03-23", "por_categoria": {pedidos.LISTO: 3986},
        "filas_en_hoja": 47398, "meses": 3,
    }
    with patch(
        "sidhe_agent.main.google_sheets.sincronizar",
        AsyncMock(return_value=resumen),
    ):
        r = cliente.post(
            "/internal/pedidos/sincronizar", headers={"X-API-Key": CLAVE}, json={}
        )
    assert r.status_code == 200
    assert r.json()["pedidos"] == 4942


def test_endpoint_responde_503_cuando_google_falla(cliente):
    """n8n reintenta; mientras tanto el bot contesta con la copia anterior."""
    with patch(
        "sidhe_agent.main.google_sheets.sincronizar",
        AsyncMock(side_effect=google_sheets.ErrorSincronizacion("sin permiso")),
    ):
        r = cliente.post(
            "/internal/pedidos/sincronizar", headers={"X-API-Key": CLAVE}, json={}
        )
    assert r.status_code == 503
    assert "sin permiso" in r.json()["detail"]


def test_la_epoca_de_sheets_coincide_con_la_de_excel():
    """Sheets y Excel cuentan los dias desde 1899-12-30."""
    assert pedidos._fecha_valida(42374) == datetime.date(2016, 1, 5)


async def test_un_pedido_b2b_se_manda_con_un_asesor():
    """Aunque diga EN SUCURSAL: no sabemos a que stand mandar al cliente."""
    from sidhe_agent.tools import pedidos as herramienta

    encontrados = [
        {"nombre": "ANA LOPEZ", "sucursal": "BIMBO", "status": pedidos.LISTO,
         "status_en_sistema": "EN SUCURSAL", "donde_esta": "BIMBO",
         "fecha_del_estudio": "2026-09-01"},
        {"nombre": "ANA LOPEZ", "sucursal": "POLANCO", "status": pedidos.LISTO,
         "status_en_sistema": "EN SUCURSAL", "donde_esta": "POLANCO",
         "fecha_del_estudio": "2026-09-02"},
    ]
    with patch.object(
        pedidos, "etiquetas_de_sucursales",
        AsyncMock(return_value=["Liverpool Polanco", "polanco"]),
    ):
        salida = await herramienta._formatear(encontrados)

    b2b, stand = salida["pedidos"]
    assert b2b["es_de_sucursal"] is False
    assert "escalar_a_humano" in b2b["que_decir"]
    assert stand["es_de_sucursal"] is True
    assert "puede pasar a recogerlas" in stand["que_decir"]
