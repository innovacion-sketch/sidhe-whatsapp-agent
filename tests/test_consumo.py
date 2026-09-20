"""El contador de gasto del modelo: cuentas y caché."""

from sidhe_agent.services.consumo import Consumo, precios_de


def test_lo_cacheado_cuesta_la_decima_parte():
    """Un millón de tokens leídos del caché sale en $0.20, no en $2."""
    caro = Consumo()
    caro.registrar(entrada=1_000_000, salida=0)

    barato = Consumo()
    barato.registrar(entrada=1_000_000, salida=0, cache_lectura=1_000_000)

    assert caro.costo_usd("claude-sonnet-5") == 2.0
    assert barato.costo_usd("claude-sonnet-5") == 0.2


def test_escribir_el_cache_cuesta_un_poco_mas():
    consumo = Consumo()
    consumo.registrar(entrada=1_000_000, salida=0, cache_escritura=1_000_000)
    assert consumo.costo_usd("claude-sonnet-5") == 2.5


def test_suma_entrada_y_salida():
    consumo = Consumo()
    consumo.registrar(entrada=1_000_000, salida=1_000_000)
    assert consumo.costo_usd("claude-sonnet-5") == 12.0  # 2 de entrada + 10 de salida


def test_porcentaje_en_cache():
    consumo = Consumo()
    consumo.registrar(entrada=10_000, salida=100, cache_lectura=9_000)
    resumen = consumo.resumen("claude-sonnet-5")
    assert resumen["porcentaje_en_cache"] == 90.0
    assert resumen["llamadas"] == 1


def test_sin_llamadas_no_divide_entre_cero():
    resumen = Consumo().resumen("claude-sonnet-5")
    assert resumen["porcentaje_en_cache"] == 0.0
    assert resumen["costo_por_llamada_usd"] is None


def test_modelo_desconocido_no_revienta():
    consumo = Consumo()
    consumo.registrar(entrada=1000, salida=100)
    assert consumo.costo_usd("modelo-inventado") is None
    assert consumo.resumen("modelo-inventado")["costo_usd"] is None


def test_reconoce_modelos_con_fecha_al_final():
    assert precios_de("claude-haiku-4-5-20251001") == (1.0, 5.0)
