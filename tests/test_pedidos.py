"""Tests del estado de pedidos: normalizacion, clasificacion y ventana."""

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


def test_solo_en_sucursal_autoriza_a_recoger():
    """La regla del negocio: lo demas sigue en fabricacion."""
    assert pedidos.clasificar_status("EN SUCURSAL") == pedidos.LISTO
    assert pedidos.clasificar_status("en sucursal ") == pedidos.LISTO
    assert pedidos.clasificar_status("ENTREGADO") == pedidos.ENTREGADO
    assert pedidos.clasificar_status("ENVIADO A DOMICILIO") == pedidos.ENVIADO
    assert (
        pedidos.clasificar_status("ENVIADO A DOMICILIO A DOMICILIO")
        == pedidos.ENVIADO
    )


def test_las_etapas_de_produccion_son_en_proceso():
    """Etapas internas y celdas vacias: se estan fabricando, no son excepciones."""
    for crudo in [
        "", "   ", "TERMINADO", "IMPRESION", "IMPRESION LISTA", "PEGADO",
        "PEDIDO", "una etapa que nadie escribio antes",
    ]:
        assert pedidos.clasificar_status(crudo) == pedidos.EN_PROCESO, crudo


def test_las_excepciones_reales_van_a_revision_humana():
    """Garantias y devoluciones nunca las interpreta el bot."""
    for crudo in [
        "VER EN GARANTIAS", "VER EN PX PEND ESTUDIOS", "VER EM PEND ESTUDIOS",
        "VER PENDIENTE EST", "NO PROCEDE", "GARANTIA CAMBIO", "CANCELADO",
    ]:
        assert pedidos.clasificar_status(crudo) == pedidos.REVISION, crudo


def test_ver_en_sucursal_no_se_confunde_con_listo():
    """Un 'VER EN...' empieza igual que el status bueno: gana la excepcion."""
    assert pedidos.clasificar_status("VER EN SUCURSAL") == pedidos.REVISION


def test_descarta_fechas_con_anio_mal_tecleado():
    """La hoja trae 0206-02-09 y 0325-04-23 por errores de captura."""
    assert pedidos._fecha_valida(datetime.datetime(206, 2, 9)) is None
    assert pedidos._fecha_valida(datetime.datetime(325, 4, 23)) is None
    assert pedidos._fecha_valida(datetime.datetime(2026, 9, 9)) == datetime.date(2026, 9, 9)
    assert pedidos._fecha_valida(None) is None


def test_lee_las_fechas_como_las_manda_la_api_de_sheets():
    """Sheets devuelve numeros de serie; el .xlsx, datetime."""
    assert pedidos._fecha_valida(46274) == datetime.date(2026, 9, 9)
    assert pedidos._fecha_valida("2026-09-09") == datetime.date(2026, 9, 9)
    assert pedidos._fecha_valida("09/09/2026") == datetime.date(2026, 9, 9)
    assert pedidos._fecha_valida("no es fecha") is None
    assert pedidos._fecha_valida(True) is None


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


def test_filas_de_sheets_vienen_truncadas():
    """La API omite las celdas vacias del final de cada fila."""
    registros = pedidos.preparar_filas([[46274, "ANA LOPEZ", "POLANCO"]])
    assert registros[0]["fecha"] == datetime.date(2026, 9, 9)
    assert registros[0]["telefono"] is None
    assert registros[0]["status"] == pedidos.EN_PROCESO


def _fila(fecha: datetime.date, nombre: str) -> tuple:
    return (fecha, nombre, "POLANCO", "", "", "5537049963", "EN SUCURSAL", "", "")


def test_la_ventana_de_meses_deja_fuera_el_historial():
    filas = [
        _fila(datetime.date(2021, 5, 1), "VIEJO UNO"),
        _fila(datetime.date(2026, 7, 20), "RECIENTE UNO"),
        _fila(datetime.date(2026, 9, 1), "RECIENTE DOS"),
    ]
    recientes = pedidos.preparar_filas(filas, meses=3)
    assert [r["nombre"] for r in recientes] == ["RECIENTE UNO", "RECIENTE DOS"]
    assert len(pedidos.preparar_filas(filas, meses=0)) == 3


def test_la_ventana_se_mide_desde_la_hoja_no_desde_hoy():
    """Si la hoja lleva meses sin tocarse, igual entran sus ultimos pedidos."""
    filas = [
        _fila(datetime.date(2020, 1, 1), "VIEJO"),
        _fila(datetime.date(2024, 2, 1), "ULTIMO CARGADO"),
    ]
    recientes = pedidos.preparar_filas(filas, meses=3)
    assert [r["nombre"] for r in recientes] == ["ULTIMO CARGADO"]


def test_columnas_faltantes_detecta_la_hoja_equivocada():
    assert pedidos.columnas_faltantes(
        ["Fecha", "Nombre", "Sucursal", "Envio", "Telefono", "Status"]
    ) == []
    assert pedidos.columnas_faltantes(["Producto", "Monto"]) == [
        "FECHA", "NOMBRE", "SUCURSAL"
    ]


def test_resumen_cuenta_lo_que_se_guardo():
    filas = [
        _fila(datetime.date(2026, 8, 1), "ANA LOPEZ"),
        (datetime.date(2026, 8, 2), "BEA RUIZ", "POLANCO", "", "", "", "PEGADO",
         "", ""),
    ]
    datos = pedidos.resumen(pedidos.preparar_filas(filas))
    assert datos["pedidos"] == 2
    assert datos["con_telefono"] == 1
    assert datos["desde"] == "2026-08-01"
    assert datos["hasta"] == "2026-08-02"
    assert datos["por_categoria"] == {pedidos.LISTO: 1, pedidos.EN_PROCESO: 1}


def test_la_sucursal_se_compara_con_tolerancia():
    """La hoja dice PARQUE DELTA y el cliente dice 'Liverpool Delta'."""
    assert pedidos.sucursal_compatible("Liverpool Delta", "PARQUE DELTA")
    assert pedidos.sucursal_compatible("delta", "PARQUE DELTA")
    assert pedidos.sucursal_compatible("Liverpool Satélite", "SATELITE")
    assert pedidos.sucursal_compatible("Puebla", "ANGELOPOLIS PUEBLA")
    assert pedidos.sucursal_compatible("SANTA FE", "Santa Fe")


def test_la_tolerancia_no_confunde_sucursales_distintas():
    assert not pedidos.sucursal_compatible("Polanco", "PERISUR")
    assert not pedidos.sucursal_compatible("Liverpool", "PERISUR")
    assert not pedidos.sucursal_compatible("", "PERISUR")
    assert not pedidos.sucursal_compatible("Perisur", "")


def test_cali_no_es_aguascalientes():
    """AguasCALIentes contiene CALI: comparar pedazos mentia al cliente."""
    assert not pedidos.sucursal_compatible(
        "CALI", "Liverpool Altaria Aguascalientes"
    )
    assert not pedidos.sucursal_compatible("LEON", "Liverpool Napoleon")


def test_relaciona_las_etiquetas_raras_de_la_hoja():
    """Las que operaciones escribe distinto a como se llaman."""
    assert pedidos.sucursal_compatible("GDL LA PERLA", "Liverpool La Perla Guadalajara")
    assert pedidos.sucursal_compatible("TOLUCA", "Liverpool Galerías Metepec")
    assert pedidos.sucursal_compatible("Metepec", "TOLUCA")
    assert pedidos.sucursal_compatible("SANLUIS", "SAN LUIS")
    assert pedidos.sucursal_compatible("Liverpool San Luis Potosí", "SAN LUIS")


CONOCIDAS = ["Liverpool Polanco", "polanco", "Liverpool Delta", "parque delta"]


def test_distingue_las_ventas_que_no_son_de_un_stand():
    """B2B (eventos, empresas) y plazas que no operamos por este canal."""
    assert pedidos.es_de_sucursal("POLANCO", CONOCIDAS)
    assert pedidos.es_de_sucursal("PARQUE DELTA", CONOCIDAS)
    for movil in ["BIMBO", "PFIZER", "world football summit", "CARRERA BATMAN",
                  "TRIATLON HEROICO", "COLEGIO ARGOS", "CALI", "MEDELLIN",
                  "UNICENTRO BOGOTA", ""]:
        assert not pedidos.es_de_sucursal(movil, CONOCIDAS), movil
