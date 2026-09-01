"""FastAPI app: webhook de Twilio WhatsApp, health check y API interna.

Patrón del webhook: validar firma → idempotencia por MessageSid → responder
200 de inmediato → procesar en background task → responder al cliente vía la
API REST de Twilio (no TwiML).
"""

import datetime
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import select, text

from .channels.schemas import IncomingMessage, OutgoingMessage, UIElement
from .channels.whatsapp_twilio import WhatsAppTwilioAdapter, validar_firma
from .config import get_settings
from .db.models import Cita, Escalamiento, Mensaje, Slot, Sucursal
from .db.session import dispose_engine, get_engine, get_session
from .graph.builder import build_graph
from .memory.long_term import crear_extractor
from .observability import configurar_logging, enmascarar_user_id
from .services.transcription import transcribir_audio
from .services.twilio_content import enviar_recordatorio
from .tools.citas import fecha_legible

logger = structlog.get_logger(__name__)

RUTA_SYSTEM_PROMPT = Path(__file__).parent / "graph" / "prompts" / "system.md"
LIMITE_PASOS_GRAFO = 30
NOTA_ESCALAMIENTO_RESUELTO = (
    "[nota del sistema] Un asesor humano ya atendió el escalamiento anterior "
    "y la conversación vuelve a estar a tu cargo. Retoma la atención con "
    "normalidad: si el cliente pide algo que puedes resolver (como agendar "
    "una cita), hazlo tú mismo y no digas que hay un asesor en camino."
)
MENSAJE_ATORADO = (
    "Perdón, me enredé buscando esa información. ¿Me dices de nuevo qué "
    "necesitas? Si prefieres, puedo pasarte con un asesor."
)
MENSAJE_ERROR_CLIENTE = (
    "Lo siento, tuve un problema técnico al procesar tu mensaje. "
    "¿Podrías intentarlo de nuevo en un momento?"
)


def _cargar_system_prompt() -> str:
    if not RUTA_SYSTEM_PROMPT.exists():
        raise RuntimeError(
            "Falta graph/prompts/system.md — ejecuta: "
            "uv run python scripts/build_system_prompt.py"
        )
    return RUTA_SYSTEM_PROMPT.read_text(encoding="utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configurar_logging(settings.log_level)

    app.state.stack = AsyncExitStack()
    saver = await app.state.stack.enter_async_context(
        AsyncPostgresSaver.from_conn_string(settings.psycopg_url)
    )
    await saver.setup()
    store = await app.state.stack.enter_async_context(
        AsyncPostgresStore.from_conn_string(settings.psycopg_url)
    )
    await store.setup()

    llm = ChatAnthropic(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        max_tokens=1024,
        temperature=0.3,
    )
    # LLM utilitario (extracción de perfil y resúmenes): determinista y barato
    llm_utilitario = ChatAnthropic(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        max_tokens=600,
        temperature=0.0,
    )
    app.state.graph = build_graph(
        llm,
        checkpointer=saver,
        store=store,
        system_prompt=_cargar_system_prompt(),
        extractor=crear_extractor(llm_utilitario),
        resumidor=llm_utilitario,
    )
    app.state.adapter = WhatsAppTwilioAdapter(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        from_number=settings.twilio_whatsapp_from,
    )
    logger.info("app_iniciada", modelo=settings.anthropic_model)
    yield
    await app.state.stack.aclose()
    await dispose_engine()


app = FastAPI(title="sidhe-whatsapp-agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        # Sin esto, un 503 no dice por qué falló la conexión de negocio.
        logger.exception("health_db_error", error=str(exc))
        raise HTTPException(status_code=503, detail="db_no_disponible") from exc
    return {"status": "ok"}


def _url_publica(request: Request) -> str:
    """URL sobre la que Twilio firmó el request (la pública, no la del proxy)."""
    base = get_settings().public_base_url.rstrip("/")
    if base:
        return f"{base}{request.url.path}"
    return str(request.url)


async def _mensaje_ya_procesado(message_sid: str) -> bool:
    async with get_session() as session:
        resultado = await session.execute(
            select(Mensaje.id).where(
                Mensaje.twilio_sid == message_sid, Mensaje.direccion == "in"
            )
        )
        return resultado.first() is not None


async def _guardar_mensaje(
    entrante_o_saliente: str,
    canal: str,
    user_id: str,
    tipo: str,
    contenido: str,
    item_id: str | None = None,
    twilio_sid: str | None = None,
) -> None:
    async with get_session() as session:
        session.add(
            Mensaje(
                canal=canal,
                user_id=user_id,
                direccion=entrante_o_saliente,
                tipo=tipo,
                contenido=contenido,
                item_id_seleccionado=item_id,
                twilio_sid=twilio_sid,
            )
        )
        await session.commit()


def _texto_de_respuesta(mensajes: list[Any]) -> str:
    for mensaje in reversed(mensajes):
        if isinstance(mensaje, AIMessage):
            contenido = mensaje.content
            if isinstance(contenido, str):
                return contenido
            partes = [
                bloque.get("text", "")
                for bloque in contenido
                if isinstance(bloque, dict) and bloque.get("type") == "text"
            ]
            return "\n".join(p for p in partes if p)
    return ""


def _contenido_para_grafo(entrante: IncomingMessage, transcripcion: str | None) -> str:
    if entrante.tipo == "audio":
        return f"[transcripción de nota de voz] {transcripcion}"
    if entrante.tipo == "seleccion_interactiva":
        # Dato exacto de la opción tocada: el modelo no interpreta texto libre.
        return (
            f"[selección interactiva] id={entrante.item_id} "
            f'etiqueta="{entrante.contenido}"'
        )
    return entrante.contenido


async def procesar_mensaje(app: FastAPI, entrante: IncomingMessage) -> None:
    log = logger.bind(
        user_id=enmascarar_user_id(entrante.user_id),
        canal=entrante.canal,
        tipo=entrante.tipo,
        request_id=str(uuid.uuid4()),
    )
    try:
        config = {
            "configurable": {"thread_id": f"{entrante.canal}:{entrante.user_id}"},
            # Corta bucles de tools antes de gastar tokens de mas.
            "recursion_limit": LIMITE_PASOS_GRAFO,
        }
        # Thread pausado por escalamiento: el bot guarda silencio; los mensajes
        # del cliente los atiende el asesor humano hasta que el escalamiento se
        # resuelva vía /internal/escalamientos/resolver.
        snapshot = await app.state.graph.aget_state(config)
        if any(t.interrupts for t in snapshot.tasks):
            log.info("thread_escalado_bot_en_silencio")
            return

        transcripcion = None
        if entrante.tipo == "audio":
            if not entrante.media_url:
                raise ValueError("mensaje de audio sin media_url")
            transcripcion = await transcribir_audio(
                entrante.media_url, entrante.media_content_type
            )
            log.info("audio_transcrito", caracteres=len(transcripcion))

        contenido = _contenido_para_grafo(entrante, transcripcion)
        resultado = await app.state.graph.ainvoke(
            {
                "messages": [HumanMessage(content=contenido)],
                "canal": entrante.canal,
                "user_id": entrante.user_id,
            },
            config,
        )
        texto = _texto_de_respuesta(resultado.get("messages", []))
        if not texto:
            if "__interrupt__" in resultado:
                # Escalado sin despedida del agente: silencio, no mensaje de error.
                log.info("thread_pausado_sin_texto")
                return
            log.warning("respuesta_vacia_del_agente")
            texto = MENSAJE_ERROR_CLIENTE

        ui_pendiente = resultado.get("ui_pendiente")
        ui = UIElement(**ui_pendiente) if ui_pendiente else None
        salida = OutgoingMessage(texto=texto, ui=ui)
        sid = await app.state.adapter.send(entrante.user_id, salida)
        await _guardar_mensaje(
            "out",
            entrante.canal,
            entrante.user_id,
            "interactivo" if ui else "texto",
            texto,
            twilio_sid=sid,
        )
        log.info("respuesta_enviada", twilio_sid=sid)
    except GraphRecursionError:
        # El agente se atoro en un bucle de tools: no dejar al cliente sin salida.
        log.exception("limite_de_pasos_agotado")
        try:
            await app.state.adapter.send(
                entrante.user_id, OutgoingMessage(texto=MENSAJE_ATORADO)
            )
        except Exception:
            log.exception("error_enviando_mensaje_de_atasco")
        return
    except Exception:
        log.exception("error_procesando_mensaje")
        try:
            await app.state.adapter.send(
                entrante.user_id, OutgoingMessage(texto=MENSAJE_ERROR_CLIENTE)
            )
        except Exception:
            log.exception("error_enviando_mensaje_de_error")


@app.post("/webhooks/twilio/whatsapp")
async def webhook_twilio_whatsapp(
    request: Request, background_tasks: BackgroundTasks
) -> Response:
    settings = get_settings()
    form = dict((await request.form()).items())

    if settings.twilio_validate_signature:
        firma = request.headers.get("X-Twilio-Signature", "")
        if not validar_firma(
            _url_publica(request), form, firma, settings.twilio_auth_token
        ):
            logger.warning("firma_twilio_invalida")
            raise HTTPException(status_code=403, detail="Firma de Twilio inválida")

    entrante = request.app.state.adapter.parse_incoming(form)

    # Twilio reintenta webhooks: idempotencia por MessageSid.
    if entrante.message_sid and await _mensaje_ya_procesado(entrante.message_sid):
        logger.info("webhook_duplicado_ignorado", twilio_sid=entrante.message_sid)
        return Response(content="<Response/>", media_type="application/xml")

    await _guardar_mensaje(
        "in",
        entrante.canal,
        entrante.user_id,
        entrante.tipo,
        entrante.contenido,
        item_id=entrante.item_id,
        twilio_sid=entrante.message_sid,
    )
    background_tasks.add_task(procesar_mensaje, request.app, entrante)
    return Response(content="<Response/>", media_type="application/xml")


def _validar_api_key_interna(x_api_key: str) -> None:
    settings = get_settings()
    if not settings.internal_api_key or x_api_key != settings.internal_api_key:
        raise HTTPException(status_code=401, detail="API key inválida")


class SolicitudRecordatorios(BaseModel):
    ventana_horas: int = 24


@app.post("/internal/recordatorios/enviar")
async def enviar_recordatorios(
    datos: SolicitudRecordatorios, x_api_key: str = Header(default="")
) -> dict[str, int]:
    """Envía el Content Template de recordatorio a las citas confirmadas de las
    próximas N horas. Idempotente: una cita ya recordada (fila en `mensajes`
    con tipo 'recordatorio') se omite; el cron de n8n puede reintentar.
    """
    _validar_api_key_interna(x_api_key)
    settings = get_settings()
    if not settings.twilio_recordatorio_content_sid:
        raise HTTPException(
            status_code=503,
            detail="Falta TWILIO_RECORDATORIO_CONTENT_SID "
            "(ejecuta scripts/setup_recordatorio_template.py)",
        )

    tz = ZoneInfo(settings.tz)
    ahora = datetime.datetime.now(tz)
    limite = ahora + datetime.timedelta(hours=datos.ventana_horas)

    async with get_session() as session:
        filas = (
            await session.execute(
                select(Cita, Slot, Sucursal)
                .join(Slot, Cita.slot_id == Slot.id)
                .join(Sucursal, Cita.sucursal_id == Sucursal.id)
                .where(
                    Cita.estado == "confirmada",
                    Slot.fecha >= ahora.date(),
                    Slot.fecha <= limite.date(),
                )
            )
        ).all()
        recordadas = {
            item_id
            for (item_id,) in (
                await session.execute(
                    select(Mensaje.item_id_seleccionado).where(
                        Mensaje.tipo == "recordatorio"
                    )
                )
            ).all()
        }

    enviados = omitidos = errores = 0
    for cita, slot, sucursal in filas:
        inicio = datetime.datetime.combine(slot.fecha, slot.hora_inicio, tzinfo=tz)
        if not (ahora <= inicio <= limite) or f"cita_{cita.id}" in recordadas:
            omitidos += 1
            continue
        try:
            sid = await enviar_recordatorio(
                app.state.adapter.client,
                settings.twilio_whatsapp_from,
                cita.cliente_telefono,
                settings.twilio_recordatorio_content_sid,
                {
                    "1": cita.cliente_nombre,
                    "2": sucursal.nombre,
                    "3": fecha_legible(slot.fecha),
                    "4": slot.hora_inicio.strftime("%H:%M"),
                },
            )
            await _guardar_mensaje(
                "out",
                cita.canal,
                cita.cliente_telefono,
                "recordatorio",
                f"recordatorio de cita {cita.id}",
                item_id=f"cita_{cita.id}",
                twilio_sid=sid,
            )
            enviados += 1
        except Exception:
            logger.exception("error_enviando_recordatorio", cita_id=cita.id)
            errores += 1

    logger.info(
        "recordatorios_procesados",
        enviados=enviados,
        omitidos=omitidos,
        errores=errores,
    )
    return {"enviados": enviados, "omitidos": omitidos, "errores": errores}


class ResolverEscalamiento(BaseModel):
    user_id: str
    canal: str = "whatsapp"


@app.post("/internal/escalamientos/resolver")
async def resolver_escalamiento(
    datos: ResolverEscalamiento, x_api_key: str = Header(default="")
) -> dict[str, Any]:
    """Marca los escalamientos pendientes del cliente como atendidos y reanuda
    su thread (el interrupt se resuelve y el bot vuelve a contestar)."""
    _validar_api_key_interna(x_api_key)

    async with get_session() as session:
        pendientes = (
            (
                await session.execute(
                    select(Escalamiento).where(
                        Escalamiento.user_id == datos.user_id,
                        Escalamiento.canal == datos.canal,
                        Escalamiento.estado == "pendiente",
                    )
                )
            )
            .scalars()
            .all()
        )
        for escalamiento in pendientes:
            escalamiento.estado = "atendido"
        await session.commit()

    config = {"configurable": {"thread_id": f"{datos.canal}:{datos.user_id}"}}
    snapshot = await app.state.graph.aget_state(config)
    reanudado = False
    if any(t.interrupts for t in snapshot.tasks):
        await app.state.graph.ainvoke(Command(resume="atendido"), config)
        reanudado = True

    # Sin esta nota el historial sigue diciendo "ya te escale" y el agente se
    # niega a retomar el caso. Se agrega al hilo SIN invocar el grafo, para no
    # enviarle un mensaje al cliente por nuestra cuenta.
    await app.state.graph.aupdate_state(
        config,
        {
            "messages": [HumanMessage(content=NOTA_ESCALAMIENTO_RESUELTO)],
            "escalado": False,
        },
    )

    return {
        "ok": True,
        "escalamientos_atendidos": len(pendientes),
        "thread_reanudado": reanudado,
    }
