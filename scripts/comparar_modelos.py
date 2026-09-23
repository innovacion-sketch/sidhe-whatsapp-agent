"""Compara dos modelos con preguntas REALES de tus clientes.

Responde la pregunta cara: ¿si cambio a un modelo más barato, contesta peor?

Cómo funciona, y por qué así:

- Los casos salen de la tabla `mensajes`: mensajes que de verdad te
  escribieron. Nunca salen del servidor — el script los lee, los usa y no
  los guarda en el repositorio. Los teléfonos no se tocan: solo se lee el
  texto del mensaje.
- Se arma el MISMO prompt y las MISMAS herramientas que usa el bot en
  producción (se importan de su código, no se copian), pero **las
  herramientas no se ejecutan**: se mira cuál habría llamado y con qué
  argumentos. Así la prueba no agenda citas de mentira ni escribe nada.
- Se califica con reglas deterministas, no con opiniones:
    inventa       ¿dijo un precio que no está en las FAQs? ¿datos bancarios?
    prohibida     ¿usó la palabra "confort"?
    herramienta   ¿llamó a la que correspondía para ese tipo de pregunta?
    largo         ¿la respuesta cabe en WhatsApp?

Uso:

    python scripts/comparar_modelos.py --solo-casos      # sin gastar nada
    python scripts/comparar_modelos.py                   # corre la comparación
    python scripts/comparar_modelos.py --modelos claude-sonnet-5,claude-haiku-4-5
"""

import argparse
import asyncio
import datetime
import json
import re
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from langchain_anthropic import ChatAnthropic  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from sidhe_agent.config import get_settings  # noqa: E402
from sidhe_agent.db.models import Mensaje  # noqa: E402
from sidhe_agent.db.session import dispose_engine, get_session  # noqa: E402
from sidhe_agent.graph.builder import TOOLS  # noqa: E402
from sidhe_agent.graph.nodes import _bloques_system  # noqa: E402
from sidhe_agent.services.cache_respuestas import (  # noqa: E402
    clave,
    es_pregunta_generica,
)
from sidhe_agent.services.cortesias import es_acuse  # noqa: E402

SALIDA = RAIZ / ".claude" / "hillclimb" / "respuesta-whatsapp"
RUTA_PROMPT = RAIZ / "src" / "sidhe_agent" / "graph" / "prompts" / "system.md"
RUTA_FAQS = RAIZ / "data" / "faqs.json"

MAX_CARACTERES_WHATSAPP = 1200

# Qué herramienta debería usar cada tipo de pregunta. None = no exigimos
# ninguna en particular (saludos, dudas generales que salen de las FAQs).
CATEGORIAS = [
    # El orden importa: gana el primero que coincida. Precio antes que cita,
    # porque "cuanto cuesta la valoracion" es una pregunta de precio.
    ("precio",
     r"\b(precio|costo|cu[a\u00e1]nto[s]? (cuesta|cuestan|sale|salen|vale|valen))\b",
     None),
    ("pedido",
     r"\b(plantilla|pedido|orden|estudio)\w*\b.{0,40}\b(list|lleg|tard|entreg|recog)",
     "consultar_estado_pedido"),
    ("pedido",
     r"\b(ya (estan|est\u00e1n|quedaron|salieron)|mi pedido|mis plantillas)\b",
     "consultar_estado_pedido"),
    # Cancelar va antes que cita: "cancelar mi cita" es lo m\u00e1s espec\u00edfico
    ("cancelar", r"\b(cancel|reagend|cambiar la cita)\w*", None),
    ("cita", r"\b(agendar|agenda|cita|estudio de pisada|valoraci[o\u00f3]n)\b", None),
    ("sucursal",
     r"\b(sucursal|sucursales|direcci[o\u00f3]n|ubicaci[o\u00f3]n|"
     r"d[o\u00f3]nde (est[a\u00e1]n|se ubican?|se encuentran?|los encuentro|hay))\b",
     "buscar_sucursal"),
    ("horario", r"\b(horario|a qu[e\u00e9] hora|abren|cierran)\b", None),
]

# Cuántos casos de cada tipo. Muestrear por categoría y no por frecuencia:
# si no, el top lo acaparan los saludos y no se prueba nada interesante.
CUOTAS = {"pedido": 6, "cita": 6, "precio": 5, "sucursal": 4, "horario": 3,
          "cancelar": 2, "otro": 4}

PROHIBIDAS = ("confort",)
BANCARIOS = re.compile(r"\b\d{16,18}\b")
PRECIOS = re.compile(r"\$\s?([\d,]{3,})")


def categoria(texto: str) -> tuple[str, str | None]:
    for nombre, patron, herramienta in CATEGORIAS:
        if re.search(patron, texto, re.IGNORECASE):
            return nombre, herramienta
    return "otro", None


def precios_permitidos() -> set[str]:
    """Las cifras que SÍ existen en las FAQs; cualquier otra es inventada."""
    crudo = RUTA_FAQS.read_text(encoding="utf-8")
    return {c.replace(",", "") for c in PRECIOS.findall(crudo)}


async def cargar_casos(cuantos: int, dias: int) -> list[dict]:
    """Mensajes reales, sin repetir, de los últimos `dias` días."""
    desde = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=dias)
    async with get_session() as session:
        filas = (
            await session.execute(
                select(Mensaje.contenido, func.count(Mensaje.id).label("veces"))
                .where(
                    Mensaje.direccion == "in",
                    Mensaje.tipo == "texto",
                    Mensaje.creado_en >= desde,
                    func.length(Mensaje.contenido) >= 10,
                    func.length(Mensaje.contenido) <= 300,
                )
                .group_by(Mensaje.contenido)
                .order_by(func.count(Mensaje.id).desc())
            )
        ).all()

    casos: list[dict] = []
    vistos: set[str] = set()
    por_categoria: dict[str, int] = {}
    for texto, veces in filas:
        limpio = " ".join(texto.split())
        # Misma clave que el caché: sin acentos ni signos, así "Dónde se
        # ubican?" y "donde se ubican" no ocupan dos lugares.
        firma = clave(limpio)
        if not firma or firma in vistos or es_acuse(limpio):
            continue
        if not es_pregunta_generica(limpio) and len(firma.split()) < 4:
            # Fragmentos sueltos ("Guadalajara", "Si por favor"): fuera de su
            # conversación no se pueden evaluar.
            continue
        cat, herramienta = categoria(limpio)
        if por_categoria.get(cat, 0) >= CUOTAS.get(cat, 0):
            continue
        vistos.add(firma)
        por_categoria[cat] = por_categoria.get(cat, 0) + 1
        casos.append(
            {
                "prompt_id": f"caso_{len(casos) + 1:03d}",
                "prompt": limpio,
                "veces": veces,
                "tags": [cat],
                "expected": {"herramienta": herramienta},
            }
        )
        if len(casos) >= cuantos:
            break
    return casos


def calificar(texto: str, tool_calls: list, esperada: str | None, validos: set) -> dict:
    inventados = {p.replace(",", "") for p in PRECIOS.findall(texto)} - validos
    llamadas = [t.get("name") for t in tool_calls or []]
    notas = {
        "no_inventa": 0.0 if inventados or BANCARIOS.search(texto) else 1.0,
        "sin_prohibidas": 0.0 if any(p in texto.lower() for p in PROHIBIDAS) else 1.0,
        "largo_ok": 1.0 if len(texto) <= MAX_CARACTERES_WHATSAPP else 0.0,
    }
    if esperada:
        notas["herramienta"] = 1.0 if esperada in llamadas else 0.0
    return notas, sorted(inventados), llamadas


async def correr(modelo: str, casos: list[dict], validos: set, prompt: str) -> list[dict]:
    llm = ChatAnthropic(
        model=modelo,
        api_key=get_settings().anthropic_api_key,
        max_tokens=1024,
        thinking={"type": "disabled"},
    ).bind_tools(TOOLS)
    system = SystemMessage(content=_bloques_system(prompt, {}))

    limite = asyncio.Semaphore(4)
    resultados: list[dict] = []

    async def uno(caso: dict) -> None:
        async with limite:
            inicio = time.monotonic()
            try:
                respuesta = await llm.ainvoke([system, HumanMessage(content=caso["prompt"])])
            except Exception as exc:
                resultados.append({**caso, "status": "error", "detalle": str(exc)[:200]})
                return
            texto = respuesta.content
            if not isinstance(texto, str):
                texto = " ".join(
                    b.get("text", "") for b in texto if isinstance(b, dict)
                )
            notas, inventados, llamadas = calificar(
                texto, respuesta.tool_calls, caso["expected"]["herramienta"], validos
            )
            uso = respuesta.usage_metadata or {}
            resultados.append(
                {
                    **caso,
                    "status": "ok",
                    "model": (respuesta.response_metadata or {}).get("model", modelo),
                    "respuesta": texto,
                    "tool_calls": llamadas,
                    "precios_inventados": inventados,
                    "grade": notas,
                    "latency_s": round(time.monotonic() - inicio, 2),
                    "usage": {
                        "input_tokens": uso.get("input_tokens"),
                        "output_tokens": uso.get("output_tokens"),
                    },
                }
            )

    await asyncio.gather(*(uno(c) for c in casos))
    return sorted(resultados, key=lambda r: r["prompt_id"])


def resumir(nombre: str, filas: list[dict]) -> dict:
    ok = [f for f in filas if f["status"] == "ok"]
    métricas: dict[str, list[float]] = {}
    for f in ok:
        for k, v in f["grade"].items():
            métricas.setdefault(k, []).append(v)
    return {
        "modelo": nombre,
        "casos": len(ok),
        "errores": len(filas) - len(ok),
        **{k: sum(v) / len(v) for k, v in métricas.items() if v},
        "salida_media": (
            sum(f["usage"]["output_tokens"] or 0 for f in ok) / len(ok) if ok else 0
        ),
        "segundos_medio": (sum(f["latency_s"] for f in ok) / len(ok) if ok else 0),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Compara modelos con casos reales")
    parser.add_argument("--casos", type=int, default=30)
    parser.add_argument("--dias", type=int, default=30)
    parser.add_argument("--modelos", default="claude-sonnet-5,claude-haiku-4-5")
    parser.add_argument("--solo-casos", action="store_true", help="no llama a la API")
    args = parser.parse_args()

    casos = await cargar_casos(args.casos, args.dias)
    await dispose_engine()
    if not casos:
        print("No encontré mensajes que sirvan como casos.")
        return

    if args.solo_casos:
        print(f"{len(casos)} casos (mensajes reales, sin repetir):\n")
        for c in casos:
            esperada = c["expected"]["herramienta"] or "—"
            print(f"  {c['prompt_id']}  [{c['tags'][0]:9}] x{c['veces']:<3} {c['prompt']}")
            print(f"{'':>34}espera: {esperada}")
        print("\nRevísalos antes de correr la comparación. Si hay casos que no")
        print("representan a tus clientes, dímelo y ajusto el filtro.")
        return

    validos = precios_permitidos()
    prompt = RUTA_PROMPT.read_text(encoding="utf-8")
    SALIDA.mkdir(parents=True, exist_ok=True)

    resumenes = []
    por_modelo = {}
    for modelo in [m.strip() for m in args.modelos.split(",") if m.strip()]:
        print(f"Corriendo {len(casos)} casos en {modelo}...")
        filas = await correr(modelo, casos, validos, prompt)
        por_modelo[modelo] = {f["prompt_id"]: f for f in filas}
        resumenes.append(resumir(modelo, filas))
        (SALIDA / f"{modelo}.jsonl").write_text(
            "\n".join(json.dumps(f, ensure_ascii=False) for f in filas),
            encoding="utf-8",
        )

    print("\n=== RESUMEN (1.0 = perfecto) ===")
    claves = [k for k in resumenes[0] if k not in ("modelo",)]
    print(f"{'modelo':22}" + "".join(f"{k:>16}" for k in claves))
    for r in resumenes:
        fila = f"{r['modelo']:22}"
        for k in claves:
            v = r.get(k, 0)
            fila += f"{v:>16.2f}" if isinstance(v, float) else f"{v:>16}"
        print(fila)

    modelos = list(por_modelo)
    if len(modelos) == 2:
        a, b = modelos
        print(f"\n=== DONDE DIFIEREN ({a} vs {b}) ===")
        diferencias = 0
        for cid in por_modelo[a]:
            ra, rb = por_modelo[a][cid], por_modelo[b].get(cid, {})
            if ra.get("grade") != rb.get("grade"):
                diferencias += 1
                print(f"\n  {cid} [{ra['tags'][0]}] {ra['prompt']}")
                print(f"    {a:20} {ra.get('grade')} tools={ra.get('tool_calls')}")
                print(f"    {b:20} {rb.get('grade')} tools={rb.get('tool_calls')}")
        if not diferencias:
            print("  Ninguna: calificaron igual en los", len(casos), "casos.")

    print(f"\nDetalle completo en {SALIDA}")


if __name__ == "__main__":
    asyncio.run(main())
