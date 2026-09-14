"""Respuestas rápidas del panel: mensajes ya redactados que el asesor inserta
escribiendo /atajo en la conversación.

El atajo se normaliza (minúsculas, sin acentos, sin espacios) para que el
asesor no tenga que acordarse si lo dio de alta como "Garantía" o "garantia":
lo que teclea después de la diagonal siempre encuentra lo mismo.
"""

import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..db.models import RespuestaRapida
from ..db.session import get_session

MAX_RESPUESTAS = 100
MAX_ATAJO = 30
MAX_TEXTO = 1000  # WhatsApp admite más, pero una respuesta rápida no es un ensayo


class ErrorRespuesta(ValueError):
    """Dato inválido; el mensaje se muestra tal cual en el panel."""


class AtajoDuplicado(ErrorRespuesta):
    pass


class LimiteAlcanzado(ErrorRespuesta):
    pass


def normalizar_atajo(valor: str) -> str:
    """'/Garantía Plantillas' → 'garantia-plantillas'."""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", valor or "")
        if unicodedata.category(c) != "Mn"
    )
    texto = sin_acentos.strip().lstrip("/").lower()
    texto = re.sub(r"\s+", "-", texto)
    texto = re.sub(r"[^a-z0-9_-]", "", texto)
    return texto[:MAX_ATAJO]


def validar(atajo: str, texto: str) -> tuple[str, str]:
    atajo_limpio = normalizar_atajo(atajo)
    texto_limpio = (texto or "").strip()
    if not atajo_limpio:
        raise ErrorRespuesta("El atajo no puede quedar vacío (usa letras o números).")
    if not texto_limpio:
        raise ErrorRespuesta("El mensaje no puede quedar vacío.")
    if len(texto_limpio) > MAX_TEXTO:
        raise ErrorRespuesta(f"El mensaje no puede pasar de {MAX_TEXTO} caracteres.")
    return atajo_limpio, texto_limpio


def _a_dict(r: RespuestaRapida) -> dict:
    return {"id": r.id, "atajo": r.atajo, "texto": r.texto}


async def listar() -> list[dict]:
    async with get_session() as session:
        filas = (
            await session.execute(select(RespuestaRapida).order_by(RespuestaRapida.atajo))
        ).scalars().all()
    return [_a_dict(r) for r in filas]


async def crear(atajo: str, texto: str) -> dict:
    atajo, texto = validar(atajo, texto)
    async with get_session() as session:
        total = (
            await session.execute(select(func.count(RespuestaRapida.id)))
        ).scalar_one()
        if total >= MAX_RESPUESTAS:
            raise LimiteAlcanzado(
                f"Ya tienes {MAX_RESPUESTAS} respuestas rápidas. Borra alguna para agregar otra."
            )
        nueva = RespuestaRapida(atajo=atajo, texto=texto)
        session.add(nueva)
        try:
            await session.commit()
        except IntegrityError as exc:
            raise AtajoDuplicado(f"Ya existe una respuesta con el atajo /{atajo}.") from exc
        await session.refresh(nueva)
        return _a_dict(nueva)


async def actualizar(respuesta_id: int, atajo: str, texto: str) -> dict | None:
    atajo, texto = validar(atajo, texto)
    async with get_session() as session:
        respuesta = await session.get(RespuestaRapida, respuesta_id)
        if respuesta is None:
            return None
        respuesta.atajo = atajo
        respuesta.texto = texto
        respuesta.actualizado_en = func.now()
        try:
            await session.commit()
        except IntegrityError as exc:
            raise AtajoDuplicado(f"Ya existe una respuesta con el atajo /{atajo}.") from exc
        await session.refresh(respuesta)
        return _a_dict(respuesta)


async def borrar(respuesta_id: int) -> bool:
    async with get_session() as session:
        respuesta = await session.get(RespuestaRapida, respuesta_id)
        if respuesta is None:
            return False
        await session.delete(respuesta)
        await session.commit()
        return True
