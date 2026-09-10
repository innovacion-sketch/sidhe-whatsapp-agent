"""Tests del estado de pedidos y de la reactivacion de conversaciones."""

import datetime

from sidhe_agent.services import pedidos


def test_normaliza_nombres_con_acentos_y_espacios():
    assert pedidos.normalizar_texto("  José  Ramírez  ") == "JOSE RAMIREZ"
    assert pedidos.normalizar_texto("MUÑOZ  PEÑA") == "MUNOZ PENA"
    assert pedidos.normalizar_texto(None) == ""


def test_telefono_toma_los_ultimos_10_digitos():
    """WhatsApp manda +5215537049963; la hoja guarda 5537049963."""
    assert pedidos.normalizar_telefono("+5215537049963") == "5537049963"
    assert pedidos.normalizar_telefono("5537049963") == "5537049963"
    assert pedidos.normalizar_telefono("55 3704 9963") == "5537049963"
    assert pedidos.normalizar_telefono("123") is None
    assert pedidos.normalizar_telefono(None) is None


def test_clasifica_los_status_reales_de_la_hoja():
    assert pedidos.clasificar_status("EN SUCURSAL") == pedidos.LISTO
    assert pedidos.clasificar_status("ENTREGADO") == pedidos.ENTREGADO
    assert pedidos.clasificar_status("IMPRESION") == pedidos.EN_PROCESO
    assert pedidos.clasificar_status("ENVIADO A DOMICILIO") == pedidos.ENVIADO
    # Variante duplicada que trae la hoja
    assert (
        pedidos.clasificar_status("ENVIADO A DOMICILIO A DOMICILIO")
        == pedidos.ENVIADO
    )


def test_lo_ambiguo_va_a_revision_humana():
    """Nunca inventar: lo que no es claro lo ve un asesor."""
    for crudo in [
        "", "   ", "VER EN GARANTIAS", "VER EN PX PEND ESTUDIOS",
        "VER EM PEND ESTUDIOS", "VER PENDIENTE EST", "NO PROCEDE",
        "algo que nadie escribio antes",
    ]:
        assert pedidos.clasificar_status(crudo) == pedidos.REVISION, crudo


def test_descarta_fechas_con_anio_mal_tecleado():
    """La hoja trae 0206-02-09 y 0325-04-23 por errores de captura."""
    assert pedidos._fecha_valida(datetime.datetime(206, 2, 9)) is None
    assert pedidos._fecha_valida(datetime.datetime(325, 4, 23)) is None
    assert pedidos._fecha_valida(datetime.datetime(2026, 9, 9)) == datetime.date(2026, 9, 9)
    assert pedidos._fecha_valida(None) is None


def test_prepara_filas_de_la_hoja():
    filas = [
        (datetime.datetime(2026, 8, 14), "  ANA LOPEZ GARCIA ", "PARQUE DELTA",
         "S PARQUE DELTA", "N/A", "5537049963", "EN SUCURSAL", "OFICINA",
         "PARQUE DELTA"),
        (None, "", "POLANCO", "", "", "", "ENTREGADO", "", ""),  # sin nombre
    ]
    registros = pedidos.preparar_filas(filas)
    assert len(registros) == 1  # la fila sin nombre se descarta
    r = registros[0]
    assert r["nombre"] == "ANA LOPEZ GARCIA"
    assert r["nombre_normalizado"] == "ANA LOPEZ GARCIA"
    assert r["telefono"] == "5537049963"
    assert r["status"] == pedidos.LISTO
    assert r["fecha"] == datetime.date(2026, 8, 14)
