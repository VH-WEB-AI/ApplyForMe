"""LLM Gateway: the only module in the codebase that talks to a model provider.

Every engine goes through here so the underlying provider/model can be swapped
(env-config only) without touching engine code (see design principle: model-agnostic).
"""

import time
from dataclasses import dataclass
from functools import lru_cache

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


@dataclass
class ChatResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float


@dataclass
class EmbeddingResult:
    vector: list[float]
    model: str
    latency_ms: float


# Reasoning-tier models (o1/o3/gpt-5 families) reject any temperature other than
# their default (1), and support a reasoning_effort knob the SDK doesn't expose
# as a named param yet — passed through via extra_body instead.
_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def _is_reasoning_model(model: str) -> bool:
    return model.startswith(_REASONING_MODEL_PREFIXES)


@lru_cache
def _client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or "https://api.openai.com/v1",
        timeout=45.0,
        max_retries=0,
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def chat_completion(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool = True,
    temperature: float = 0.3,
) -> ChatResult:
    settings = get_settings()
    start = time.perf_counter()

    kwargs: dict = {
        "model": settings.openai_chat_model,
        "response_format": {"type": "json_object"} if json_mode else None,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if _is_reasoning_model(settings.openai_chat_model):
        kwargs["extra_body"] = {"reasoning_effort": settings.openai_reasoning_effort}
    else:
        kwargs["temperature"] = temperature

    response = _client().chat.completions.create(**kwargs)

    latency_ms = (time.perf_counter() - start) * 1000
    choice = response.choices[0].message.content or ""
    usage = response.usage

    return ChatResult(
        content=choice,
        model=response.model,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        latency_ms=latency_ms,
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def create_embedding(text: str) -> EmbeddingResult:
    settings = get_settings()
    start = time.perf_counter()

    response = _client().embeddings.create(model=settings.openai_embedding_model, input=text)

    latency_ms = (time.perf_counter() - start) * 1000
    return EmbeddingResult(
        vector=response.data[0].embedding,
        model=response.model,
        latency_ms=latency_ms,
    )
