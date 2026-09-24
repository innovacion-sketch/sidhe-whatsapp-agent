"""Mensajes en ráfaga: una respuesta por ráfaga, y nunca dos turnos a la vez.

En 30 días entraron 527 mensajes a menos de 10 segundos del anterior del
mismo cliente. Cada uno lanzaba su propia ejecución del bot en paralelo:
respuestas encimadas, turnos pagados de más y memoria pisada.
"""

import asyncio

from sidhe_agent.channels.schemas import IncomingMessage
from sidhe_agent.services.rafagas import Agrupador, fusionar


def msg(texto: str = "", tipo: str = "texto", user: str = "+521", **extra) -> IncomingMessage:
    return IncomingMessage(canal="whatsapp", user_id=user, tipo=tipo, contenido=texto, **extra)


class Registro:
    """Un `procesar` falso que anota qué recibió y cuántos corrían a la vez."""

    def __init__(self, tarda: float = 0.0):
        self.recibidos: list[IncomingMessage] = []
        self.a_la_vez = 0
        self.maximo_a_la_vez = 0
        self.tarda = tarda

    async def __call__(self, entrante: IncomingMessage) -> None:
        self.a_la_vez += 1
        self.maximo_a_la_vez = max(self.maximo_a_la_vez, self.a_la_vez)
        await asyncio.sleep(self.tarda)
        self.recibidos.append(entrante)
        self.a_la_vez -= 1


# --- fusionar ---

def test_los_textos_seguidos_se_vuelven_uno():
    unidos = fusionar([msg("hola"), msg("quiero info"), msg("¿cuánto cuesta?")])

    assert len(unidos) == 1
    assert unidos[0].contenido == "hola\nquiero info\n¿cuánto cuesta?"


def test_un_boton_en_medio_corta_la_union():
    """Juntarlo con texto le quitaría al agente el id exacto de la opción."""
    unidos = fusionar([
        msg("hola"),
        msg("Polanco", tipo="seleccion_interactiva", item_id="suc_1"),
        msg("gracias"),
    ])

    assert [m.tipo for m in unidos] == ["texto", "seleccion_interactiva", "texto"]
    assert unidos[1].item_id == "suc_1"


def test_se_conserva_el_numero_al_que_escribieron():
    unidos = fusionar([msg("a", numero_negocio="+5215638955164"), msg("b")])
    assert unidos[0].numero_negocio == "+5215638955164"


# --- agrupador ---

async def test_una_rafaga_de_texto_se_contesta_una_sola_vez():
    agrupador, registro = Agrupador(espera=0.05), Registro()

    await asyncio.gather(
        agrupador.recibir(msg("hola"), registro),
        _despues(0.01, agrupador.recibir(msg("quiero info"), registro)),
        _despues(0.02, agrupador.recibir(msg("¿cuánto cuesta?"), registro)),
    )

    assert len(registro.recibidos) == 1
    assert registro.recibidos[0].contenido == "hola\nquiero info\n¿cuánto cuesta?"


async def test_mensajes_separados_se_contestan_por_separado():
    agrupador, registro = Agrupador(espera=0.02), Registro()

    await agrupador.recibir(msg("hola"), registro)
    await agrupador.recibir(msg("¿cuánto cuesta?"), registro)

    assert [m.contenido for m in registro.recibidos] == ["hola", "¿cuánto cuesta?"]


async def test_un_boton_no_espera():
    """Un toque es un mensaje completo: hacerlo esperar solo se siente lento."""
    agrupador, registro = Agrupador(espera=5.0), Registro()
    reloj = asyncio.get_running_loop().time

    inicio = reloj()
    await agrupador.recibir(msg("Polanco", tipo="seleccion_interactiva", item_id="s"), registro)

    assert reloj() - inicio < 1.0
    assert len(registro.recibidos) == 1


async def test_un_boton_que_llega_despierta_al_texto_que_esperaba():
    agrupador, registro = Agrupador(espera=5.0), Registro()
    reloj = asyncio.get_running_loop().time

    inicio = reloj()
    await asyncio.gather(
        agrupador.recibir(msg("este"), registro),
        _despues(0.01, agrupador.recibir(
            msg("Polanco", tipo="seleccion_interactiva", item_id="s"), registro
        )),
    )

    assert reloj() - inicio < 1.0
    assert [m.tipo for m in registro.recibidos] == ["texto", "seleccion_interactiva"]


async def test_quien_escribe_sin_parar_no_espera_de_mas():
    """Con tope: cada mensaje alarga la espera, pero no para siempre."""
    agrupador, registro = Agrupador(espera=0.05, maximo=0.12), Registro()

    async def escribir_sin_parar():
        for i in range(12):
            await agrupador.recibir(msg(f"m{i}"), registro)
            await asyncio.sleep(0.03)

    await escribir_sin_parar()

    assert len(registro.recibidos) >= 2  # el tope cortó al menos una vez


async def test_nunca_corren_dos_turnos_del_mismo_cliente_a_la_vez():
    """El caso que pisaba la memoria: dos ejecuciones escribiendo el hilo."""
    agrupador, registro = Agrupador(espera=0.0), Registro(tarda=0.05)

    await asyncio.gather(*[
        agrupador.recibir(msg(f"m{i}"), registro) for i in range(4)
    ])

    assert registro.maximo_a_la_vez == 1
    assert [m.contenido for m in registro.recibidos] == ["m0", "m1", "m2", "m3"]


async def test_clientes_distintos_si_van_en_paralelo():
    """El candado es por conversación, no global: nadie espera a otro cliente."""
    agrupador, registro = Agrupador(espera=0.0), Registro(tarda=0.05)

    await asyncio.gather(
        agrupador.recibir(msg("a", user="+521"), registro),
        agrupador.recibir(msg("b", user="+522"), registro),
    )

    assert registro.maximo_a_la_vez == 2


async def test_un_mensaje_roto_no_se_lleva_a_los_demas():
    agrupador, procesados = Agrupador(espera=0.0), []

    async def procesar(entrante):
        if entrante.item_id == "roto":
            raise RuntimeError("falló")
        procesados.append(entrante.contenido)

    await agrupador.recibir(msg("x", tipo="seleccion_interactiva", item_id="roto"), procesar)
    await agrupador.recibir(msg("sigue"), procesar)

    assert procesados == ["sigue"]


async def test_no_se_quedan_candados_colgados():
    """Un candado por cliente que ya se fue sería memoria que solo crece."""
    agrupador, registro = Agrupador(espera=0.0), Registro()

    await asyncio.gather(*[
        agrupador.recibir(msg("x", user=f"+52{i}"), registro) for i in range(20)
    ])

    assert agrupador._candados == {}
    assert agrupador._usos == {}
    assert agrupador._lotes == {}


async def _despues(segundos: float, corrutina):
    await asyncio.sleep(segundos)
    await corrutina
