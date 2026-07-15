"""Tests estructurales del contrato multicanal (Fase 5)."""

import pytest

from sidhe_agent.channels.base import ChannelAdapter
from sidhe_agent.channels.chatwoot import ChatwootAdapter
from sidhe_agent.channels.whatsapp_twilio import WhatsAppTwilioAdapter


def test_abc_no_es_instanciable():
    with pytest.raises(TypeError):
        ChannelAdapter()  # type: ignore[abstract]


def test_adapters_cumplen_el_contrato():
    assert issubclass(WhatsAppTwilioAdapter, ChannelAdapter)
    assert issubclass(ChatwootAdapter, ChannelAdapter)
    assert WhatsAppTwilioAdapter.canal == "whatsapp"
    assert ChatwootAdapter.canal == "chatwoot"


async def test_chatwoot_es_esqueleto_documentado():
    adapter = ChatwootAdapter(
        base_url="https://chatwoot.example.com", account_id=1, api_access_token="tok"
    )
    with pytest.raises(NotImplementedError):
        adapter.parse_incoming({})
    with pytest.raises(NotImplementedError):
        await adapter.send("1:99", None)
    with pytest.raises(NotImplementedError):
        await adapter.handoff(123)
    # El plan de integración vive en el docstring del módulo
    import sidhe_agent.channels.chatwoot as modulo

    assert "toggle_status" in modulo.__doc__
    assert "message_created" in modulo.__doc__
