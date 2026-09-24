"""Juntar los mensajes que un cliente manda seguidos y contestarlos una vez.

En WhatsApp nadie escribe un párrafo: escriben "hola", "quiero info",
"¿cuánto cuesta?" en tres mensajes con segundos de diferencia. Antes cada
uno lanzaba su propia ejecución del bot, EN PARALELO sobre la misma
conversación. Salían tres respuestas encimadas, se pagaban tres turnos
del modelo, y dos ejecuciones escribían la memoria al mismo tiempo, así
que una podía pisar a la otra. En 30 días fueron 527 mensajes así.

Dos piezas, porque son dos problemas:

- **Espera.** Un mensaje de texto no se contesta al llegar: se esperan unos
  segundos de silencio por si viene otro, y los textos seguidos se unen en
  uno. Con tope, para que alguien que escribe sin parar no espere de más.
  Un toque en un botón o una nota de voz no esperan: son un mensaje
  completo, y hacerlos esperar solo haría sentir lento al bot.
- **Candado por conversación.** Aunque no hubiera espera, dos turnos del
  mismo cliente nunca corren a la vez. El segundo espera a que termine el
  primero, y en orden.

Vive en memoria: sirve porque el servicio corre en un solo proceso. Si
algún día se levantan varios, el candado tendría que pasar a Postgres.
"""

import asyncio
from collections.abc import Awaitable, Callable

import structlog

from ..channels.schemas import IncomingMessage

logger = structlog.get_logger(__name__)

Procesar = Callable[[IncomingMessage], Awaitable[None]]

# Solo el texto espera a ver si viene más
TIPOS_QUE_ESPERAN = frozenset({"texto"})


def fusionar(lote: list[IncomingMessage]) -> list[IncomingMessage]:
    """Los textos seguidos se vuelven uno; lo demás se queda como vino.

    Un botón o un audio en medio corta la unión: se contestan en su orden,
    porque juntarlos con texto le quitaría al agente el id exacto de la
    opción tocada.
    """
    resultado: list[IncomingMessage] = []
    for mensaje in lote:
        previo = resultado[-1] if resultado else None
        if previo is not None and previo.tipo == "texto" and mensaje.tipo == "texto":
            resultado[-1] = previo.model_copy(
                update={
                    "contenido": f"{previo.contenido}\n{mensaje.contenido}".strip(),
                    "message_sid": mensaje.message_sid or previo.message_sid,
                    "numero_negocio": mensaje.numero_negocio or previo.numero_negocio,
                    "nombre_perfil": mensaje.nombre_perfil or previo.nombre_perfil,
                }
            )
        else:
            resultado.append(mensaje)
    return resultado


class Agrupador:
    """Junta y ordena los mensajes de cada conversación.

    El primer mensaje de una ráfaga se queda de "dueño": espera, cierra el
    lote y lo procesa. Los que llegan mientras tanto solo se suman y se van.
    Así encaja con las tareas en segundo plano de FastAPI sin tareas sueltas
    que haya que vigilar.
    """

    def __init__(self, espera: float, maximo: float = 15.0) -> None:
        self.espera = max(0.0, espera)
        self.maximo = max(self.espera, maximo)
        self._lotes: dict[str, list[IncomingMessage]] = {}
        self._ultimo: dict[str, float] = {}
        self._ya: dict[str, asyncio.Event] = {}
        self._candados: dict[str, asyncio.Lock] = {}
        self._usos: dict[str, int] = {}

    async def recibir(self, entrante: IncomingMessage, procesar: Procesar) -> None:
        clave = f"{entrante.canal}:{entrante.user_id}"
        reloj = asyncio.get_running_loop().time

        if clave in self._lotes:
            # Alguien ya está juntando los de este cliente: se suma y se va
            self._lotes[clave].append(entrante)
            self._ultimo[clave] = reloj()
            if entrante.tipo not in TIPOS_QUE_ESPERAN:
                self._ya[clave].set()
            return

        self._lotes[clave] = [entrante]
        primero = self._ultimo[clave] = reloj()
        ya = self._ya[clave] = asyncio.Event()
        if entrante.tipo not in TIPOS_QUE_ESPERAN:
            ya.set()
        try:
            while not ya.is_set():
                limite = min(self._ultimo[clave] + self.espera, primero + self.maximo)
                falta = limite - reloj()
                if falta <= 0:
                    break
                try:
                    await asyncio.wait_for(ya.wait(), timeout=falta)
                except TimeoutError:
                    pass  # se vuelve a medir: pudo haber llegado otro texto
        finally:
            lote = self._lotes.pop(clave, [])
            self._ultimo.pop(clave, None)
            self._ya.pop(clave, None)

        # Se cuenta quién usa el candado para poder soltarlo al final sin que
        # otro turno se quede con uno distinto: entre setdefault y el conteo
        # no hay ningún await, así que no se puede colar nadie.
        candado = self._candados.setdefault(clave, asyncio.Lock())
        self._usos[clave] = self._usos.get(clave, 0) + 1
        try:
            async with candado:
                for mensaje in fusionar(lote):
                    try:
                        await procesar(mensaje)
                    except Exception:
                        # Que un mensaje roto no se lleve a los que venían atrás
                        logger.exception("error_procesando_mensaje_del_lote")
        finally:
            self._usos[clave] -= 1
            if not self._usos[clave]:
                del self._usos[clave]
                del self._candados[clave]
