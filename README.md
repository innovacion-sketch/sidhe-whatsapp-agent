# sidhe-whatsapp-agent

Agente conversacional de atención a clientes por WhatsApp para **Sidhe Group**
(plantillas ortopédicas personalizadas impresas en 3D, ~30 sucursales en
tiendas Liverpool). Responde preguntas frecuentes sin alucinar, tiene memoria
de corto y largo plazo, transcribe notas de voz y (Fase 2) agenda citas de
estudio de pisada con botones interactivos.

**Stack:** Python 3.12 + uv · FastAPI · LangGraph (checkpointer y store sobre
Postgres) · Claude Sonnet (`claude-sonnet-4-6`, prompt caching) · PostgreSQL 16
+ pgvector · Twilio WhatsApp (Content API) · Whisper vía Groq · Docker.

## Estado de fases

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Núcleo conversacional: FAQs cacheadas, webhook Twilio, voz, memoria multi-día | ✅ |
| 2 | Citas con botones (list-picker/quick-reply, concurrencia de slots) | ✅ |
| 3 | Memoria avanzada (perfil, resumen) + escalamiento con interrupt + recordatorios | ✅ |
| 4 | RAG activo (ingesta + pgvector HNSW) | ✅ |
| 5 | Multicanal (Chatwoot: Messenger/Instagram) | ✅ estructura |

## Desarrollo local

Requisitos: [uv](https://docs.astral.sh/uv/), Docker.

```bash
# 1. Dependencias
uv sync

# 2. Postgres local con pgvector
docker compose up -d db

# 3. Variables de entorno
cp .env.example .env   # llena las credenciales

# 4. Migraciones + system prompt + seeds
uv run alembic upgrade head
uv run python scripts/build_system_prompt.py
uv run python scripts/seed_sucursales.py
uv run python scripts/seed_slots.py --dias 14 --minutos 60

# 5. Correr el servidor
uv run uvicorn sidhe_agent.main:app --reload --port 8000

# 6. Tests
uv run pytest
```

### Conectar Twilio en desarrollo

1. Expón el puerto local: `ngrok http 8000`.
2. En la consola de Twilio (sender de WhatsApp), configura el webhook de
   mensajes entrantes: `POST https://<tu-ngrok>/webhooks/twilio/whatsapp`.
3. Pon `PUBLIC_BASE_URL=https://<tu-ngrok>` en `.env` (la firma de Twilio se
   valida contra la URL pública, no la interna).
4. Escribe al sender `+52 1 56 3895 5164` desde WhatsApp.

Para probar sin firma real (solo local): `TWILIO_VALIDATE_SIGNATURE=false`.

### Probar el flujo de citas (Fase 2)

Con sucursales y slots sembrados, desde WhatsApp:

1. Escribe "quiero una cita" **sin** mencionar ciudad → llega un list-picker
   de zonas. Si mencionas ciudad/plaza ("cita en Perisur"), salta directo a
   la sucursal.
2. Toca zona → sucursal → fecha ("Lun 20 jul") → horario ("11:00").
3. El agente pide tu nombre si no lo conoce y muestra los botones
   "Confirmar ✅ / Cambiar / Cancelar". Solo agenda tras tocar Confirmar; la
   confirmación trae folio y dirección.
4. "¿Qué citas tengo?" lista tus citas; "cancela mi cita" pide confirmación
   con botones antes de cancelar (libera el cupo).

Notas de operación:
- Los mensajes interactivos se crean al vuelo en la Content API (friendly
  names `sidhe_lista_*` / `sidhe_botones_*`); solo funcionan dentro de la
  ventana de 24h, y si la Content API falla el mensaje se degrada a lista
  numerada en texto.
- Concurrencia: `agendar_cita` bloquea el slot con `SELECT ... FOR UPDATE`;
  si dos clientes confirman el mismo horario a la vez, uno recibe
  alternativas en lugar de una doble reserva (test:
  `tests/test_tools_citas.py::test_slot_lleno_concurrente`).
- Los tests de tools de citas requieren Postgres corriendo (`docker compose
  up -d db`); sin DB se saltan automáticamente.

## Deploy en Easypanel

1. **Servicio de DB:** ya existe `sidhe-postgres`. Verifica que la imagen tenga
   pgvector (p. ej. `pgvector/pgvector:pg16`); la migración crea las
   extensiones `vector` y `unaccent`.
2. **App:** crea un servicio tipo *App* → *Build desde GitHub* apuntando a este
   repo. Easypanel detecta el `Dockerfile`.
3. **Variables de entorno:** copia las de `.env.example` con valores reales.
   `DATABASE_URL` apunta al servicio interno, p. ej.
   `postgresql://usuario:pass@sidhe-postgres:5432/sidhe`.
4. **Dominio:** asigna un dominio/HTTPS al puerto 8000 y ponlo en
   `PUBLIC_BASE_URL`.
5. **Primera vez:** ejecuta en el contenedor
   `alembic upgrade head && python scripts/build_system_prompt.py && python scripts/seed_sucursales.py`.
6. **Twilio:** apunta el webhook del sender a
   `https://<dominio>/webhooks/twilio/whatsapp`.
7. Healthcheck: `GET /health` (verifica la conexión a la DB).

## Variables de entorno

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | DSN de Postgres (`postgresql://...`), único para negocio y memoria |
| `ANTHROPIC_API_KEY` | API key de Anthropic |
| `ANTHROPIC_MODEL` | Default `claude-sonnet-4-6` |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Credenciales de Twilio |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+5215638955164` |
| `TWILIO_VALIDATE_SIGNATURE` | `true` en producción |
| `PUBLIC_BASE_URL` | URL pública del servicio (validación de firma tras proxy) |
| `GROQ_API_KEY` | Whisper vía Groq (`whisper-large-v3-turbo`) |
| `OPENAI_API_KEY` | Fallback de transcripción (`whisper-1`) y embeddings `openai` |
| `EMBEDDINGS_PROVIDER` | `voyage` \| `cohere` \| `openai` (default) \| `bge-m3` (stub) |
| `EMBEDDINGS_MODEL` | Vacío = default del proveedor; dimensión fija 1024 |
| `VOYAGE_API_KEY` / `COHERE_API_KEY` | Según el proveedor elegido |
| `INTERNAL_API_KEY` | Protege los endpoints `/internal/*` |
| `HORAS_REACTIVAR_BOT` | Horas sin atención antes de que el bot retome (default 4) |
| `TWILIO_RECORDATORIO_CONTENT_SID` | SID (HX...) del template de recordatorio aprobado |
| `GOOGLE_CREDENTIALS_JSON` | JSON completo de la cuenta de servicio (vacío = sin calendario) |
| `GOOGLE_CALENDAR_RECORDATORIO_MIN` | Minutos de aviso en el evento (default 60) |
| `GOOGLE_SHEETS_PEDIDOS_ID` | Id del Sheet de operaciones (vacío = sin sincronizar pedidos) |
| `GOOGLE_SHEETS_PEDIDOS_HOJA` | Pestaña con el estado de pedidos (default `STATUS`) |
| `PEDIDOS_MESES_HISTORIAL` | Meses de pedidos que se guardan (default 3) |
| `N8N_WEBHOOK_CITAS` | URL del webhook de n8n para citas (vacío = no se envía) |
| `TZ` | `America/Mexico_City` |
| `LOG_LEVEL` | `INFO` por default |

## Contrato de recordatorios (para n8n)

El cron de recordatorios vive en n8n, fuera de este repo. Contrato:

```
POST /internal/recordatorios/enviar
Header: X-API-Key: <INTERNAL_API_KEY>
Body: {"ventana_horas": 24}   # citas confirmadas en las próximas N horas
Respuesta: {"enviados": n, "omitidos": m, "errores": k}
```

Envía a cada cliente el Content Template de WhatsApp pre-aprobado (variables:
nombre, sucursal, fecha, hora), válido fuera de la ventana de 24h. Es
idempotente: cada cita se recuerda una sola vez (auditado en la tabla
`mensajes` con tipo `recordatorio`), así que n8n puede llamarlo cada hora sin
duplicar envíos.

Setup único del template (requiere aprobación de WhatsApp, categoría UTILITY):

```bash
uv run python scripts/setup_recordatorio_template.py
# imprime el HX... → ponlo en TWILIO_RECORDATORIO_CONTENT_SID
```

## Citas en Google Calendar y Google Sheets

Cada cita confirmada se refleja automáticamente en el calendario de su
sucursal y se avisa a n8n (para la hoja de cálculo). La agenda real vive en
Postgres: **si Google o n8n fallan, la cita NO se pierde ni se bloquea** —
solo se registra el error.

### Google Calendar (una sola vez)

1. En [Google Cloud Console](https://console.cloud.google.com): crea un
   proyecto, habilita **Google Calendar API** y crea una **cuenta de
   servicio**. Genera una llave JSON y copia el correo de la cuenta
   (`algo@proyecto.iam.gserviceaccount.com`).
2. Pega el **contenido completo** del JSON en la variable de entorno
   `GOOGLE_CREDENTIALS_JSON` (no una ruta: el JSON entero).
3. En **cada** cuenta de Gmail de sucursal: Configuración del calendario →
   *Compartir con determinadas personas* → agrega ese correo con permiso
   **"Hacer cambios en los eventos"**.
4. Pon el correo del calendario de cada sucursal en la columna `calendar_id`
   de `data/sucursales.csv` (para Gmail el `calendar_id` **es** el correo) y
   corre `python scripts/seed_sucursales.py` (es upsert).

Las sucursales sin `calendar_id` simplemente no se sincronizan.

**Atajo: crear los calendarios por API.** En vez de entrar sucursal por
sucursal, la cuenta de servicio puede crear un calendario por sucursal,
guardarlo en la base y compartirlo con el stand. Pon el correo de cada
sucursal en la columna `correo_sucursal` de `data/sucursales.csv` y corre:

```bash
python scripts/crear_calendarios.py --dry-run   # qué haría
python scripts/crear_calendarios.py             # hazlo
python scripts/crear_calendarios.py --admin citas@sidhegroup.com
```

Es idempotente (omite las que ya tienen `calendar_id`), pausa entre altas
para no toparse con el límite de uso de Google, y con `--admin` deja a una
cuenta de la empresa como propietaria de cada calendario.

Lo único que no se puede automatizar: **cada sucursal recibe un correo y
alguien de esa cuenta tiene que hacer clic en "Añadir este calendario"**.
Si no les llega, pueden agregarlo a mano con el id (Google Calendar → Otros
calendarios → Suscribirse a un calendario → pegar el id).

Para comprobar el setup completo (credenciales, qué sucursales tienen
calendario y teléfono, y acceso al Sheet):

```bash
uv run python scripts/verificar_google.py
uv run python scripts/verificar_google.py --probar "Liverpool Polanco"
```

Con `--probar` crea un evento de prueba en esa sucursal y lo borra enseguida:
es la única forma de confirmar que el calendario se compartió con permiso de
escritura y no solo de lectura.

> Dentro del contenedor **no hay `uv`** (la imagen final solo trae el venv en
> el `PATH`): ahí todos los scripts se corren como `python scripts/…`. El
> prefijo `uv run` es para tu máquina de desarrollo.

El evento incluye nombre del cliente, teléfono, folio, dirección del stand y
un recordatorio popup (`GOOGLE_CALENDAR_RECORDATORIO_MIN`, default 60 min).
Al cancelar por WhatsApp, el evento se borra del calendario.

### Google Sheets vía n8n

Push en tiempo real: define `N8N_WEBHOOK_CITAS` con la URL del webhook y el
bot enviará en cada alta/baja:

```json
{"evento": "creada", "cita": {"folio": 1, "cliente": "...", "telefono": "...",
 "sucursal": "...", "fecha": "2026-09-02", "hora": "11:00", ...}}
```

Incluye el header `X-API-Key` con `INTERNAL_API_KEY` para que n8n valide el
origen. En n8n: nodo **Webhook** → **Google Sheets (Append)**.

Para reportes o para reconciliar si un webhook se perdió:

```
GET /internal/citas?desde=2026-09-01&hasta=2026-09-15
Header: X-API-Key: <INTERNAL_API_KEY>
```

## Estado de pedidos (hoja STATUS del Google Sheet de operaciones)

El bot responde "¿ya están mis plantillas?" consultando la tabla `pedidos`,
una copia de la hoja STATUS. **Busca por el teléfono de la conversación**, que
el sistema ya conoce: el cliente no tiene que dar ningún dato. Solo si ese
número no aparece pide nombre completo y sucursal, y si aun así no aparece le
pasa el teléfono de su sucursal para atención directa.

Solo se guardan los últimos `PEDIDOS_MESES_HISTORIAL` meses (default 3): lo
anterior es historial y nadie pregunta por WhatsApp por plantillas de hace dos
años. La ventana se mide desde la fecha más reciente de la hoja, no desde hoy,
para que un par de días sin actualizar no vacíe la tabla.

### Qué puede decir el bot

**Solo `EN SUCURSAL` autoriza a decir "ya puedes pasar por ellas"**. Todo lo
demás sigue en fabricación; el bot no promete fechas.

| Categoría | Status de la hoja |
|---|---|
| `listo_en_sucursal` | EN SUCURSAL |
| `entregado` | ENTREGADO |
| `enviado_a_domicilio` | ENVIADO A DOMICILIO (y variantes) |
| `en_proceso` | vacío, IMPRESION, IMPRESION LISTA, TERMINADO, PEGADO, PEDIDO y **cualquier etapa desconocida** |
| `requiere_revision` | VER EN…, VER EM…, NO PROCEDE, GARANTIA, CANCELADO, DEVOLUCION |

Las excepciones ganan a los prefijos: `VER EN SUCURSAL` es revisión, no
"listo". En la hoja real, la ventana de 3 meses da 5,199 pedidos, 94.6% con
teléfono y **0 que requieran revisión humana**.

### Sincronizar desde el Google Sheet (recomendado)

Se lee en vivo con la **misma cuenta de servicio de Calendar**, así que no hay
credencial nueva que crear:

1. En Google Cloud Console habilita también **Google Sheets API**.
2. Comparte el Sheet de operaciones con el correo de la cuenta de servicio
   (`algo@proyecto.iam.gserviceaccount.com`); basta permiso de **lector**.
3. Copia el id del Sheet (lo que va entre `/d/` y `/edit` en la URL) en
   `GOOGLE_SHEETS_PEDIDOS_ID`.

```bash
uv run python scripts/verificar_google.py     # diagnóstico de todo lo de Google
uv run python scripts/sincronizar_pedidos.py  # sincroniza una vez
```

En producción, un cron de n8n cada pocas horas:

```
POST /internal/pedidos/sincronizar
Header: X-API-Key: <INTERNAL_API_KEY>
Body (opcional): {"meses": 3}
```

Cada sincronización reemplaza la tabla completa. **Si la hoja no se puede
leer, viene con otro encabezado o la ventana queda vacía, no se borra nada**:
devuelve 503 y el bot sigue contestando con la última copia buena.

### Respaldo: importar un .xlsx descargado

```bash
uv run --with openpyxl python scripts/importar_pedidos.py "PACIENTES SUPERVISION.xlsx" --meses 3
```

## El teléfono de la sucursal como última salida

Ningún cliente debe quedarse sin a dónde ir. El bot da el teléfono de su
sucursal cuando no encuentra su pedido, cuando el tema excede lo que puede
resolver, y cuando ya escaló pero nadie lo atendió. Lo hace **además** de
escalar, no en lugar de escalar.

Los números viven en la columna `telefono` de `data/sucursales.csv`; para
cargarlos o corregirlos, edita el CSV y corre `python
scripts/seed_sucursales.py` (es upsert). `scripts/verificar_google.py` lista
las sucursales que aún no tienen número.

## Conversaciones abandonadas

Un escalamiento sin respuesta dejaba al cliente escribiendo al vacío (pasó de
verdad: cinco días de mensajes sin que nadie contestara). Ahora:

- Si nadie atiende un escalamiento en `HORAS_REACTIVAR_BOT` horas (default 4),
  **el bot retoma la conversación**, se disculpa una vez por la demora y sigue
  atendiendo.
- `GET /internal/escalamientos/pendientes` lista los que esperan y cuántas
  horas llevan, para que n8n avise al equipo antes de que se llegue a ese
  límite.

## Panel de métricas

`GET /panel` sirve un tablero con lo que va pasando en las conversaciones:
clientes atendidos, mensajes (entrantes/salientes), citas agendadas y su tasa
de conversión, eventos sincronizados al calendario, escalamientos pendientes,
tiempo promedio de primera respuesta, notas de voz, toques de botón, serie
diaria de mensajes, desglose por canal, próximas citas y citas por sucursal.

La página no contiene secretos: pide la `INTERNAL_API_KEY` al entrar, la
guarda en el navegador y con ella consulta `GET /internal/metricas?dias=30`
(mismo header `X-API-Key` que el resto de `/internal/*`).

Todo se calcula sobre las tablas de auditoría (`mensajes`, `citas`,
`escalamientos`), y el canal es una columna: **cuando el adaptador de Chatwoot
empiece a escribir con canal `facebook` o `instagram`, aparecen solos en el
panel** — no hay nada que cambiar en las métricas.

### Bandeja de conversaciones

La pestaña **Conversaciones** del panel es una bandeja: lista de chats
ordenados por actividad, historial completo (con las notas de voz ya
transcritas y los toques de botón) y caja para responder a mano.

**Responder toma el control de la conversación**: al enviar un mensaje desde
el panel se crea un escalamiento pendiente y el bot se calla en ese chat — si
no, bot y asesor contestarían al mismo cliente. El botón *Devolver al bot* lo
reactiva. La cabecera avisa si la ventana de 24h de WhatsApp sigue abierta;
fuera de ella WhatsApp rechaza el texto libre y el panel lo dice.

Endpoints (todos con `X-API-Key`):

```
GET  /internal/conversaciones?buscar=&limite=50
GET  /internal/conversaciones/{canal}/{user_id}
POST /internal/conversaciones/{canal}/{user_id}/responder        {"texto": "..."}
POST /internal/conversaciones/{canal}/{user_id}/devolver-al-bot
```

Enviar solo funciona en canales con adaptador (hoy WhatsApp); en Instagram o
Facebook el endpoint responde 400 explicando por qué, hasta que exista el
adaptador de Chatwoot.

Para revisar el diseño sin base de datos (métricas y chats de ejemplo):

```bash
uv run python scripts/demo_panel.py   # http://localhost:8123/panel
```

## Escalamiento a humano

Cuando el agente escala (el cliente lo pide, queja de garantía, tema médico
fuera de FAQs o 2 intentos fallidos), registra el caso en la tabla
`escalamientos`, se despide confirmando que un asesor contactará al cliente y
**pausa el thread** (`interrupt()` de LangGraph): mientras está pausado el bot
guarda silencio y los mensajes del cliente los atiende el humano (quedan
auditados en `mensajes`, no en el contexto del agente). Para reanudar el bot:

```
POST /internal/escalamientos/resolver
Header: X-API-Key: <INTERNAL_API_KEY>
Body: {"user_id": "+52...", "canal": "whatsapp"}
Respuesta: {"ok": true, "escalamientos_atendidos": n, "thread_reanudado": true}
```

## Memoria

- **Corto plazo**: checkpoints de LangGraph por thread (`whatsapp:+52...`),
  persisten indefinidamente en Postgres — el agente recuerda entre días.
- **Perfil (largo plazo)**: al final de cada turno un extractor con salida
  estructurada guarda hechos duraderos (nombre, sucursal habitual,
  padecimiento mencionado, tipo de plantilla de interés) en el Store
  (namespace `("perfiles", user_id)`); se inyectan como `<perfil_cliente>` en
  cada turno. No se guardan datos médicos sensibles más allá de lo que el
  cliente dijo explícitamente.
- **Resumen/trim**: al superar 30 mensajes, los viejos se condensan en
  `<resumen_conversacion>` y se recortan del historial (control de costo).

## RAG (base documental)

La tool `buscar_conocimiento` hace búsqueda semántica (coseno, índice HNSW de
pgvector, top 5) sobre la tabla `chunks`; el system prompt instruye usarla
solo cuando la respuesta no está en las FAQs. Con la base vacía devuelve una
nota y el agente lo dice honestamente.

Ingesta de documentos (.md / .txt):

```bash
# Configura EMBEDDINGS_PROVIDER y su API key en .env, luego:
uv run python scripts/ingest_documents.py data/docs/garantias.md
uv run python scripts/ingest_documents.py "data/docs/*.md"
```

- Chunking por párrafos (~1200 chars, overlap 200 en cortes duros).
- Re-ingestar el mismo archivo reemplaza sus chunks (idempotente por ruta).
- Proveedores de embeddings (`EMBEDDINGS_PROVIDER`): `voyage` (voyage-3.5),
  `cohere` (embed-multilingual-v3.0), `openai` (text-embedding-3-small con
  `dimensions=1024`) y `bge-m3` como stub para self-hosted. La dimensión es
  siempre 1024 (columna `vector(1024)`); cambiar de proveedor requiere
  re-ingestar el corpus.

## Multicanal (estructura)

El núcleo es agnóstico al canal: grafo, tools y memoria solo conocen los
contratos de `channels/schemas.py` y la ABC `ChannelAdapter`
(`src/sidhe_agent/channels/base.py`), cuyo docstring documenta los tres pasos
para agregar un canal nuevo (normalizar entrada, renderizar salida, webhook).

Para Facebook Messenger, Instagram DM y comentarios FB/IG la ruta será
Meta → Chatwoot (AgentBot) → este servicio. El plan de integración completo
(payload del webhook `message_created`, respuesta vía la API de Chatwoot,
UI con `input_select` y handoff cambiando el status a `open`) está documentado
en `src/sidhe_agent/channels/chatwoot.py`, que hoy es un esqueleto sin
implementar a propósito. La Meta Graph API directa no se usa todavía.

## Arquitectura (resumen)

```
Twilio webhook → FastAPI → normalizador (texto | audio→Whisper | selección interactiva)
  → LangGraph  (thread_id = "whatsapp:+52...")
      ├─ checkpointer AsyncPostgresSaver  → memoria de la conversación (multi-día)
      ├─ store AsyncPostgresStore ("perfiles", user_id) → memoria de largo plazo
      ├─ nodo agente: Claude Sonnet + system prompt cacheado (FAQs) + tools
      └─ nodo tools: buscar_conocimiento, escalar_a_humano (+ citas en Fase 2)
  → OutgoingMessage → WhatsAppTwilioAdapter → Twilio REST
```

Regla anti-alucinación: los datos operativos (precios, horarios, sucursales,
citas) salen SOLO de las FAQs del system prompt o de las tools; si no hay
dato, el agente lo dice y ofrece escalar a un humano.
