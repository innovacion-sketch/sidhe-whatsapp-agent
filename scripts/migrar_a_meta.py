"""Sacar un número de Twilio y ponerlo en nuestra propia WABA, por API.

Es el camino de vuelta del que hicimos en agosto: entonces movimos el 0202
a la WABA de Twilio y el asistente gráfico de Meta se atoró, así que se
hizo por Graph API. Esto es lo mismo al revés, y automatizado.

No hay MCP de Meta. Sí hay API, y cubre todo salvo tres cosas que un
humano tiene que hacer por fuerza:

- **El método de pago.** Se teclea una tarjeta; no hay endpoint, y tampoco
  es algo que deba pasar por un script.
- **La verificación del negocio.** La revisa Meta a mano y tarda días.
- **Nada más.** Lo de Twilio, la migración, el código de verificación y el
  registro sí entran aquí.

Uso, en este orden:

    python scripts/migrar_a_meta.py revisar
    python scripts/migrar_a_meta.py suscribir-app
    python scripts/migrar_a_meta.py crear-plantilla
    python scripts/migrar_a_meta.py soltar-de-twilio --numero +5215512340202
    python scripts/migrar_a_meta.py migrar          --numero +5215512340202
    python scripts/migrar_a_meta.py pedir-codigo    --numero +5215512340202
    python scripts/migrar_a_meta.py verificar       --numero +5215512340202 --codigo 123456
    python scripts/migrar_a_meta.py registrar       --numero +5215512340202 --pin 123456

`revisar` solo lee y se puede correr las veces que quieras. De ahí en
adelante cada paso toca la línea de verdad y pide confirmación.

Los tokens se leen del entorno y NUNCA se imprimen:

    META_MIGRACION_TOKEN   token con whatsapp_business_management
    META_WABA_ID           la WABA nuestra (la que recibe el número)

Van por entorno y no por config.py a propósito: ese token puede mover
números entre cuentas y no tiene por qué vivir en la configuración del
bot, que solo necesita mandar mensajes.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sidhe_agent.config import get_settings

GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = httpx.Timeout(30.0)


def _token() -> str:
    token = os.environ.get("META_MIGRACION_TOKEN", "").strip()
    if not token:
        salir(
            "Falta META_MIGRACION_TOKEN.\n"
            "  Se saca en business.facebook.com -> Usuarios del sistema ->\n"
            "  Generar token, con el permiso whatsapp_business_management.\n"
            "  Ponlo como variable de entorno; no lo pegues en ningún chat."
        )
    return token


def _waba() -> str:
    waba = os.environ.get("META_WABA_ID", "").strip()
    if not waba:
        salir("Falta META_WABA_ID (el id de NUESTRA cuenta de WhatsApp).")
    return waba


def salir(mensaje: str) -> None:
    print(f"\nERROR: {mensaje}\n")
    raise SystemExit(1)


def solo_digitos(numero: str) -> str:
    return "".join(c for c in numero if c.isdigit())


def partir(numero: str) -> tuple[str, str]:
    """+5215512340202 -> ('52', '15512340202'). México siempre es 52."""
    digitos = solo_digitos(numero)
    if not digitos.startswith("52"):
        salir(f"'{numero}' no parece un número mexicano (+52...).")
    return "52", digitos[2:]


def confirmar(que: str) -> None:
    print(f"\nOJO: {que}")
    if input("  Escribe SI para continuar: ").strip() != "SI":
        salir("Cancelado. No se tocó nada.")


async def graph(metodo: str, ruta: str, **datos) -> dict:
    """Llamada a la Graph API. Devuelve el JSON o revienta con el motivo."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
        respuesta = await cliente.request(
            metodo,
            f"{GRAPH}/{ruta}",
            headers={"Authorization": f"Bearer {_token()}"},
            **({"params": datos} if metodo == "GET" else {"data": datos}),
        )
    cuerpo = respuesta.json() if respuesta.content else {}
    if respuesta.status_code >= 400:
        error = (cuerpo.get("error") or {}).get("message", respuesta.text[:300])
        salir(f"Meta rechazó la llamada ({respuesta.status_code}):\n  {error}")
    return cuerpo


# --- revisar ---

async def revisar() -> None:
    """Los requisitos que Meta exige ANTES de dejarte migrar nada."""
    waba = _waba()
    print(f"Revisando la WABA {waba}\n")

    cuenta = await graph(
        "GET",
        waba,
        fields="id,name,account_review_status,business_verification_status,"
        "currency,timezone_id",
    )
    print(f"  Nombre:                {cuenta.get('name', '?')}")
    print(f"  Revisión de la cuenta: {cuenta.get('account_review_status', '?')}")
    print(f"  Verificación negocio:  {cuenta.get('business_verification_status', '?')}")
    print(f"  Moneda:                {cuenta.get('currency', '?')}")

    # De quién es la cuenta y quién la paga: decide si hay que MOVER los
    # números o basta con quitar a Twilio de en medio. Aparte, y sin
    # reventar, porque no todas las cuentas exponen estos campos.
    try:
        duenos = await graph(
            "GET", waba, fields="owner_business_info,on_behalf_of_business_info"
        )
        dueno = duenos.get("owner_business_info") or {}
        a_nombre = duenos.get("on_behalf_of_business_info") or {}
        print(f"  Dueño de la cuenta:     {dueno.get('name', '?')} ({dueno.get('id', '?')})")
        if a_nombre:
            print(f"  A nombre de:           {a_nombre.get('name', '?')} ({a_nombre.get('id', '?')})")
    except SystemExit:
        print("  Dueño de la cuenta:     (Meta no lo dejó ver con este token)")

    listo = (
        cuenta.get("account_review_status") == "APPROVED"
        and cuenta.get("business_verification_status") == "verified"
    )

    suscritas = (await graph("GET", f"{waba}/subscribed_apps")).get("data", [])
    print(f"\n  Apps suscritas al webhook: {len(suscritas)}")
    for app in suscritas:
        detalle = app.get("whatsapp_business_api_data") or {}
        print(f"    - {detalle.get('name', detalle.get('id', '?'))}")

    numeros = (
        await graph(
            "GET",
            f"{waba}/phone_numbers",
            fields="id,display_phone_number,verified_name,"
            "code_verification_status,quality_rating,platform_type",
        )
    ).get("data", [])
    print(f"\n  Números ya en esta WABA: {len(numeros)}")
    for numero in numeros:
        print(
            f"    - {numero.get('display_phone_number')}  "
            f"id={numero.get('id')}  "
            f"{numero.get('code_verification_status', '?')}  "
            f"plataforma={numero.get('platform_type', '?')}"
        )

    plantillas = (
        await graph("GET", f"{waba}/message_templates", fields="name,status,language")
    ).get("data", [])
    buscada = get_settings().whatsapp_cloud_plantilla_recordatorio
    print(f"\n  Plantilla de recordatorio ({buscada}):")
    propias = [t for t in plantillas if t.get("name") == buscada]
    if not propias:
        print("    todavía no existe -> corre crear-plantilla")
    for t in propias:
        print(f"    {t.get('language')}: {t.get('status')}")

    print("\n" + "-" * 62)
    if not listo:
        print("ERROR: Todavía NO se puede migrar.")
        print("  Meta exige la cuenta revisada y el negocio verificado.")
    elif not suscritas:
        print("ERROR: Falta suscribir una app al webhook de esta WABA.")
        print("  Sin eso el número migra pero no llegan los mensajes.")
    else:
        print("OK: Los requisitos de Meta están.")
    print("\n  El método de pago NO se ve por API. Compruébalo a mano en")
    print("  business.facebook.com -> Configuración de WhatsApp -> Pagos.")
    print("  Sin tarjeta, la migración falla en el último paso.")


# --- preparar la cuenta (no toca la linea) ---

async def suscribir_app() -> None:
    """Que los mensajes de esta WABA le lleguen a nuestra app.

    Sin esto el número migra, contesta Meta, y el webhook nunca se entera:
    el cliente escribe y nadie lo ve.
    """
    await graph("POST", f"{_waba()}/subscribed_apps")
    print("\nOK. La app quedó suscrita a la WABA.")
    print("  Falta configurar en la app de Meta -> WhatsApp -> Configuración:")
    print("    URL:          https://TU-DOMINIO/webhooks/whatsapp/cloud")
    print("    Verify token: el mismo de META_VERIFY_TOKEN")
    print("    Campo:        messages")


async def crear_plantilla() -> None:
    """Da de alta la plantilla de recordatorio para que Meta la apruebe.

    La de Twilio vive en la cuenta de Twilio y no viaja con el número. Esta
    trae además la dirección y los botones Confirmo / Reagendar / Cancelar.
    La aprobación de una plantilla de utilidad suele tardar de minutos a
    un día: conviene pedirla ANTES de migrar, para no quedarse sin
    recordatorios.
    """
    from sidhe_agent.channels.whatsapp_cloud import (
        CUERPO_RECORDATORIO,
        plantilla_recordatorio,
    )

    ajustes = get_settings()
    nombre = ajustes.whatsapp_cloud_plantilla_recordatorio
    idioma = ajustes.whatsapp_cloud_idioma_plantilla
    print(f"\nPlantilla '{nombre}' ({idioma}):\n  {CUERPO_RECORDATORIO}")
    print("  Botones: Confirmo asistencia · Reagendar · Cancelar cita")
    confirmar("Se va a mandar a revisión de Meta.")
    async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
        respuesta = await cliente.post(
            f"{GRAPH}/{_waba()}/message_templates",
            headers={"Authorization": f"Bearer {_token()}"},
            json=plantilla_recordatorio(nombre, idioma),
        )
    cuerpo = respuesta.json() if respuesta.content else {}
    if respuesta.status_code >= 400:
        error = (cuerpo.get("error") or {}).get("message", respuesta.text[:300])
        salir(f"Meta no la aceptó ({respuesta.status_code}):\n  {error}")
    print(f"\nOK. Estado: {cuerpo.get('status', '?')}")
    print("  Corre 'revisar' en un rato para ver si ya quedó APPROVED.")


# --- twilio ---

async def soltar_de_twilio(numero: str) -> None:
    """Borra el sender en Twilio: es lo que libera el número.

    Mientras el número sea un sender activo, Meta no lo deja moverse. Al
    borrarlo deja de recibir por Twilio, así que a partir de aquí hay
    servicio interrumpido hasta terminar el registro en Meta.
    """
    ajustes = get_settings()
    if not ajustes.twilio_account_sid or not ajustes.twilio_auth_token:
        salir("Faltan las credenciales de Twilio en el entorno.")

    url = "https://messaging.twilio.com/v2/Channels/Senders"
    auth = (ajustes.twilio_account_sid, ajustes.twilio_auth_token)
    async with httpx.AsyncClient(timeout=TIMEOUT, auth=auth) as cliente:
        listado = await cliente.get(url)
        listado.raise_for_status()
        senders = listado.json().get("senders", [])

        buscado = solo_digitos(numero)
        elegido = next(
            (s for s in senders if solo_digitos(s.get("sender_id", "")) == buscado),
            None,
        )
        if elegido is None:
            print(f"No hay ningún sender de Twilio para {numero}.")
            print("Puede que ya esté suelto. Corre 'revisar' y sigue con 'migrar'.")
            return

        print(f"\nSender encontrado: {elegido.get('sender_id')}")
        print(f"  sid:    {elegido.get('sid')}")
        print(f"  estado: {elegido.get('status')}")
        confirmar(
            f"Esto BORRA el sender de Twilio para {numero}.\n"
            "  Desde ese momento el número deja de recibir mensajes y no\n"
            "  vuelve a funcionar hasta terminar 'registrar' en Meta.\n"
            "  Hazlo en un rato de poco tráfico."
        )
        borrado = await cliente.delete(f"{url}/{elegido['sid']}")
        if borrado.status_code >= 400:
            salir(f"Twilio no lo borró ({borrado.status_code}): {borrado.text[:300]}")

    print(f"\nOK: Sender borrado. El número {numero} quedó libre.")
    print("  Siguiente: migrar")


# --- migracion ---

async def migrar(numero: str) -> None:
    cc, resto = partir(numero)
    confirmar(f"Se va a pedir a Meta que {numero} pase a la WABA {_waba()}.")
    respuesta = await graph(
        "POST",
        f"{_waba()}/phone_numbers",
        cc=cc,
        phone_number=resto,
        migrate_phone_number="true",
    )
    print(f"\nOK: Aceptado. phone_number_id = {respuesta.get('id')}")
    print("  Apúntalo: va en WHATSAPP_CLOUD_NUMEROS al terminar.")
    print("  Siguiente: pedir-codigo")


async def _id_de(numero: str) -> str:
    """El phone_number_id que Meta le dio a este número en nuestra WABA."""
    buscado = solo_digitos(numero)
    numeros = (
        await graph("GET", f"{_waba()}/phone_numbers", fields="id,display_phone_number")
    ).get("data", [])
    for fila in numeros:
        if solo_digitos(fila.get("display_phone_number", "")) == buscado:
            return fila["id"]
    salir(f"{numero} no aparece en la WABA {_waba()}. ¿Ya corriste 'migrar'?")


async def pedir_codigo(numero: str, metodo: str) -> None:
    phone_id = await _id_de(numero)
    await graph(
        "POST", f"{phone_id}/request_code", code_method=metodo, language="es_MX"
    )
    print(f"\nOK: Código pedido por {metodo} al {numero}.")
    if metodo == "VOICE":
        print("  Entra una llamada que lo dicta. Ten a alguien junto al teléfono.")
    print("  Siguiente: verificar --codigo 123456")


async def verificar(numero: str, codigo: str) -> None:
    phone_id = await _id_de(numero)
    await graph("POST", f"{phone_id}/verify_code", code=solo_digitos(codigo))
    print("\nOK: Número verificado.")
    print("  Siguiente: registrar --pin 123456")


async def registrar(numero: str, pin: str) -> None:
    """El paso final: aquí el número empieza a recibir por Cloud API."""
    phone_id = await _id_de(numero)
    await graph(
        "POST", f"{phone_id}/register", messaging_product="whatsapp", pin=solo_digitos(pin)
    )
    print(f"\nOK: {numero} registrado en Cloud API. phone_number_id = {phone_id}")
    print("\n  Falta ponerlo en el entorno del bot y desplegar:")
    print(f"    WHATSAPP_CLOUD_NUMEROS={numero}={phone_id}")
    print("  Si ya hay otro número en Meta, van los dos separados con ;")
    print("\n  Y comprobar que el webhook de esta WABA apunta a")
    print("  https://TU-DOMINIO/webhooks/whatsapp/cloud")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="paso", required=True)

    sub.add_parser("revisar", help="Solo lee: dice si se puede migrar")
    sub.add_parser("suscribir-app", help="Que los mensajes de la WABA lleguen al bot")
    sub.add_parser("crear-plantilla", help="Manda a aprobar la plantilla de recordatorio")

    for nombre, ayuda in [
        ("soltar-de-twilio", "Borra el sender de Twilio (interrumpe el servicio)"),
        ("migrar", "Pide a Meta el traslado a nuestra WABA"),
        ("pedir-codigo", "Pide el código de verificación"),
        ("verificar", "Confirma el código recibido"),
        ("registrar", "Último paso: lo deja recibiendo por Cloud API"),
    ]:
        p = sub.add_parser(nombre, help=ayuda)
        p.add_argument("--numero", required=True, help="+5215512340202")
        if nombre == "pedir-codigo":
            p.add_argument("--metodo", default="VOICE", choices=["VOICE", "SMS"])
        if nombre == "verificar":
            p.add_argument("--codigo", required=True)
        if nombre == "registrar":
            p.add_argument("--pin", required=True, help="El PIN de 6 dígitos")

    args = parser.parse_args()

    tareas = {
        "revisar": lambda: revisar(),
        "suscribir-app": lambda: suscribir_app(),
        "crear-plantilla": lambda: crear_plantilla(),
        "soltar-de-twilio": lambda: soltar_de_twilio(args.numero),
        "migrar": lambda: migrar(args.numero),
        "pedir-codigo": lambda: pedir_codigo(args.numero, args.metodo),
        "verificar": lambda: verificar(args.numero, args.codigo),
        "registrar": lambda: registrar(args.numero, args.pin),
    }
    asyncio.run(tareas[args.paso]())


if __name__ == "__main__":
    main()
