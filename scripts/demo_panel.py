"""Sirve el panel con datos de ejemplo para revisarlo sin base de datos.

Solo para desarrollo: no toca Postgres ni el agente. Incluye una conversación
en cada estado de la bandeja (esperando asesor, atendida, cerrada, con el
bot) y respuestas rápidas en memoria que se pueden crear, editar y borrar.

Uso: uv run python scripts/demo_panel.py   (clave: cualquiera)
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "alembic" / "versions"))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

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
         "fecha": "2026-09-15", "hora": "11:00", "canal": "whatsapp"},
        {"folio": 169, "cliente": "Beto Ramirez", "sucursal": "Liverpool Polanco",
         "fecha": "2026-09-15", "hora": "13:00", "canal": "instagram"},
    ],
    "primera_respuesta_seg": 4.6,
    "conversion": 40.8,
}


def hace(minutos: int) -> str:
    momento = datetime.datetime.now() - datetime.timedelta(minutes=minutos)
    return momento.isoformat(timespec="seconds")


# Una conversación por estado, con su historial
CONVERSACIONES = {
    "+5215512232247": {
        "canal": "whatsapp", "estado": "esperando_asesor", "esperando_desde": hace(25),
        "mensajes": [
            ("in", "texto", "Hola, compré plantillas en Perisur hace 3 semanas", 30),
            ("out", "texto", "¡Hola! Déjame buscar tu pedido.", 29),
            ("in", "texto", "Me lastiman del lado derecho, ¿me las pueden ajustar?", 27),
            ("out", "texto", "Entiendo. Te comunico con un asesor para revisar el ajuste; te contactará por este mismo chat.", 25),
        ],
    },
    "ig:andres.bautista": {
        "canal": "instagram", "estado": "esperando_asesor", "esperando_desde": hace(140),
        "mensajes": [
            ("in", "texto", "¿Tienen convenio con mi empresa?", 150),
            ("out", "texto", "Te comunico con un asesor para revisarlo.", 140),
        ],
    },
    "+5215642934582": {
        "canal": "whatsapp", "estado": "atendida", "esperando_desde": None,
        "mensajes": [
            ("in", "texto", "¿Ya están mis plantillas?", 90),
            ("out", "texto", "Tu pedido necesita revisión, te paso con un asesor.", 89),
            ("out", "humano", "Hola, soy Laura. Ya revisé: llegan a Polanco el jueves.", 60),
        ],
    },
    "+5215533114455": {
        "canal": "whatsapp", "estado": "cerrada", "esperando_desde": None,
        "mensajes": [
            ("in", "texto", "¿Cómo se limpian?", 300),
            ("out", "humano", "Se limpian con un paño húmedo y jabón suave.", 290),
            ("in", "texto", "¡Gracias!", 288),
        ],
    },
    "+5215599887766": {
        "canal": "whatsapp", "estado": "bot", "esperando_desde": None,
        "mensajes": [
            ("in", "texto", "Quiero agendar", 12),
            ("out", "interactivo", "¿En qué ciudad, zona o plaza te queda más cerca?", 12),
            ("in", "seleccion_interactiva", "Polanco", 11),
        ],
    },
}


def _resumen(user_id: str, datos: dict) -> dict:
    ultimo = datos["mensajes"][-1]
    return {
        "canal": datos["canal"], "user_id": user_id,
        "ultimo_mensaje": ultimo[2], "ultima_direccion": ultimo[0],
        "ultima_fecha": hace(ultimo[3]), "mensajes": len(datos["mensajes"]),
        "estado": datos["estado"], "esperando_desde": datos["esperando_desde"],
        "bot_pausado": datos["estado"] in ("esperando_asesor", "atendida"),
    }


app = FastAPI()


@app.get("/panel", response_class=HTMLResponse)
async def panel() -> HTMLResponse:
    return HTMLResponse(RUTA_PANEL.read_text(encoding="utf-8"))


@app.get("/internal/metricas")
async def met(dias: int = 30) -> dict:
    return {**EJEMPLO, "dias": dias}


@app.get("/internal/conversaciones")
async def conversaciones(buscar: str = "", limite: int = 50, estado: str = "") -> dict:
    todas = [
        _resumen(u, d) for u, d in CONVERSACIONES.items()
        if buscar.lower() in u.lower()
    ]
    todas.sort(key=lambda c: c["ultima_fecha"], reverse=True)
    conteos = {e: 0 for e in ("esperando_asesor", "atendida", "cerrada", "bot")}
    for c in todas:
        conteos[c["estado"]] += 1
    conteos["todas"] = len(todas)
    visibles = [c for c in todas if not estado or c["estado"] == estado]
    return {"conversaciones": visibles[:limite], "conteos": conteos}


@app.get("/internal/conversaciones/{canal}/{user_id}")
async def conversacion(canal: str, user_id: str) -> dict:
    datos = CONVERSACIONES[user_id]
    return {
        **_resumen(user_id, datos),
        "ventana_abierta": True,
        "mensajes": [
            {"direccion": d, "tipo": t, "contenido": c, "fecha": hace(m)}
            for d, t, c, m in datos["mensajes"]
        ],
    }


class Respuesta(BaseModel):
    texto: str


@app.post("/internal/conversaciones/{canal}/{user_id}/responder")
async def responder(canal: str, user_id: str, datos: Respuesta) -> dict:
    conv = CONVERSACIONES[user_id]
    conv["mensajes"].append(("out", "humano", datos.texto, 0))
    conv["estado"], conv["esperando_desde"] = "atendida", None
    return {"ok": True, "bot_pausado": True}


@app.post("/internal/conversaciones/{canal}/{user_id}/cerrar")
async def cerrar(canal: str, user_id: str) -> dict:
    CONVERSACIONES[user_id]["estado"] = "cerrada"
    CONVERSACIONES[user_id]["esperando_desde"] = None
    return {"ok": True, "estado": "cerrada"}


@app.post("/internal/conversaciones/{canal}/{user_id}/devolver-al-bot")
async def devolver(canal: str, user_id: str) -> dict:
    CONVERSACIONES[user_id]["estado"] = "bot"
    CONVERSACIONES[user_id]["esperando_desde"] = None
    return {"ok": True}


# --- respuestas rápidas en memoria, con la misma semilla que la migración ---

from sidhe_agent.services.respuestas_rapidas import normalizar_atajo  # noqa: E402

import importlib.util  # noqa: E402

def _cargar_migracion(nombre: str):
    ruta = Path(__file__).parent.parent / "alembic" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# Mismo resultado que aplicar las migraciones 0004 y 0005 en orden
_m4 = _cargar_migracion("0004_bandeja_asesores.py")
_m5 = _cargar_migracion("0005_respuestas_del_equipo.py")
_semilla = [
    (_m5.RENOMBRADAS.get(a, a), t)
    for a, t in _m4.RESPUESTAS_INICIALES
    if a not in _m5.REEMPLAZADAS
] + list(_m5.RESPUESTAS_EQUIPO)

RESPUESTAS = {
    i + 1: {"id": i + 1, "atajo": a, "texto": t} for i, (a, t) in enumerate(_semilla)
}


class DatosRespuesta(BaseModel):
    atajo: str
    texto: str


def _validar(datos: DatosRespuesta, propio: int | None = None) -> tuple[str, str]:
    atajo, texto = normalizar_atajo(datos.atajo), datos.texto.strip()
    if not atajo:
        raise HTTPException(400, "El atajo no puede quedar vacío (usa letras o números).")
    if not texto:
        raise HTTPException(400, "El mensaje no puede quedar vacío.")
    if any(r["atajo"] == atajo and r["id"] != propio for r in RESPUESTAS.values()):
        raise HTTPException(409, f"Ya existe una respuesta con el atajo /{atajo}.")
    return atajo, texto


@app.get("/internal/respuestas-rapidas")
async def listar_respuestas() -> dict:
    ordenadas = sorted(RESPUESTAS.values(), key=lambda r: r["atajo"])
    return {"respuestas": ordenadas, "maximo": 100}


@app.post("/internal/respuestas-rapidas", status_code=201)
async def crear_respuesta(datos: DatosRespuesta) -> dict:
    atajo, texto = _validar(datos)
    nuevo_id = max(RESPUESTAS, default=0) + 1
    RESPUESTAS[nuevo_id] = {"id": nuevo_id, "atajo": atajo, "texto": texto}
    return RESPUESTAS[nuevo_id]


@app.put("/internal/respuestas-rapidas/{respuesta_id}")
async def editar_respuesta(respuesta_id: int, datos: DatosRespuesta) -> dict:
    if respuesta_id not in RESPUESTAS:
        raise HTTPException(404, "Esa respuesta ya no existe.")
    atajo, texto = _validar(datos, respuesta_id)
    RESPUESTAS[respuesta_id].update(atajo=atajo, texto=texto)
    return RESPUESTAS[respuesta_id]


@app.delete("/internal/respuestas-rapidas/{respuesta_id}")
async def borrar_respuesta(respuesta_id: int) -> dict:
    if RESPUESTAS.pop(respuesta_id, None) is None:
        raise HTTPException(404, "Esa respuesta ya no existe.")
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8123)
