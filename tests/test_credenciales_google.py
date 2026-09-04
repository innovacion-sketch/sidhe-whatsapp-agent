"""Test de las credenciales de Google: JSON directo o en base64."""

import base64
import json

from sidhe_agent.config import Settings

CREDENCIAL = json.dumps(
    {"type": "service_account", "project_id": "sidhe-bot", "private_key": "x"}
)


def test_json_en_una_linea():
    s = Settings(google_credentials_json=CREDENCIAL)
    assert json.loads(s.google_credentials)["project_id"] == "sidhe-bot"


def test_json_en_base64():
    """Los paneles rompen el JSON multilinea; base64 es el camino seguro."""
    b64 = base64.b64encode(CREDENCIAL.encode()).decode()
    s = Settings(google_credentials_json=b64)
    assert json.loads(s.google_credentials)["project_id"] == "sidhe-bot"


def test_base64_con_saltos_de_linea():
    """Un base64 pegado con saltos (como lo formatea algun panel) tambien."""
    b64 = base64.b64encode(CREDENCIAL.encode()).decode()
    s = Settings(google_credentials_json="  " + b64 + "  ")
    assert json.loads(s.google_credentials)["project_id"] == "sidhe-bot"


def test_vacio_desactiva_la_sincronizacion():
    assert Settings().google_credentials == ""
