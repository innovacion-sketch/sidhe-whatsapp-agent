"""Sirve el panel con metricas de ejemplo para revisarlo sin base de datos.

Solo para desarrollo: no toca Postgres ni el agente.

Uso: uv run python scripts/demo_panel.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

RUTA_PANEL = Path(__file__).parent.parent / "src" / "sidhe_agent" / "panel" / "index.html"

EJEMPLO = {
    "dias": 30,
    "desde": "2026-08-04",
    "resumen": {"clientes": 412, "mensajes": 5231, "entrantes": 2698,
                "salientes": 2533, "notas_de_voz": 96, "toques_de_boton": 1144},
    "citas": {"total": 168, "confirmadas": 155, "canceladas": 13,
              "en_calendario": 152},
    "escalamientos": {"total": 37, "pendientes": 6},
    "por_canal": [
        {"canal": "whatsapp", "mensajes": 4180, "clientes": 330},
        {"canal": "instagram", "mensajes": 704, "clientes": 58},
        {"canal": "facebook", "mensajes": 347, "clientes": 24},
    ],
    "serie_diaria": [
        {"dia": f"2026-08-{d:02d}", "mensajes": m, "clientes": m // 12}
        for d, m in zip(range(4, 32), [120, 145, 160, 132, 98, 76, 88, 190, 210,
                                        175, 160, 148, 96, 84, 205, 232, 198,
                                        176, 165, 102, 90, 221, 245, 210, 188,
                                        170, 110, 95])
    ],
    "top_sucursales": [
        {"sucursal": "Liverpool Perisur", "citas": 34},
        {"sucursal": "Liverpool Polanco", "citas": 28},
        {"sucursal": "Liverpool Satelite", "citas": 21},
        {"sucursal": "Liverpool Santa Fe", "citas": 18},
        {"sucursal": "Liverpool Andares Guadalajara", "citas": 14},
    ],
    "proximas_citas": [
        {"folio": 168, "cliente": "Ana Lopez", "sucursal": "Liverpool Perisur",
         "fecha": "2026-09-05", "hora": "11:00", "canal": "whatsapp"},
        {"folio": 169, "cliente": "Beto Ramirez", "sucursal": "Liverpool Polanco",
         "fecha": "2026-09-05", "hora": "13:00", "canal": "instagram"},
        {"folio": 170, "cliente": "Carla Diaz", "sucursal": "Liverpool Satelite",
         "fecha": "2026-09-06", "hora": "16:00", "canal": "whatsapp"},
    ],
    "primera_respuesta_seg": 4.6,
    "conversion": 40.8,
}

app = FastAPI()


@app.get("/panel", response_class=HTMLResponse)
async def panel() -> HTMLResponse:
    return HTMLResponse(RUTA_PANEL.read_text(encoding="utf-8"))


@app.get("/internal/metricas")
async def met(dias: int = 30) -> dict:
    return {**EJEMPLO, "dias": dias}


CONVERSACIONES = [
    {"canal": "whatsapp", "user_id": "+5215642934582",
     "ultimo_mensaje": "Confirmar", "ultima_direccion": "in",
     "ultima_fecha": "2026-09-04T17:42:00", "mensajes": 18, "bot_pausado": False},
    {"canal": "whatsapp", "user_id": "+5215512232247",
     "ultimo_mensaje": "Un asesor te contactara en breve",
     "ultima_direccion": "out", "ultima_fecha": "2026-09-04T16:10:00",
     "mensajes": 9, "bot_pausado": True},
    {"canal": "instagram", "user_id": "ig:andres.bautista",
     "ultimo_mensaje": "Informacion para comprar plantillas",
     "ultima_direccion": "in", "ultima_fecha": "2026-09-04T12:03:00",
     "mensajes": 4, "bot_pausado": False},
]

HISTORIAL = {
    "canal": "whatsapp", "user_id": "+5215642934582",
    "bot_pausado": False, "ventana_abierta": True,
    "mensajes": [
        {"direccion": "in", "tipo": "texto", "contenido": "Hola",
         "fecha": "2026-09-04T17:30:00"},
        {"direccion": "out", "tipo": "texto",
         "contenido": "Hola! Bienvenido a Sidhe Group. En que te ayudo?",
         "fecha": "2026-09-04T17:30:12"},
        {"direccion": "in", "tipo": "audio",
         "contenido": "[transcripcion] cuanto cuestan las plantillas",
         "fecha": "2026-09-04T17:31:00"},
        {"direccion": "out", "tipo": "texto",
         "contenido": "Estandar 2199, deportiva 2499, express 2899 y sandalias 3799.",
         "fecha": "2026-09-04T17:31:20"},
        {"direccion": "in", "tipo": "seleccion_interactiva",
         "contenido": "Confirmar", "fecha": "2026-09-04T17:42:00"},
        {"direccion": "out", "tipo": "humano",
         "contenido": "Te esperamos manana, cualquier duda aqui estoy.",
         "fecha": "2026-09-04T17:45:00"},
    ],
}


@app.get("/internal/conversaciones")
async def conversaciones(buscar: str = "", limite: int = 50) -> dict:
    datos = [c for c in CONVERSACIONES if buscar.lower() in c["user_id"].lower()]
    return {"conversaciones": datos}


@app.get("/internal/conversaciones/{canal}/{user_id}")
async def conversacion(canal: str, user_id: str) -> dict:
    return {**HISTORIAL, "canal": canal, "user_id": user_id}


@app.post("/internal/conversaciones/{canal}/{user_id}/responder")
async def responder(canal: str, user_id: str) -> dict:
    return {"ok": True, "bot_pausado": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8123)
