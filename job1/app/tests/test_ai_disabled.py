import pytest

from app.shared_services import embedding_service as embedding_module
from app.shared_services.embedding_service import EmbeddingService
from app.shared_services.llm_client import LLMClient


@pytest.mark.asyncio
async def test_embedding_service_returns_deterministic_local_vector_when_ai_disabled(monkeypatch):
    monkeypatch.setattr(embedding_module.settings, "AI_ENABLED", False)
    monkeypatch.setattr(embedding_module.settings, "EMBEDDING_DIM", 8)

    service = EmbeddingService()

    first = await service.get_embedding("backend engineer roles")
    second = await service.get_embedding("backend engineer roles")
    different = await service.get_embedding("product manager roles")

    assert first == second
    assert first != different
    assert len(first) == 8


@pytest.mark.asyncio
async def test_llm_client_returns_offline_message_when_ai_disabled(monkeypatch):
    from app.shared_services import llm_client as llm_module

    monkeypatch.setattr(llm_module.settings, "AI_ENABLED", False)

    response = await LLMClient().chat_completion(
        [
            {"role": "system", "content": "You are a career copilot."},
            {"role": "user", "content": "User message: What should I improve?"},
        ]
    )

    assert response.model == "offline-disabled-ai"
    assert "AI is disabled" in response.content
    assert "What should I improve?" in response.content
