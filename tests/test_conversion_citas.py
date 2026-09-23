"""Atribución de una compra a una cita."""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from conversion_citas import porcentaje, vendio  # noqa: E402

from sidhe_agent.services.pedidos import normalizar_telefono  # noqa: E402

CITA = datetime.date(2026, 9, 1)


def test_la_compra_del_mismo_dia_cuenta():
    assert vendio(CITA, CITA, 60)


def test_la_compra_posterior_dentro_de_la_ventana_cuenta():
    assert vendio(CITA, CITA + datetime.timedelta(days=30), 60)
    assert vendio(CITA, CITA + datetime.timedelta(days=60), 60)


def test_una_compra_anterior_no_la_trajo_la_cita():
    assert not vendio(CITA, CITA - datetime.timedelta(days=1), 60)


def test_una_compra_muy_posterior_tampoco():
    assert not vendio(CITA, CITA + datetime.timedelta(days=61), 60)


def test_sin_compra_no_hay_venta():
    assert not vendio(CITA, None, 60)


def test_los_telefonos_de_los_dos_lados_cruzan():
    """WhatsApp manda +5215535109140; la hoja guarda 10 dígitos."""
    assert normalizar_telefono("+5215535109140") == normalizar_telefono("5535109140")
    assert normalizar_telefono("55 3510 9140") == "5535109140"
    assert normalizar_telefono("123") is None


def test_porcentaje_sin_datos_no_divide_entre_cero():
    assert porcentaje(0, 0) == "—"
    assert porcentaje(3, 12) == "25.0%"
