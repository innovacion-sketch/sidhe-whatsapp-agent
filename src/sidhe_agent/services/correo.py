"""Envío de correo por SMTP de Gmail, con las credenciales que ya existen.

Se reusan a propósito las mismas variables del sistema de asistencias
(GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_ALERTAS_TO, EMAIL_ALERTAS_CC): una
sola cuenta que mantener y unos destinatarios que ya están acordados.

Es stdlib corriendo fuera del event loop; no hace falta otra dependencia.
"""

import asyncio
import smtplib
from email.message import EmailMessage

import structlog

from ..config import get_settings

logger = structlog.get_logger(__name__)

SERVIDOR = "smtp.gmail.com"
PUERTO = 465


def destinatarios() -> tuple[list[str], list[str]]:
    ajustes = get_settings()
    separar = lambda s: [c.strip() for c in (s or "").replace(";", ",").split(",") if c.strip()]  # noqa: E731
    return separar(ajustes.email_alertas_to), separar(ajustes.email_alertas_cc)


def configurado() -> bool:
    ajustes = get_settings()
    para, _ = destinatarios()
    return bool(ajustes.gmail_user and ajustes.gmail_app_password and para)


def _enviar_sync(asunto: str, cuerpo: str) -> None:
    ajustes = get_settings()
    para, copia = destinatarios()
    mensaje = EmailMessage()
    mensaje["From"] = ajustes.gmail_user
    mensaje["To"] = ", ".join(para)
    if copia:
        mensaje["Cc"] = ", ".join(copia)
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo)
    with smtplib.SMTP_SSL(SERVIDOR, PUERTO, timeout=20) as servidor:
        servidor.login(ajustes.gmail_user, ajustes.gmail_app_password)
        servidor.send_message(mensaje, to_addrs=para + copia)


async def enviar(asunto: str, cuerpo: str) -> bool:
    """Manda el correo. Devuelve si salió; nunca lanza excepción."""
    if not configurado():
        logger.warning("correo_no_configurado", asunto=asunto)
        return False
    try:
        await asyncio.to_thread(_enviar_sync, asunto, cuerpo)
        logger.info("correo_enviado", asunto=asunto)
        return True
    except Exception:
        logger.exception("error_enviando_correo", asunto=asunto)
        return False
