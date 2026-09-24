"""FastAPI app: webhook de Twilio WhatsApp, health check y API interna.

Patrón del webhook: validar firma → idempotencia por MessageSid → responder
200 de inmediato → procesar en background task → responder al cliente vía la
API REST de Twilio (no TwiML).
"""

import asyncio
import datetime
import json
import uuid
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import select, text

from .channels.schemas import IncomingMessage, OutgoingMessage, UIElement
from .channels import meta as canal_meta
from .channels.whatsapp_twilio import WhatsAppTwilioAdapter, validar_firma
from .config import get_settings
from .db.models import Cita, Escalamiento, Mensaje, Slot, Sucursal
from .db.session import dispose_engine, get_engine, get_session
from .graph.builder import build_graph
from .memory.long_term import crear_extractor
from .observability import configurar_logging, enmascarar_user_id
from .services import (
    agenda,
    alertas_citas,
    asistencias,
    cache_respuestas,
    consumo,
    conversaciones,
    cortesias,
    google_sheets,
    horario_asesores,
    metricas,
)
from .services import respuestas_rapidas
from .services.transcription import transcribir_audio
from .services.rafagas import Agrupador
from .services.twilio_content import enviar_recordatorio
from .tools.citas import fecha_legible

logger = structlog.get_logger(__name__)

RUTA_SYSTEM_PROMPT = Path(__file__).parent / "graph" / "prompts" / "system.md"
RUTA_PANEL = Path(__file__).parent / "panel" / "index.html"
MAX_DIAS_PANEL = 365
# Tope de pasos por mensaje. Cada paso es una llamada al modelo con todo el
# contexto: un agente en bucle es el gasto más caro y más invisible que hay.
LIMITE_PASOS_GRAFO = 16
NOTA_ESCALAMIENTO_RESUELTO = (
    "[nota del sistema] Un asesor humano ya atendió el escalamiento anterior "
    "y la conversación vuelve a estar a tu cargo. Retoma la atención con "
    "normalidad: si el cliente pide algo que puedes resolver (como agendar "
    "una cita), hazlo tú mismo y no digas que hay un asesor en camino."
)
NOTA_CONVERSACION_CERRADA = (
    "[nota del sistema] Un asesor marcó esta conversación como resuelta y "
    "vuelve a estar a tu cargo. Si el cliente escribe de nuevo, trátalo como "
    "una consulta nueva: salúdalo y pregúntale en qué le puedes ayudar, sin "
    "retomar el tema anterior a menos que él lo mencione."
)
NOTA_NADIE_ATENDIO = (
    "[nota del sistema] Escalaste esta conversación pero ningún asesor la "
    "atendió en varias horas y vuelve a estar a tu cargo. Discúlpate una vez "
    "por la demora, retoma la atención y resuelve lo que puedas tú mismo. "
    "Si el tema necesita sí o sí a una persona, dale el teléfono de su "
    "sucursal (buscar_sucursal) para que la contacte directo, en vez de "
    "volver a dejarlo esperando."
)
MENSAJE_ATORADO = (
    "Perdón, me enredé buscando esa información. ¿Me dices de nuevo qué "
    "necesitas? Si prefieres, puedo pasarte con un asesor."
)
MENSAJE_ERROR_CLIENTE = (
    "Lo siento, tuve un problema técnico al procesar tu mensaje. "
    "¿Podrías intentarlo de nuevo en un momento?"
)
# Para lo que llegue vacío pese a todo (un tipo de mensaje que ningún canal
# describe todavía). Mandarlo al modelo es un error seguro de la API y el
# cliente acababa leyendo "problema técnico" por haber mandado una foto.
MENSAJE_VACIO = "No alcancé a ver tu mensaje. ¿Me lo escribes, por favor?"


def _cargar_system_prompt() -> str:
    if not RUTA_SYSTEM_PROMPT.exists():
        raise RuntimeError(
            "Falta graph/prompts/system.md — ejecuta: "
            "uv run python scripts/build_system_prompt.py"
        )
    return RUTA_SYSTEM_PROMPT.read_text(encoding="utf-8")


def crear_llm(settings: Any, modelo: str) -> ChatAnthropic:
    """El modelo que atiende al cliente.

    Sin `temperature`: los modelos de Anthropic 4.6 en adelante la rechazan
    con error 400. Sin razonamiento extendido: en Sonnet 5 viene encendido
    por defecto y esas respuestas se cobran como salida, cuando hoy el bot
    contesta bien sin él. Si algún día se quiere, se cambia aquí.

    Sin punto de corte de caché en el historial, aunque la API lo permita:
    el bloque dinámico del system prompt lleva la hora con minutos, así que
    todo lo que va después cambia cada minuto y un corte ahí escribiría un
    caché que nunca se lee. Primero hay que sacar el reloj de ahí; mientras,
    el único corte vive en el system prompt (graph/nodes.py).
    """
    return ChatAnthropic(
        model=modelo,
        api_key=settings.anthropic_api_key,
        max_tokens=1024,
        thinking={"type": "disabled"},
        callbacks=[consumo.CONTADOR],
    )


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

    llm = crear_llm(settings, settings.anthropic_model)
    # Modelo aparte para el flujo de citas (ver es_conversacion_de_cita)
    llm_agenda = (
        crear_llm(settings, settings.anthropic_model_agenda)
        if settings.anthropic_model_agenda
        and settings.anthropic_model_agenda != settings.anthropic_model
        else None
    )
    # LLM utilitario (extracción de perfil y resúmenes): determinista y barato
    llm_utilitario = ChatAnthropic(
        model=settings.anthropic_model_utilitario or settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        max_tokens=600,
        temperature=0.0,
        callbacks=[consumo.CONTADOR],
    )
    system_prompt = _cargar_system_prompt()
    # La huella del prompt invalida sola el caché de respuestas cuando cambia
    # un precio o un horario en las FAQs.
    cache_respuestas.fijar_prompt(system_prompt)
    app.state.graph = build_graph(
        llm,
        checkpointer=saver,
        store=store,
        system_prompt=system_prompt,
        extractor=crear_extractor(llm_utilitario),
        resumidor=llm_utilitario,
        llm_agenda=llm_agenda,
    )
    app.state.adapter = WhatsAppTwilioAdapter(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        from_number=settings.twilio_whatsapp_from,
        resolver_remitente=_numero_al_que_escribio,
    )
    # Un adaptador por canal. Instagram y Messenger solo aparecen si tienen
    # token: sin el tramite de Meta terminado, esos canales no existen.
    app.state.adapters = {app.state.adapter.canal: app.state.adapter}
    app.state.adapters.update(
        canal_meta.adaptadores_configurados(
            settings.meta_token_instagram, settings.meta_token_messenger
        )
    )
    logger.info("canales_activos", canales=sorted(app.state.adapters))
    # Trampa fácil: el DSN completo gana sobre los datos sueltos, así que si
    # quedaron los dos puestos, los ASISTENCIAS_DB_* se ignoran en silencio.
    if settings.asistencias_database_url and settings.asistencias_db_host:
        logger.warning(
            "asistencias_configurada_dos_veces",
            usando="ASISTENCIAS_DATABASE_URL",
            ignorando="ASISTENCIAS_DB_HOST y demas",
            que_hacer="borra una de las dos formas",
        )
    # La hoja de pedidos se relee sola: sin esto habria que empujarla desde
    # n8n y una sincronizacion olvidada es un cliente al que le decimos que
    # sus plantillas siguen en fabricacion cuando ya estan en la sucursal.
    app.state.sincronizador = asyncio.create_task(
        google_sheets.sincronizar_periodicamente(
            settings.pedidos_sincronizar_cada_horas
        )
    )
    # Igual con la agenda: los horarios son filas generadas por adelantado y
    # sin relleno se acaban solos (el bot llego a ofrecer solo dos dias).
    # Citas de hoy en una sucursal donde nadie abrio: correo a los mismos
    # destinatarios que ya usa el sistema de asistencias.
    app.state.vigilancia_citas = asyncio.create_task(alertas_citas.vigilar())
    app.state.agenda = asyncio.create_task(
        agenda.mantener_agenda_abierta(
            settings.agenda_dias_adelante, settings.agenda_minutos_por_cita
        )
    )

    logger.info(
        "app_iniciada",
        modelo=settings.anthropic_model,
        modelo_agenda=settings.anthropic_model_agenda,
        modelo_interno=settings.anthropic_model_utilitario,
    )
    yield

    for tarea in (
        app.state.sincronizador,
        app.state.agenda,
        app.state.vigilancia_citas,
    ):
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea
    await app.state.stack.aclose()
    await asistencias.cerrar_motor()
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
    numero_negocio: str | None = None,
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
                numero_negocio=numero_negocio,
            )
        )
        await session.commit()


async def _numero_al_que_escribio(user_id: str) -> str | None:
    """Número del negocio al que este cliente escribió por última vez.

    Con el 5164 y el 0202 activos, la respuesta tiene que salir del mismo
    número: si no, al cliente le llega de un contacto que no tiene guardado.
    None si nunca escribió desde que se guarda el dato (sale del de siempre).
    """
    async with get_session() as session:
        return (
            await session.execute(
                select(Mensaje.numero_negocio)
                .where(
                    Mensaje.canal == "whatsapp",
                    Mensaje.user_id == user_id,
                    Mensaje.direccion == "in",
                    Mensaje.numero_negocio.is_not(None),
                )
                .order_by(Mensaje.creado_en.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


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


def texto_acuse_pausa(momento: datetime.datetime) -> str:
    """Lo que se le dice al cliente que escribe mientras espera a un asesor."""
    cuando = horario_asesores.cuando_contestan(momento)
    if not cuando:
        return (
            "Recibí tu mensaje y ya lo tiene un asesor. Te contesta por aquí "
            "en cuanto se libere."
        )
    return (
        "Recibí tu mensaje y ya lo tiene un asesor. Nuestro equipo atiende "
        f"{horario_asesores.horario_legible()}, así que te contestamos {cuando}."
    )


async def _acusar_pausa(app: FastAPI, entrante: IncomingMessage, log) -> None:
    """Un aviso, una sola vez, al que escribe durante una pausa.

    Sin esto el cliente escribe "¿hola?" y no recibe nada. Nunca interrumpe
    el paso: si falla, la pausa sigue igual que antes.
    """
    try:
        if entrante.tipo == "texto" and cortesias.es_acuse(entrante.contenido):
            return  # a un "gracias" no hace falta contestarle que esperamos
        if not await conversaciones.toca_acusar_la_pausa(
            entrante.canal, entrante.user_id
        ):
            return
        texto = texto_acuse_pausa(horario_asesores.ahora_local())
        adaptador = _adaptador(app, entrante.canal) or app.state.adapter
        sid = await adaptador.send(entrante.user_id, OutgoingMessage(texto=texto))
        await _guardar_mensaje(
            "out", entrante.canal, entrante.user_id,
            conversaciones.TIPO_ACUSE_PAUSA, texto, twilio_sid=sid,
        )
        log.info("pausa_acusada")
    except Exception:
        log.exception("error_acusando_la_pausa")


_agrupador_de_rafagas: Agrupador | None = None


def _agrupador() -> Agrupador:
    """Uno para todo el proceso: el candado solo sirve si es el mismo."""
    global _agrupador_de_rafagas
    if _agrupador_de_rafagas is None:
        ajustes = get_settings()
        _agrupador_de_rafagas = Agrupador(
            espera=ajustes.espera_rafaga_segundos,
            maximo=ajustes.espera_rafaga_maximo_segundos,
        )
    return _agrupador_de_rafagas


async def _despachar(app: FastAPI, entrante: IncomingMessage) -> None:
    """Entrada de todos los webhooks: junta la ráfaga y contesta en orden.

    `procesar_mensaje` se busca al momento de llamar, no al definir, para
    que los tests puedan sustituirlo.
    """
    await _agrupador().recibir(entrante, lambda e: procesar_mensaje(app, e))


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
        # El estado de atención se revisa ANTES que el interrupt, y no al
        # revés. Un hilo escalado siempre tiene interrupt, así que cortando
        # ahí primero nunca se llegaba a reactivar: la red de las cuatro
        # horas quedaba muerta exactamente en el caso para el que se
        # escribió, y la conversación se quedaba muda para siempre esperando
        # que alguien la resolviera a mano. Pasó con un cliente que mandó sus
        # datos de cita y nadie le contestó en horas.
        estado_atencion = await conversaciones.revisar_pausa(
            entrante.canal, entrante.user_id, get_settings().horas_reactivar_bot
        )
        if estado_atencion == conversaciones.REACTIVADO:
            # Nadie contesto en horas: mejor un bot que un cliente en silencio.
            # Con Command(resume=...), que es lo único que quita el interrupt;
            # escribir el estado a secas dejaba el hilo pausado igual.
            log.warning("escalamiento_abandonado_bot_retoma")
            await _devolver_al_agente(
                entrante.canal, entrante.user_id, NOTA_NADIE_ATENDIO
            )
        snapshot = await app.state.graph.aget_state(config)
        if any(t.interrupts for t in snapshot.tasks):
            log.info("thread_escalado_bot_en_silencio")
            await _acusar_pausa(app, entrante, log)
            return
        if estado_atencion == conversaciones.PAUSADO:
            # Una persona lleva la conversacion: el bot no opina.
            log.info("conversacion_atendida_por_humano")
            await _acusar_pausa(app, entrante, log)
            return

        # Dos atajos que no necesitan al modelo, ambos solo cuando la
        # conversación no tiene nada abierto: un acuse suelto ("gracias") y
        # una pregunta de catálogo que ya se contestó antes igual.
        if entrante.tipo == "texto" and estado_atencion != conversaciones.REACTIVADO:
            sin_pendientes = not cortesias.hay_algo_abierto(
                snapshot.values or {}, bool(snapshot.next)
            )
            atajo = tipo_atajo = None
            if cortesias.es_acuse(entrante.contenido) and sin_pendientes:
                atajo, tipo_atajo = cortesias.RESPUESTA, "texto"
            elif sin_pendientes:
                guardada = await cache_respuestas.buscar(entrante.contenido)
                atajo, tipo_atajo = guardada, "cache"
            if atajo:
                log.info("contestado_sin_modelo", atajo=tipo_atajo)
                adaptador = _adaptador(app, entrante.canal) or app.state.adapter
                sid = await adaptador.send(
                    entrante.user_id, OutgoingMessage(texto=atajo)
                )
                await _guardar_mensaje(
                    "out",
                    entrante.canal,
                    entrante.user_id,
                    tipo_atajo,
                    atajo,
                    twilio_sid=sid,
                )
                if tipo_atajo == "cache":
                    # Que el hilo no pierda el paso: la próxima vez el modelo
                    # tiene que ver lo que ya se preguntó y se contestó.
                    await app.state.graph.aupdate_state(
                        config,
                        {
                            "messages": [
                                HumanMessage(content=entrante.contenido),
                                AIMessage(content=atajo),
                            ]
                        },
                    )
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
        if not (contenido or "").strip():
            log.warning("mensaje_vacio_contestado_sin_modelo")
            adaptador = _adaptador(app, entrante.canal) or app.state.adapter
            sid = await adaptador.send(
                entrante.user_id, OutgoingMessage(texto=MENSAJE_VACIO)
            )
            await _guardar_mensaje(
                "out", entrante.canal, entrante.user_id, "texto", MENSAJE_VACIO,
                twilio_sid=sid,
            )
            return
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
        adaptador = _adaptador(app, entrante.canal) or app.state.adapter
        sid = await adaptador.send(entrante.user_id, salida)
        await _guardar_mensaje(
            "out",
            entrante.canal,
            entrante.user_id,
            "interactivo" if ui else "texto",
            texto,
            twilio_sid=sid,
        )
        log.info("respuesta_enviada", twilio_sid=sid)

        # Si fue una pregunta de catálogo contestada sin consultar nada, se
        # guarda para el próximo que pregunte lo mismo. Va al final: guardar
        # nunca debe retrasar la respuesta al cliente.
        if entrante.tipo == "texto" and cache_respuestas.apta_para_guardar(
            entrante.contenido,
            texto,
            resultado.get("messages", []),
            hay_ui=ui is not None,
            escalado=bool(resultado.get("escalado")),
            nombre_cliente=(resultado.get("perfil") or {}).get("nombre", ""),
        ):
            await cache_respuestas.guardar(entrante.contenido, texto)
    except GraphRecursionError:
        # El agente se atoro en un bucle de tools: no dejar al cliente sin salida.
        log.exception("limite_de_pasos_agotado")
        try:
            await (_adaptador(app, entrante.canal) or app.state.adapter).send(
                entrante.user_id, OutgoingMessage(texto=MENSAJE_ATORADO)
            )
        except Exception:
            log.exception("error_enviando_mensaje_de_atasco")
        return
    except Exception:
        log.exception("error_procesando_mensaje")
        try:
            await (_adaptador(app, entrante.canal) or app.state.adapter).send(
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
        numero_negocio=entrante.numero_negocio,
    )
    background_tasks.add_task(_despachar, request.app, entrante)
    return Response(content="<Response/>", media_type="application/xml")


def _adaptador(app: FastAPI, canal: str):
    """El adaptador de ese canal, o None si no está configurado."""
    return getattr(app.state, "adapters", {}).get(canal)


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
                # Desde el número por el que agendó, no siempre el de siempre
                await app.state.adapter.remitente_para(cita.cliente_telefono),
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


async def _devolver_al_agente(canal: str, user_id: str, nota: str) -> bool:
    """Reanuda el hilo pausado y le deja al agente una nota de contexto.

    Sin la nota el historial sigue diciendo "ya te escalé" y el agente se
    niega a retomar el caso. Se agrega al hilo SIN invocar el grafo, para no
    enviarle un mensaje al cliente por nuestra cuenta. Devuelve True si había
    un hilo en pausa que reanudar.

    Antes de la nota va lo que se habló durante la pausa. Esos mensajes
    nunca pasaron por el grafo —el bot estaba callado— así que no existen
    en su memoria: al devolverle la conversación volvía a preguntar lo que
    el cliente ya le había contestado al asesor.
    """
    config = {"configurable": {"thread_id": f"{canal}:{user_id}"}}
    snapshot = await app.state.graph.aget_state(config)
    reanudado = False
    if any(t.interrupts for t in snapshot.tasks):
        await app.state.graph.ainvoke(Command(resume="atendido"), config)
        reanudado = True
    await app.state.graph.aupdate_state(
        config,
        {
            "messages": [*await _contexto_de_la_pausa(canal, user_id),
                         HumanMessage(content=nota)],
            "escalado": False,
        },
    )
    return reanudado


async def _contexto_de_la_pausa(canal: str, user_id: str) -> list[Any]:
    """Lo dicho mientras el bot callaba, como mensajes del hilo.

    Lo del cliente entra como suyo; lo que mandó el asesor entra como del
    bot, que es como tiene que leerlo para no repetirlo ni contradecirlo.
    Si algo falla se devuelve vacío: mejor un bot sin contexto que una
    conversación que no se puede devolver.
    """
    try:
        desde = await conversaciones.inicio_de_la_pausa(canal, user_id)
        if desde is None:
            return []
        hablado = await conversaciones.hablado_durante_la_pausa(canal, user_id, desde)
    except Exception:
        logger.exception("no_se_pudo_recuperar_el_contexto_de_la_pausa")
        return []
    return [
        HumanMessage(content=texto) if direccion == "in" else AIMessage(content=texto)
        for direccion, texto in hablado
    ]


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

    reanudado = await _devolver_al_agente(
        datos.canal, datos.user_id, NOTA_ESCALAMIENTO_RESUELTO
    )

    return {
        "ok": True,
        "escalamientos_atendidos": len(pendientes),
        "thread_reanudado": reanudado,
    }


@app.get("/internal/citas")
async def listar_citas(
    desde: str | None = None,
    hasta: str | None = None,
    x_api_key: str = Header(default=""),
) -> dict[str, Any]:
    """Citas en un rango de fechas (default: hoy a +14 días).

    Pensado para n8n (sincronización a Google Sheets y reportes) y para
    reconciliar si algún webhook se perdió.
    """
    _validar_api_key_interna(x_api_key)
    tz = ZoneInfo(get_settings().tz)
    hoy = datetime.datetime.now(tz).date()
    try:
        inicio = datetime.date.fromisoformat(desde) if desde else hoy
        fin = datetime.date.fromisoformat(hasta) if hasta else hoy + datetime.timedelta(days=14)
    except ValueError:
        raise HTTPException(status_code=400, detail="fechas_invalidas (usa YYYY-MM-DD)")

    async with get_session() as session:
        filas = (
            await session.execute(
                select(Cita, Slot, Sucursal)
                .join(Slot, Cita.slot_id == Slot.id)
                .join(Sucursal, Cita.sucursal_id == Sucursal.id)
                .where(Slot.fecha >= inicio, Slot.fecha <= fin)
                .order_by(Slot.fecha, Slot.hora_inicio)
            )
        ).all()

    return {
        "desde": inicio.isoformat(),
        "hasta": fin.isoformat(),
        "total": len(filas),
        "citas": [
            {
                "folio": cita.id,
                "estado": cita.estado,
                "cliente": cita.cliente_nombre,
                "telefono": cita.cliente_telefono,
                "sucursal": sucursal.nombre,
                "direccion": sucursal.direccion,
                "fecha": slot.fecha.isoformat(),
                "fecha_legible": fecha_legible(slot.fecha),
                "hora": slot.hora_inicio.strftime("%H:%M"),
                "canal": cita.canal,
                "creada_en": cita.creada_en.isoformat(),
            }
            for cita, slot, sucursal in filas
        ],
    }


@app.get("/internal/metricas")
async def obtener_metricas(
    dias: int = 30, x_api_key: str = Header(default="")
) -> dict[str, Any]:
    """Métricas del panel: conversaciones, citas, canales y escalamientos."""
    _validar_api_key_interna(x_api_key)
    if not 1 <= dias <= MAX_DIAS_PANEL:
        raise HTTPException(status_code=400, detail="dias fuera de rango (1-365)")
    return await metricas.calcular(dias)


@app.get("/panel", response_class=HTMLResponse)
async def panel() -> HTMLResponse:
    """Página del panel. No trae datos: los pide a /internal/metricas con la
    clave que el usuario escribe, así la página puede servirse sin secretos."""
    if not RUTA_PANEL.exists():
        raise HTTPException(status_code=404, detail="panel no encontrado")
    return HTMLResponse(RUTA_PANEL.read_text(encoding="utf-8"))


class RespuestaHumana(BaseModel):
    texto: str


@app.get("/internal/conversaciones")
async def listar_conversaciones(
    buscar: str = "",
    limite: int = 50,
    estado: str = "",
    x_api_key: str = Header(default=""),
) -> dict[str, Any]:
    """Bandeja: conversaciones por actividad reciente, con su estado.

    `estado`: esperando_asesor | atendida | cerrada | bot. Vacío = todas.
    """
    _validar_api_key_interna(x_api_key)
    return await conversaciones.listar(buscar, limite, estado)


@app.get("/internal/conversaciones/{canal}/{user_id}")
async def ver_conversacion(
    canal: str, user_id: str, x_api_key: str = Header(default="")
) -> dict[str, Any]:
    """Historial completo de una conversación."""
    _validar_api_key_interna(x_api_key)
    return await conversaciones.historial(canal, user_id)


@app.post("/internal/conversaciones/{canal}/{user_id}/responder")
async def responder_conversacion(
    canal: str,
    user_id: str,
    datos: RespuestaHumana,
    x_api_key: str = Header(default=""),
) -> dict[str, Any]:
    """Envía un mensaje escrito por una persona y silencia al bot.

    Silenciar es parte de responder: si el bot siguiera activo, contestaría
    también al próximo mensaje del cliente y se pisarían.
    """
    _validar_api_key_interna(x_api_key)
    texto = datos.texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="texto vacío")
    adaptador = _adaptador(app, canal)
    if adaptador is None:
        raise HTTPException(
            status_code=400,
            detail=f"el canal '{canal}' aún no tiene adaptador de envío",
        )

    pausado_ahora = await conversaciones.tomar_control(canal, user_id)
    try:
        sid = await adaptador.send(user_id, OutgoingMessage(texto=texto))
    except Exception as exc:
        logger.exception("error_enviando_respuesta_humana")
        raise HTTPException(
            status_code=502,
            detail="no se pudo enviar (¿venció la ventana de 24h de WhatsApp?)",
        ) from exc

    await _guardar_mensaje("out", canal, user_id, "humano", texto, twilio_sid=sid)
    logger.info(
        "respuesta_humana_enviada",
        user_id=enmascarar_user_id(user_id),
        bot_pausado_ahora=pausado_ahora,
    )
    return {"ok": True, "twilio_sid": sid, "bot_pausado": True}


@app.post("/internal/conversaciones/{canal}/{user_id}/devolver-al-bot")
async def devolver_al_bot(
    canal: str, user_id: str, x_api_key: str = Header(default="")
) -> dict[str, Any]:
    """Cierra los escalamientos pendientes y reactiva al bot."""
    return await resolver_escalamiento(
        ResolverEscalamiento(user_id=user_id, canal=canal), x_api_key
    )


@app.get("/internal/escalamientos/pendientes")
async def escalamientos_pendientes(
    x_api_key: str = Header(default=""),
) -> dict[str, Any]:
    """Escalamientos sin atender, con cuánto llevan esperando.

    Pensado para que n8n consulte cada pocos minutos y avise al equipo.
    """
    _validar_api_key_interna(x_api_key)
    pendientes = await conversaciones.pendientes_detallados()
    return {
        "total": len(pendientes),
        "horas_para_reactivar_bot": get_settings().horas_reactivar_bot,
        "escalamientos": pendientes,
    }


class SolicitudSyncPedidos(BaseModel):
    # Vacío = los meses configurados en PEDIDOS_MESES_HISTORIAL
    meses: int | None = None


@app.post("/internal/pedidos/sincronizar")
async def sincronizar_pedidos(
    datos: SolicitudSyncPedidos | None = None, x_api_key: str = Header(default="")
) -> dict[str, Any]:
    """Vuelve a leer la hoja STATUS del Google Sheet y reemplaza la tabla.

    Pensado para un cron de n8n cada pocas horas. Si la hoja no se puede
    leer devuelve 503 y la copia anterior queda intacta: el bot sigue
    contestando con los datos de la última sincronización buena.
    """
    _validar_api_key_interna(x_api_key)
    meses = datos.meses if datos else None
    try:
        return await google_sheets.sincronizar(meses)
    except google_sheets.ErrorSincronizacion as exc:
        logger.warning("sincronizacion_pedidos_fallida", error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/webhooks/meta")
async def verificar_webhook_meta(request: Request) -> Response:
    """Meta llama esta URL una sola vez, al dar de alta el webhook.

    Hay que devolverle textualmente el hub.challenge que manda; si no, el
    panel de Meta no deja guardar la URL.
    """
    parametros = request.query_params
    esperado = get_settings().meta_verify_token
    if (
        esperado
        and parametros.get("hub.mode") == "subscribe"
        and parametros.get("hub.verify_token") == esperado
    ):
        logger.info("webhook_meta_verificado")
        return Response(
            content=parametros.get("hub.challenge", ""), media_type="text/plain"
        )
    logger.warning("verificacion_meta_rechazada")
    raise HTTPException(status_code=403, detail="verify token inválido")


@app.post("/webhooks/meta")
async def webhook_meta(
    request: Request, background_tasks: BackgroundTasks
) -> Response:
    """Mensajes de Instagram DM y Facebook Messenger.

    Un mismo POST puede traer varios eventos de varias conversaciones. Se
    contesta 200 siempre que la firma sea buena: si tardamos o fallamos, Meta
    reintenta y el cliente recibe la respuesta duplicada.
    """
    settings = get_settings()
    cuerpo = await request.body()
    if not canal_meta.validar_firma(
        cuerpo, request.headers.get("X-Hub-Signature-256", ""), settings.meta_app_secret
    ):
        logger.warning("firma_meta_invalida")
        raise HTTPException(status_code=403, detail="Firma de Meta inválida")

    try:
        payload = json.loads(cuerpo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cuerpo no es JSON") from exc

    for canal, evento in canal_meta.desglosar(payload):
        adaptador = _adaptador(request.app, canal)
        if adaptador is None:
            logger.warning("mensaje_de_canal_sin_token", canal=canal)
            continue

        entrante = adaptador.parse_incoming(evento)
        if not entrante.user_id:
            continue
        # Meta reintenta: idempotencia por el mid del mensaje.
        if entrante.message_sid and await _mensaje_ya_procesado(
            entrante.message_sid
        ):
            logger.info("webhook_duplicado_ignorado", mid=entrante.message_sid)
            continue

        await _guardar_mensaje(
            "in",
            entrante.canal,
            entrante.user_id,
            entrante.tipo,
            entrante.contenido,
            item_id=entrante.item_id,
            twilio_sid=entrante.message_sid,
        )
        background_tasks.add_task(_despachar, request.app, entrante)

    return Response(status_code=200)


@app.post("/internal/conversaciones/{canal}/{user_id}/cerrar")
async def cerrar_conversacion(
    canal: str, user_id: str, x_api_key: str = Header(default="")
) -> dict[str, Any]:
    """El asesor da la conversación por resuelta.

    Queda en verde, el bot vuelve a quedar a cargo y el agente recibe una nota
    para tratar el siguiente mensaje del cliente como una consulta nueva. No
    se le manda nada al cliente: para despedirse está la respuesta rápida
    /cierre. Si el cliente vuelve a escribir, la conversación se reabre sola.
    """
    _validar_api_key_interna(x_api_key)
    resueltos = await conversaciones.cerrar(canal, user_id)
    try:
        reanudado = await _devolver_al_agente(canal, user_id, NOTA_CONVERSACION_CERRADA)
        nota_agregada = True
    except Exception:
        # El cierre ya quedó guardado, que es lo que ve el asesor; si el hilo
        # no acepta la nota, el agente solo pierde ese contexto.
        logger.exception("error_avisando_cierre_al_agente")
        reanudado, nota_agregada = False, False

    logger.info(
        "conversacion_cerrada",
        user_id=enmascarar_user_id(user_id),
        canal=canal,
        escalamientos_resueltos=resueltos,
    )
    return {
        "ok": True,
        "estado": conversaciones.CERRADA,
        "escalamientos_resueltos": resueltos,
        "thread_reanudado": reanudado,
        "nota_agregada": nota_agregada,
    }


class DatosRespuestaRapida(BaseModel):
    atajo: str
    texto: str


def _error_respuesta(exc: respuestas_rapidas.ErrorRespuesta) -> HTTPException:
    codigo = 409 if isinstance(exc, respuestas_rapidas.AtajoDuplicado) else 400
    return HTTPException(status_code=codigo, detail=str(exc))


@app.get("/internal/respuestas-rapidas")
async def listar_respuestas_rapidas(
    x_api_key: str = Header(default=""),
) -> dict[str, Any]:
    _validar_api_key_interna(x_api_key)
    return {
        "respuestas": await respuestas_rapidas.listar(),
        "maximo": respuestas_rapidas.MAX_RESPUESTAS,
    }


@app.post("/internal/respuestas-rapidas", status_code=201)
async def crear_respuesta_rapida(
    datos: DatosRespuestaRapida, x_api_key: str = Header(default="")
) -> dict[str, Any]:
    _validar_api_key_interna(x_api_key)
    try:
        return await respuestas_rapidas.crear(datos.atajo, datos.texto)
    except respuestas_rapidas.ErrorRespuesta as exc:
        raise _error_respuesta(exc) from exc


@app.put("/internal/respuestas-rapidas/{respuesta_id}")
async def actualizar_respuesta_rapida(
    respuesta_id: int,
    datos: DatosRespuestaRapida,
    x_api_key: str = Header(default=""),
) -> dict[str, Any]:
    _validar_api_key_interna(x_api_key)
    try:
        actualizada = await respuestas_rapidas.actualizar(
            respuesta_id, datos.atajo, datos.texto
        )
    except respuestas_rapidas.ErrorRespuesta as exc:
        raise _error_respuesta(exc) from exc
    if actualizada is None:
        raise HTTPException(status_code=404, detail="Esa respuesta ya no existe.")
    return actualizada


@app.delete("/internal/respuestas-rapidas/{respuesta_id}")
async def borrar_respuesta_rapida(
    respuesta_id: int, x_api_key: str = Header(default="")
) -> dict[str, Any]:
    _validar_api_key_interna(x_api_key)
    if not await respuestas_rapidas.borrar(respuesta_id):
        raise HTTPException(status_code=404, detail="Esa respuesta ya no existe.")
    return {"ok": True}
