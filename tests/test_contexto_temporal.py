"""Test del contexto temporal inyectado al agente.

El modelo no sabe que dia es hoy: sin este bloque inventaba fechas de su
entrenamiento y las tools de agendado las rechazaban.
"""

import datetime
from zoneinfo import ZoneInfo

from sidhe_agent.config import get_settings
from sidhe_agent.graph.nodes import _bloques_system


def test_fecha_de_hoy_va_en_el_prompt():
    bloques = _bloques_system("PROMPT", {})
    dinamico = bloques[1]["text"]
    hoy = datetime.datetime.now(ZoneInfo(get_settings().tz)).date()
    assert "<contexto_temporal>" in dinamico
    assert hoy.isoformat() in dinamico


def test_el_contexto_temporal_no_invalida_el_cache():
    """El bloque cacheado debe quedar intacto; la fecha va en el dinamico."""
    bloques = _bloques_system("PROMPT", {"perfil": {"nombre": "Ana"}})
    assert bloques[0]["text"] == "PROMPT"
    assert bloques[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in bloques[1]
    assert "Ana" in bloques[1]["text"]
