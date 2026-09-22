"""Cuánto gasta el bot en llamadas al modelo, medido en casa.

Se cuenta con un callback de LangChain enganchado a los modelos, no en un
punto del grafo: así entran TODAS las llamadas — la del agente, la que
extrae el perfil y la que resume conversaciones largas. Contar solo una de
las tres era la razón de que el panel mostrara mucho menos que la factura.

El acumulado se guarda por día y por modelo en la tabla `uso_modelo`,
porque en memoria se reinicia con cada Deploy y deja de ser comparable con
el recibo de Anthropic.

Ojo con las cuentas: LangChain reporta `input_tokens` ya con los tokens
cacheados sumados, así que lo que se paga a precio completo es la resta.
"""

import datetime
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from langchain_core.callbacks import AsyncCallbackHandler
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as insertar_pg

from ..config import get_settings
from ..db.models import UsoModelo
from ..db.session import get_session

logger = structlog.get_logger(__name__)

# USD por millón de tokens (entrada, salida), precios públicos de Anthropic.
# Un modelo que no esté en la tabla no rompe nada: solo no estima el costo.
PRECIOS = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
# Leer del caché cuesta la décima parte; escribirlo, un 25% de más
FACTOR_LECTURA = 0.1
FACTOR_ESCRITURA = 1.25


def precios_de(modelo: str) -> tuple[float, float] | None:
    if modelo in PRECIOS:
        return PRECIOS[modelo]
    for nombre, precios in PRECIOS.items():
        if modelo.startswith(nombre):
            return precios
    return None


@dataclass
class Consumo:
    llamadas: int = 0
    entrada: int = 0  # total, con lo cacheado incluido
    salida: int = 0
    cache_lectura: int = 0
    cache_escritura: int = 0

    def registrar(
        self,
        entrada: int = 0,
        salida: int = 0,
        cache_lectura: int = 0,
        cache_escritura: int = 0,
    ) -> None:
        self.llamadas += 1
        self.entrada += entrada or 0
        self.salida += salida or 0
        self.cache_lectura += cache_lectura or 0
        self.cache_escritura += cache_escritura or 0

    def costo_usd(self, modelo: str) -> float | None:
        precios = precios_de(modelo)
        if precios is None:
            return None
        por_entrada, por_salida = precios
        sin_cache = max(0, self.entrada - self.cache_lectura - self.cache_escritura)
        entrada_equivalente = (
            sin_cache
            + self.cache_lectura * FACTOR_LECTURA
            + self.cache_escritura * FACTOR_ESCRITURA
        )
        total = (entrada_equivalente * por_entrada + self.salida * por_salida) / 1e6
        return round(total, 4)

    def resumen(self, modelo: str) -> dict:
        en_cache = (
            round(100 * self.cache_lectura / self.entrada, 1) if self.entrada else 0.0
        )
        costo = self.costo_usd(modelo)
        return {
            "modelo": modelo,
            "llamadas": self.llamadas,
            "tokens_entrada": self.entrada,
            "tokens_salida": self.salida,
            # Si esto anda cerca de cero, el prompt se está pagando completo
            # en cada mensaje y ahí hay un ahorro grande esperando.
            "porcentaje_en_cache": en_cache,
            "costo_usd": costo,
            "costo_por_llamada_usd": (
                round(costo / self.llamadas, 5) if costo and self.llamadas else None
            ),
        }


def uso_de_respuesta(mensaje: Any) -> tuple[str, dict] | None:
    """(modelo, tokens) de una respuesta de LangChain, o None si no trae uso."""
    uso = getattr(mensaje, "usage_metadata", None)
    if not uso:
        return None
    meta = getattr(mensaje, "response_metadata", None) or {}
    modelo = meta.get("model") or meta.get("model_name") or "desconocido"
    detalle = uso.get("input_token_details") or {}
    return modelo, {
        "entrada": uso.get("input_tokens") or 0,
        "salida": uso.get("output_tokens") or 0,
        "cache_lectura": detalle.get("cache_read") or 0,
        "cache_escritura": detalle.get("cache_creation") or 0,
    }


async def registrar_uso(modelo: str, tokens: dict) -> None:
    """Suma una llamada al acumulado del día para ese modelo."""
    hoy = datetime.datetime.now(ZoneInfo(get_settings().tz)).date()
    insercion = insertar_pg(UsoModelo).values(
        fecha=hoy, modelo=modelo[:60], llamadas=1, **tokens
    )
    async with get_session() as session:
        await session.execute(
            insercion.on_conflict_do_update(
                constraint="uq_uso_fecha_modelo",
                set_={
                    "llamadas": UsoModelo.llamadas + 1,
                    "entrada": UsoModelo.entrada + tokens["entrada"],
                    "salida": UsoModelo.salida + tokens["salida"],
                    "cache_lectura": UsoModelo.cache_lectura + tokens["cache_lectura"],
                    "cache_escritura": (
                        UsoModelo.cache_escritura + tokens["cache_escritura"]
                    ),
                },
            )
        )
        await session.commit()


class ContadorDeTokens(AsyncCallbackHandler):
    """Anota cada llamada al modelo. Nunca interrumpe la conversación."""

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            for generaciones in response.generations:
                for generacion in generaciones:
                    datos = uso_de_respuesta(getattr(generacion, "message", None))
                    if not datos:
                        continue
                    modelo, tokens = datos
                    logger.info("uso_tokens", modelo=modelo, **tokens)
                    await registrar_uso(modelo, tokens)
        except Exception:
            logger.exception("error_registrando_uso_de_tokens")


CONTADOR = ContadorDeTokens()


async def resumen_periodo(dias: int) -> dict:
    """Gasto de los últimos `dias`, por modelo y en total.

    Nunca tumba el panel: si la tabla todavía no existe (falta correr la
    migración) o la consulta falla, devuelve el resumen en ceros. El resto
    de las métricas vale más que este dato.
    """
    desde = datetime.datetime.now(ZoneInfo(get_settings().tz)).date() - (
        datetime.timedelta(days=max(1, dias) - 1)
    )
    try:
        async with get_session() as session:
            filas = (
                await session.execute(
                    select(
                        UsoModelo.modelo,
                        func.sum(UsoModelo.llamadas),
                        func.sum(UsoModelo.entrada),
                        func.sum(UsoModelo.salida),
                        func.sum(UsoModelo.cache_lectura),
                        func.sum(UsoModelo.cache_escritura),
                    )
                    .where(UsoModelo.fecha >= desde)
                    .group_by(UsoModelo.modelo)
                    .order_by(func.sum(UsoModelo.entrada).desc())
                )
            ).all()
            # Desde cuándo hay datos: el conteo empezó con un Deploy, así que
            # decir "30 días" cuando solo hay dos se presta a malentendidos
            primer_dia = (
                await session.execute(
                    select(func.min(UsoModelo.fecha)).where(UsoModelo.fecha >= desde)
                )
            ).scalar()
    except Exception:
        logger.exception("error_leyendo_uso_del_modelo")
        filas, primer_dia = [], None
    resumen = desglose(filas, dias)
    resumen["desde"] = primer_dia.isoformat() if primer_dia else None
    return resumen


def desglose(filas: list, dias: int) -> dict:
    """Arma el resumen a partir de las filas agregadas de `uso_modelo`."""
    total = Consumo()
    por_modelo = []
    costo_total = 0.0
    se_pudo_costear = bool(filas)
    for modelo, llamadas, entrada, salida, lectura, escritura in filas:
        # int() a propósito: Postgres devuelve las sumas de enteros grandes
        # como Decimal, que ni se multiplica por los precios (float) ni se
        # serializa a JSON.
        uno = Consumo(
            llamadas=int(llamadas or 0),
            entrada=int(entrada or 0),
            salida=int(salida or 0),
            cache_lectura=int(lectura or 0),
            cache_escritura=int(escritura or 0),
        )
        total.llamadas += uno.llamadas
        total.entrada += uno.entrada
        total.salida += uno.salida
        total.cache_lectura += uno.cache_lectura
        total.cache_escritura += uno.cache_escritura
        costo = uno.costo_usd(modelo)
        if costo is None:
            se_pudo_costear = False
        else:
            costo_total += costo
        por_modelo.append({**uno.resumen(modelo), "modelo": modelo})
    en_cache = (
        round(100 * total.cache_lectura / total.entrada, 1) if total.entrada else 0.0
    )
    return {
        "dias": dias,
        "llamadas": total.llamadas,
        "tokens_entrada": total.entrada,
        "tokens_salida": total.salida,
        "porcentaje_en_cache": en_cache,
        "costo_usd": round(costo_total, 2) if se_pudo_costear else None,
        "salida_por_llamada": (
            round(total.salida / total.llamadas) if total.llamadas else 0
        ),
        "por_modelo": por_modelo,
    }
