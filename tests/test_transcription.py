"""Tests del servicio de transcripción: prioridad Groq → fallback OpenAI."""

import pytest

from sidhe_agent.config import Settings
from sidhe_agent.services import transcription


@pytest.fixture
def sin_descarga(monkeypatch):
    async def fake_descarga(media_url: str) -> bytes:
        return b"OGG-bytes-de-prueba"

    monkeypatch.setattr(transcription, "_descargar_media", fake_descarga)


def _settings(**overrides) -> Settings:
    return Settings(twilio_account_sid="AC", twilio_auth_token="tk", **overrides)


async def test_usa_groq_si_hay_key(monkeypatch, sin_descarga):
    monkeypatch.setattr(
        transcription, "get_settings", lambda: _settings(groq_api_key="gsk_x")
    )

    async def fake_groq(datos: bytes, nombre: str) -> str:
        return "hola desde groq"

    monkeypatch.setattr(transcription, "_via_groq", fake_groq)
    texto = await transcription.transcribir_audio("https://media/x", "audio/ogg")
    assert texto == "hola desde groq"


async def test_fallback_openai_sin_groq(monkeypatch, sin_descarga):
    monkeypatch.setattr(
        transcription, "get_settings", lambda: _settings(openai_api_key="sk_x")
    )

    async def fake_openai(datos: bytes, nombre: str) -> str:
        return "hola desde openai"

    monkeypatch.setattr(transcription, "_via_openai", fake_openai)
    texto = await transcription.transcribir_audio("https://media/x", "audio/ogg")
    assert texto == "hola desde openai"


async def test_fallback_openai_si_groq_falla(monkeypatch, sin_descarga):
    monkeypatch.setattr(
        transcription,
        "get_settings",
        lambda: _settings(groq_api_key="gsk_x", openai_api_key="sk_x"),
    )

    async def groq_roto(datos: bytes, nombre: str) -> str:
        raise RuntimeError("groq caído")

    async def fake_openai(datos: bytes, nombre: str) -> str:
        return "rescatado por openai"

    monkeypatch.setattr(transcription, "_via_groq", groq_roto)
    monkeypatch.setattr(transcription, "_via_openai", fake_openai)
    texto = await transcription.transcribir_audio("https://media/x", "audio/ogg")
    assert texto == "rescatado por openai"


async def test_error_sin_proveedores(monkeypatch, sin_descarga):
    monkeypatch.setattr(transcription, "get_settings", lambda: _settings())
    with pytest.raises(transcription.TranscripcionError):
        await transcription.transcribir_audio("https://media/x", "audio/ogg")
