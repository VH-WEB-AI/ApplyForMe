"""
Shared Embedding Generator service. Wraps LLMClient.embed with caching
(Redis) so repeated text (e.g. re-scored resumes) doesn't re-hit the API.
"""
import hashlib
import random

from app.shared_services.cache_service import cache_service
from app.shared_services.llm_client import llm_client
from app.core.config import get_settings

settings = get_settings()


class EmbeddingService:
    def _cache_key(self, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        model = (
            settings.GEMINI_EMBEDDING_MODEL
            if settings.AI_PROVIDER == "gemini"
            else settings.OPENAI_EMBEDDING_MODEL
        )
        return f"embedding:{settings.AI_PROVIDER}:{model}:{digest}"

    def _offline_embedding(self, text: str) -> list[float]:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(settings.EMBEDDING_DIM)]

    async def get_embedding(self, text: str) -> list[float]:
        if not settings.AI_ENABLED:
            return self._offline_embedding(text)

        key = self._cache_key(text)
        cached = await cache_service.get_json(key)
        if cached is not None:
            return cached

        embedding = await llm_client.embed(text)
        await cache_service.set_json(key, embedding, ttl=settings.CACHE_TTL_EMBEDDINGS)
        return embedding

    async def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        if not settings.AI_ENABLED:
            return [self._offline_embedding(text) for text in texts]

        # Simple per-item cache check; batches only the misses to OpenAI.
        results: list[list[float] | None] = [None] * len(texts)
        misses: list[int] = []
        for i, text in enumerate(texts):
            cached = await cache_service.get_json(self._cache_key(text))
            if cached is not None:
                results[i] = cached
            else:
                misses.append(i)

        if misses:
            fresh = await llm_client.embed_batch([texts[i] for i in misses])
            for idx, emb in zip(misses, fresh):
                results[idx] = emb
                await cache_service.set_json(self._cache_key(texts[idx]), emb, ttl=settings.CACHE_TTL_EMBEDDINGS)

        return results  # type: ignore[return-value]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


embedding_service = EmbeddingService()
