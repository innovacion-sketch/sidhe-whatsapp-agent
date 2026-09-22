"""Lectura del sistema de asistencias (base `asistencias`, solo consultas).

Sirve para dos cosas:

1. **No agendar donde no habrá nadie.** Si el rol de la semana dice que en
   esa sucursal ese día nadie trabaja, el bot no genera horarios.
2. **Avisar cuando ya se agendó y el stand no abrió.** Eso lo resuelve
   `alertas_citas.py` con `sucursales_sin_checada`.

Dos reglas que valen más que el ahorro de una consulta:

- **Ante la duda, se agenda.** Si la base no responde, si la sucursal no
  existe allá o si la semana todavía no se carga, esto devuelve "no sé" y
  la agenda sigue como siempre. Bloquear por falta de datos dejaría al
  negocio sin citas cada vez que el rol se sube tarde.
- **Solo se bloquea con evidencia.** Un día se descarta únicamente cuando
  hay horarios cargados para esa sucursal ese día y todos son descanso o
  ninguno tiene horas de trabajo.

Las sucursales se cruzan por nombre: los dos sistemas usan exactamente los
mismos ("Liverpool Perisur", "Liverpool Galerías Metepec"...).
"""

import datetime
from typing import Any

import structlog
from sqlalchemy import Date, Time, bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ..config import get_settings

logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None

# Días con rol cargado en los que NADIE trabaja en esa sucursal
SQL_DIAS_SIN_PERSONAL_TEXTO = """
    SELECT s.nombre, hp.fecha
      FROM horarios_programados hp
      JOIN empleados e ON e.id = hp.empleado_id
      JOIN sucursales s ON s.id = e.sucursal_principal_id
     WHERE hp.fecha BETWEEN :desde AND :hasta
       AND e.activo = true
       AND s.activa = true
     GROUP BY s.nombre, hp.fecha
    HAVING COUNT(*) FILTER (
             WHERE hp.es_descanso = false
               AND hp.hora_entrada IS NOT NULL
               AND hp.hora_salida IS NOT NULL
           ) = 0
    """
SQL_DIAS_SIN_PERSONAL = text(SQL_DIAS_SIN_PERSONAL_TEXTO).bindparams(
    bindparam("desde", type_=Date()), bindparam("hasta", type_=Date())
)

# Sucursales donde hoy había gente programada y nadie ha marcado entrada
SQL_SIN_CHECADA = text(
    """
    WITH programados AS (
        SELECT e.sucursal_principal_id AS sucursal_id, e.id AS empleado_id
          FROM horarios_programados hp
          JOIN empleados e ON e.id = hp.empleado_id
         WHERE hp.fecha = :hoy
           AND hp.es_descanso = false
           AND hp.hora_entrada IS NOT NULL
           AND e.activo = true
           AND (:hora)::time >= (
                 hp.hora_entrada
                 + (COALESCE(hp.tolerancia_min, 15) || ' minutes')::interval
               )::time
    )
    SELECT s.nombre
      FROM sucursales s
      JOIN programados p ON p.sucursal_id = s.id
      LEFT JOIN jornadas j
             ON j.empleado_id = p.empleado_id
            AND j.fecha = :hoy
            AND j.entrada_at IS NOT NULL
     WHERE s.activa = true AND s.es_virtual = false
     GROUP BY s.nombre
    HAVING COUNT(j.id) = 0
    """
).bindparams(
    # Tipos explícitos: el driver necesita un date y un time de verdad, no
    # texto, y con text() no los adivina.
    bindparam("hoy", type_=Date()),
    bindparam("hora", type_=Time()),
)


def configurado() -> bool:
    return bool(get_settings().asistencias_url)


def _motor() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().asistencias_url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
        )
    return _engine


async def cerrar_motor() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def _consultar(sql: Any, parametros: dict) -> list[tuple] | None:
    """Ejecuta una consulta. None = no se pudo saber (nunca bloquea nada)."""
    if not configurado():
        return None
    try:
        async with _motor().connect() as conexion:
            return (await conexion.execute(sql, parametros)).all()
    except Exception:
        logger.exception("error_consultando_asistencias")
        return None


async def dias_sin_personal(
    desde: datetime.date, hasta: datetime.date
) -> set[tuple[str, datetime.date]]:
    """Pares (sucursal, fecha) donde el rol dice que no habrá nadie.

    Vacío si no se pudo consultar: sin datos se agenda normal.
    """
    filas = await _consultar(
        SQL_DIAS_SIN_PERSONAL, {"desde": desde, "hasta": hasta}
    )
    if not filas:
        return set()
    sin_personal = {(nombre, fecha) for nombre, fecha in filas}
    logger.info("dias_sin_personal", dias=len(sin_personal))
    return sin_personal


async def sucursales_sin_checada(
    hoy: datetime.date, hora: datetime.time
) -> set[str] | None:
    """Sucursales donde alguien debía haber abierto y nadie marcó entrada.

    None = no se pudo consultar (y entonces no se alerta nada, para no
    inventar una emergencia por una falla de conexión).
    """
    filas = await _consultar(SQL_SIN_CHECADA, {"hoy": hoy, "hora": hora})
    if filas is None:
        return None
    return {nombre for (nombre,) in filas}
