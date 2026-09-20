"""Cuánto lleva gastado el bot en llamadas al modelo desde que arrancó.

No se guarda en base de datos: se reinicia en cada Deploy, y con eso alcanza
para las dos preguntas que de verdad importan cuando la factura sube:
¿está pegando el caché del prompt? y ¿cuánto cuesta atender un mensaje?

Ojo con las cuentas: LangChain reporta `input_tokens` ya con los tokens
cacheados sumados, así que lo que se paga a precio completo es la resta.
"""

from dataclasses import dataclass

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


# Acumulado del proceso. Un solo event loop, no hace falta candado.
CONSUMO = Consumo()
