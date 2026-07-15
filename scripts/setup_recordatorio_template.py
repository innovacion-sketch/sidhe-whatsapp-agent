"""Crea el Content Template de recordatorio de cita y solicita su aprobación
a WhatsApp (categoría UTILITY). Ejecutar UNA sola vez por cuenta de Twilio.

Uso: uv run python scripts/setup_recordatorio_template.py

Imprime el content_sid (HX...); ponlo en la env var
TWILIO_RECORDATORIO_CONTENT_SID. La aprobación de WhatsApp puede tardar;
consulta su estado en la consola de Twilio (Content Template Builder).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sidhe_agent.config import get_settings
from sidhe_agent.services.twilio_content import (
    crear_content,
    payload_template_recordatorio,
    solicitar_aprobacion_whatsapp,
)


async def main() -> None:
    settings = get_settings()
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise SystemExit("Faltan TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN en .env")

    sid = await crear_content(
        payload_template_recordatorio(),
        settings.twilio_account_sid,
        settings.twilio_auth_token,
    )
    print(f"Content creado: {sid}")

    aprobacion = await solicitar_aprobacion_whatsapp(
        sid, settings.twilio_account_sid, settings.twilio_auth_token
    )
    print(f"Solicitud de aprobación enviada: {aprobacion}")
    print(f"\nAgrega a tu .env:\nTWILIO_RECORDATORIO_CONTENT_SID={sid}")


if __name__ == "__main__":
    asyncio.run(main())
