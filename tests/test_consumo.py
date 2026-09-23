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


def test_el_desglose_suma_los_dos_modelos():
    """El agente y las tareas internas se cobran a precios distintos."""
    from sidhe_agent.services.consumo import desglose

    filas = [
        # modelo, llamadas, entrada, salida, cache_lectura, cache_escritura
        ("claude-sonnet-5", 100, 1_000_000, 100_000, 900_000, 0),
        ("claude-haiku-4-5", 40, 40_000, 8_000, 0, 0),
    ]
    resumen = desglose(filas, 30)

    assert resumen["llamadas"] == 140
    assert resumen["dias"] == 30
    # Sonnet: 100k sin cache ($0.20) + 900k cacheados ($0.18) + salida ($1.00)
    # Haiku: 40k entrada ($0.04) + 8k salida ($0.04)
    assert resumen["costo_usd"] == 1.46
    assert resumen["porcentaje_en_cache"] == round(100 * 900_000 / 1_040_000, 1)
    assert [m["modelo"] for m in resumen["por_modelo"]] == [
        "claude-sonnet-5",
        "claude-haiku-4-5",
    ]


def test_el_desglose_aguanta_lo_que_devuelve_postgres():
    """sum() de un entero grande llega como Decimal, no como int."""
    import json
    from decimal import Decimal

    from sidhe_agent.services.consumo import desglose

    filas = [
        (
            "claude-sonnet-5",
            Decimal("100"),
            Decimal("1000000"),
            Decimal("100000"),
            Decimal("900000"),
            Decimal("0"),
        )
    ]
    resumen = desglose(filas, 30)

    assert resumen["costo_usd"] == 1.38
    # Y tiene que poder viajar al panel como JSON
    assert json.dumps(resumen)


def test_el_desglose_reporta_la_salida_promedio():
    from sidhe_agent.services.consumo import desglose

    resumen = desglose([("claude-sonnet-5", 10, 1000, 1500, 0, 0)], 7)
    assert resumen["salida_por_llamada"] == 150


def test_sin_datos_en_el_periodo():
    from sidhe_agent.services.consumo import desglose

    resumen = desglose([], 30)
    assert resumen["llamadas"] == 0
    assert resumen["costo_usd"] is None
    assert resumen["porcentaje_en_cache"] == 0.0


def test_lee_el_uso_de_una_respuesta_de_langchain():
    from langchain_core.messages import AIMessage

    from sidhe_agent.services.consumo import uso_de_respuesta

    mensaje = AIMessage(
        content="hola",
        usage_metadata={
            "input_tokens": 6100,
            "output_tokens": 120,
            "total_tokens": 6220,
            "input_token_details": {"cache_read": 5900, "cache_creation": 0},
        },
        response_metadata={"model": "claude-sonnet-5"},
    )
    modelo, tokens = uso_de_respuesta(mensaje)
    assert modelo == "claude-sonnet-5"
    assert tokens == {
        "entrada": 6100,
        "salida": 120,
        "cache_lectura": 5900,
        "cache_escritura": 0,
    }


async def test_si_falta_la_tabla_el_panel_no_se_cae():
    """Antes de correr la migración, las métricas deben seguir abriendo."""
    from unittest.mock import patch

    from sidhe_agent.services import consumo

    with patch.object(
        consumo, "get_session", side_effect=RuntimeError('relation "uso_modelo" no existe')
    ):
        resumen = await consumo.resumen_periodo(30)

    assert resumen["llamadas"] == 0
    assert resumen["costo_usd"] is None


def test_una_respuesta_sin_uso_no_se_cuenta():
    from langchain_core.messages import AIMessage

    from sidhe_agent.services.consumo import uso_de_respuesta

    assert uso_de_respuesta(AIMessage(content="hola")) is None
    assert uso_de_respuesta(None) is None


def test_se_cuentan_los_tokens_escritos_aunque_vengan_por_ttl():
    """Escribir caché cuesta 1.25x y no se estaba viendo.

    Cuando la respuesta dice de qué TTL fue el caché, LangChain pone los
    tokens en 'ephemeral_5m_input_tokens' y deja 'cache_creation' en cero
    para no contarlos dos veces. Leyendo solo la clave genérica, lo escrito
    salía siempre 0 y el costo del panel quedaba corto.
    """
    from types import SimpleNamespace

    from sidhe_agent.services.consumo import uso_de_respuesta

    respuesta = SimpleNamespace(
        usage_metadata={
            "input_tokens": 9000,
            "output_tokens": 120,
            "input_token_details": {
                "cache_read": 2000,
                "cache_creation": 0,
                "ephemeral_5m_input_tokens": 6000,
                "ephemeral_1h_input_tokens": 0,
            },
        },
        response_metadata={"model": "claude-haiku-4-5"},
    )

    modelo, tokens = uso_de_respuesta(respuesta)

    assert modelo == "claude-haiku-4-5"
    assert tokens["cache_lectura"] == 2000
    assert tokens["cache_escritura"] == 6000


def test_la_clave_generica_sigue_sirviendo():
    """No todas las respuestas traen el desglose por TTL."""
    from types import SimpleNamespace

    from sidhe_agent.services.consumo import uso_de_respuesta

    respuesta = SimpleNamespace(
        usage_metadata={
            "input_tokens": 5000,
            "output_tokens": 50,
            "input_token_details": {"cache_read": 0, "cache_creation": 4800},
        },
        response_metadata={"model": "claude-sonnet-5"},
    )

    _, tokens = uso_de_respuesta(respuesta)

    assert tokens["cache_escritura"] == 4800
