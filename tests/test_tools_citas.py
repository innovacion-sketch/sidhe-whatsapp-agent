"""Tests de las tools de sucursales y citas contra Postgres real.

Requieren un Postgres con pgvector en DATABASE_URL (docker compose up -d db);
si no hay conexión, se saltan. Crean y destruyen su propio esquema en la base
sidhe_test. El caso clave: dos transacciones concurrentes sobre un slot con
capacidad 1 — una gana, la otra recibe slot_no_disponible con alternativas.
"""

import asyncio
import datetime

import pytest

from sidhe_agent.db import session as db_session
from sidhe_agent.db.models import Slot, Sucursal
from sidhe_agent.tools.citas import (
    _agendar_cita,
    _cancelar_cita,
    _consultar_disponibilidad,
    _consultar_mis_citas,
    fecha_legible,
)
from sidhe_agent.tools.sucursales import _buscar_sucursal, _listar_zonas

TEL_ANA = "+5215511111111"
TEL_BETO = "+5215522222222"

MANANA = datetime.date.today() + datetime.timedelta(days=1)
PASADO = datetime.date.today() + datetime.timedelta(days=2)


@pytest.fixture
async def datos(db):
    """Dos sucursales; Perisur con 3 slots (uno de capacidad 1 mañana)."""
    async with db_session.get_session() as session:
        perisur = Sucursal(
            nombre="Liverpool Perisur",
            alias=["perisur", "peri sur"],
            ciudad="Ciudad de México",
            estado="Ciudad de México",
            zona="CDMX Sur",
            direccion="Anillo Periférico 4690, Coyoacán",
            horario_apertura=datetime.time(11, 0),
            horario_cierre=datetime.time(20, 0),
            dias_operacion=["lunes", "martes", "miercoles", "jueves", "viernes"],
            activa=True,
        )
        gdl = Sucursal(
            nombre="Liverpool Galerías Guadalajara",
            alias=["galerias gdl"],
            ciudad="Zapopan",
            estado="Jalisco",
            zona="Guadalajara",
            direccion="Av. Rafael Sanzio 150, Zapopan",
            horario_apertura=datetime.time(11, 0),
            horario_cierre=datetime.time(20, 0),
            dias_operacion=["lunes"],
            activa=True,
        )
        session.add_all([perisur, gdl])
        await session.flush()
        slots = [
            Slot(
                sucursal_id=perisur.id,
                fecha=MANANA,
                hora_inicio=datetime.time(11, 0),
                hora_fin=datetime.time(12, 0),
                capacidad=1,
            ),
            Slot(
                sucursal_id=perisur.id,
                fecha=MANANA,
                hora_inicio=datetime.time(12, 0),
                hora_fin=datetime.time(13, 0),
                capacidad=1,
            ),
            Slot(
                sucursal_id=perisur.id,
                fecha=PASADO,
                hora_inicio=datetime.time(11, 0),
                hora_fin=datetime.time(12, 0),
                capacidad=2,
            ),
        ]
        session.add_all(slots)
        await session.commit()
        yield {
            "perisur_id": perisur.id,
            "slot_11": slots[0].id,
            "slot_12": slots[1].id,
            "slot_pasado": slots[2].id,
        }


async def test_buscar_sucursal_sin_acentos(datos):
    resultados = await _buscar_sucursal("perisúr")
    assert len(resultados) == 1
    assert resultados[0]["nombre"] == "Liverpool Perisur"
    assert resultados[0]["id"] == datos["perisur_id"]


async def test_buscar_sucursal_por_ciudad_y_alias(datos):
    assert (await _buscar_sucursal("guadalajara"))[0]["zona"] == "Guadalajara"
    assert (await _buscar_sucursal("galerias gdl"))[0]["ciudad"] == "Zapopan"
    assert await _buscar_sucursal("cancún") == []


async def test_listar_zonas(datos):
    zonas = await _listar_zonas()
    assert {"zona": "CDMX Sur", "sucursales": 1} in zonas
    assert {"zona": "Guadalajara", "sucursales": 1} in zonas


async def test_disponibilidad_rango_devuelve_fechas(datos):
    """Rango de varios dias: agrega por fecha para el primer list-picker."""
    hoy = datetime.date.today().isoformat()
    fin = (datetime.date.today() + datetime.timedelta(days=13)).isoformat()
    resultado = await _consultar_disponibilidad(datos["perisur_id"], hoy, fin)

    assert resultado["tipo"] == "fechas"
    fechas = {f["fecha"]: f["horarios_disponibles"] for f in resultado["fechas"]}
    assert fechas == {MANANA.isoformat(): 2, PASADO.isoformat(): 1}
    assert resultado["fechas"][0]["fecha_legible"] == fecha_legible(MANANA)


async def test_disponibilidad_un_dia_devuelve_horarios(datos):
    """Mismo dia en inicio y fin: horarios con slot_id para el segundo picker."""
    resultado = await _consultar_disponibilidad(
        datos["perisur_id"], MANANA.isoformat(), MANANA.isoformat()
    )
    assert resultado["tipo"] == "horarios"
    assert [h["slot_id"] for h in resultado["horarios"]] == [
        datos["slot_11"], datos["slot_12"],
    ]
    assert resultado["horarios"][0]["hora_inicio"] == "11:00"


async def test_disponibilidad_excluye_slots_llenos(datos):
    await _agendar_cita(datos["slot_11"], "Ana", TEL_ANA, "whatsapp")
    resultado = await _consultar_disponibilidad(
        datos["perisur_id"], MANANA.isoformat(), MANANA.isoformat()
    )
    assert [h["slot_id"] for h in resultado["horarios"]] == [datos["slot_12"]]


async def test_disponibilidad_fechas_invalidas(datos):
    resultado = await _consultar_disponibilidad(datos["perisur_id"], "20/07", "21/07")
    assert resultado["error"] == "fechas_invalidas"


async def test_agendar_feliz(datos):
    resultado = await _agendar_cita(datos["slot_11"], "Ana López", TEL_ANA, "whatsapp")
    assert resultado["folio"] > 0
    assert resultado["sucursal"] == "Liverpool Perisur"
    assert resultado["direccion"].startswith("Anillo Periférico")
    assert resultado["fecha"] == MANANA.isoformat()
    assert resultado["hora"] == "11:00"


async def test_slot_lleno_concurrente(datos):
    """Dos clientes intentan el mismo slot (capacidad 1) a la vez:
    exactamente uno gana; el otro recibe alternativas, nunca una excepción."""
    r1, r2 = await asyncio.gather(
        _agendar_cita(datos["slot_11"], "Ana", TEL_ANA, "whatsapp"),
        _agendar_cita(datos["slot_11"], "Beto", TEL_BETO, "whatsapp"),
    )
    exitos = [r for r in (r1, r2) if "folio" in r]
    fallos = [r for r in (r1, r2) if r.get("error") == "slot_no_disponible"]
    assert len(exitos) == 1 and len(fallos) == 1
    alternativas = fallos[0]["alternativas"]
    assert len(alternativas) >= 1
    assert datos["slot_11"] not in [a["slot_id"] for a in alternativas]


async def test_doble_reserva_mismo_cliente(datos):
    slot = datos["slot_pasado"]  # capacidad 2
    assert "folio" in await _agendar_cita(slot, "Ana", TEL_ANA, "whatsapp")
    repetida = await _agendar_cita(slot, "Ana", TEL_ANA, "whatsapp")
    assert repetida["error"] == "ya_tienes_cita_en_ese_horario"


async def test_mis_citas_solo_del_telefono(datos):
    await _agendar_cita(datos["slot_11"], "Ana", TEL_ANA, "whatsapp")
    await _agendar_cita(datos["slot_12"], "Beto", TEL_BETO, "whatsapp")
    citas_ana = await _consultar_mis_citas(TEL_ANA)
    assert len(citas_ana) == 1
    assert citas_ana[0]["hora"] == "11:00"


async def test_cancelar_libera_cupo(datos):
    cita = await _agendar_cita(datos["slot_11"], "Ana", TEL_ANA, "whatsapp")
    # Beto no puede cancelar la cita de Ana
    ajena = await _cancelar_cita(cita["folio"], TEL_BETO)
    assert ajena["error"] == "cita_no_encontrada"

    ok = await _cancelar_cita(cita["folio"], TEL_ANA)
    assert ok == {"ok": True, "folio": cita["folio"]}
    # El slot vuelve a tener cupo y Beto puede tomarlo
    assert "folio" in await _agendar_cita(datos["slot_11"], "Beto", TEL_BETO, "whatsapp")
    # Cancelar dos veces no procede
    repetido = await _cancelar_cita(cita["folio"], TEL_ANA)
    assert repetido["error"] == "cita_no_cancelable"
