"""
Thin, resilient wrapper around the OpenAI SDK. Every engine and the
orchestrator talks to the LLM only through this client — never directly —
so retries, timeouts, cost logging, and provider swaps happen in one place.
"""
import re
import time
from typing import Any, Optional

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.exceptions import LLMProviderError
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

class LLMResponse:
    def __init__(self, content: str, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: float):
        self.content = content
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.latency_ms = latency_ms


class LLMClient:
    """Provider-neutral LLM boundary for OpenAI and Gemini.

    Provider SDKs are imported lazily so selecting OpenAI does not require the
    Gemini package at runtime (and vice versa during local development).
    """

    def __init__(self) -> None:
        self._openai_client = None
        self._gemini_client = None

    def _offline_response(self, messages: list[dict[str, str]], model: Optional[str]) -> LLMResponse:
        """Deterministic local response used when AI calls are disabled."""
        start = time.perf_counter()
        user_message = self._last_user_message(messages)
        content = (
            "AI is disabled for this environment, so I cannot call the live career model right now. "
            f"I received your message: \"{user_message}\". "
            "Enable AI_ENABLED and configure the selected provider API key to get a personalized reply."
        )
        return LLMResponse(
            content=content,
            model=model or "offline-disabled-ai",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    @staticmethod
    def _last_user_message(messages: list[dict[str, str]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content", "").strip()
                match = re.search(r"User message:\s*(.+)\s*$", content, flags=re.DOTALL)
                if match:
                    return match.group(1).strip()[:240] or "an empty message"
                return content[:240] or "an empty message"
        return "an empty message"

    def _selected_provider(self) -> str:
        if settings.AI_PROVIDER == "openai":
            if not settings.OPENAI_API_KEY:
                raise LLMProviderError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
            return "openai"
        if not settings.GEMINI_API_KEY:
            raise LLMProviderError("GEMINI_API_KEY is required when AI_PROVIDER=gemini")
        return "gemini"

    def _openai(self):
        if self._openai_client is None:
            from openai import AsyncOpenAI

            self._openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._openai_client

    def _gemini(self):
        if self._gemini_client is None:
            from google import genai

            self._gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY).aio
        return self._gemini_client

    @staticmethod
    def _provider_error_message(provider: str, exc: Exception, operation: str) -> tuple[str, dict[str, str]]:
        reason = str(exc)
        lowered = reason.lower()
        if "resource_exhausted" in lowered or "quota exceeded" in lowered or "429" in lowered:
            return (
                f"{provider.title()} quota exhausted while running {operation}. "
                "Enable billing, wait for quota reset, use a model/key with available quota, or switch AI_PROVIDER.",
                {"reason": reason},
            )
        return (f"{provider.title()} {operation} request failed", {"reason": reason})

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
    )
    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        response_format: Optional[dict[str, Any]] = None,
    ) -> LLMResponse:
        if not settings.AI_ENABLED:
            return self._offline_response(messages, model)

        provider = self._selected_provider()
        start = time.perf_counter()
        try:
            if provider == "openai":
                resp = await self._openai().chat.completions.create(
                    model=model or settings.OPENAI_CHAT_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )
                content = resp.choices[0].message.content or ""
                usage = resp.usage
                response_model = resp.model
                prompt_tokens = usage.prompt_tokens if usage else 0
                completion_tokens = usage.completion_tokens if usage else 0
            else:
                system_instruction, contents = self._gemini_contents(messages)
                config: dict[str, Any] = {
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                }
                if system_instruction:
                    config["system_instruction"] = system_instruction
                if response_format and response_format.get("type") == "json_object":
                    config["response_mime_type"] = "application/json"
                    # Also reinforce JSON-only output in the last user message
                    if contents and contents[-1]["role"] == "user":
                        last_text = contents[-1]["parts"][0]["text"]
                        if "json" not in last_text.lower()[-100:]:
                            contents[-1]["parts"][0]["text"] = (
                                last_text + "\n\nRespond with valid JSON only. No explanation, no markdown."
                            )
                response_model = model or settings.GEMINI_CHAT_MODEL
                resp = await self._gemini().models.generate_content(
                    model=response_model, contents=contents, config=config
                )
                content = resp.text or ""
                usage = getattr(resp, "usage_metadata", None)
                prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
                completion_tokens = getattr(usage, "candidates_token_count", 0) or 0
        except Exception as exc:
            logger.error("llm_call_failed", provider=provider, error=str(exc))
            message, details = self._provider_error_message(provider, exc, "chat completion")
            raise LLMProviderError(message, details) from exc

        latency_ms = (time.perf_counter() - start) * 1000
        return LLMResponse(
            content=content,
            model=response_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def embed(self, text: str, *, model: Optional[str] = None) -> list[float]:
        provider = self._selected_provider()
        try:
            if provider == "openai":
                resp = await self._openai().embeddings.create(
                    model=model or settings.OPENAI_EMBEDDING_MODEL, input=text
                )
                return resp.data[0].embedding
            resp = await self._gemini().models.embed_content(
                model=model or settings.GEMINI_EMBEDDING_MODEL, contents=text
            )
            return resp.embeddings[0].values
        except Exception as exc:
            logger.error("embedding_call_failed", provider=provider, error=str(exc))
            message, details = self._provider_error_message(provider, exc, "embedding")
            raise LLMProviderError(message, details) from exc

    async def embed_batch(self, texts: list[str], *, model: Optional[str] = None) -> list[list[float]]:
        provider = self._selected_provider()
        try:
            if provider == "openai":
                resp = await self._openai().embeddings.create(
                    model=model or settings.OPENAI_EMBEDDING_MODEL, input=texts
                )
                return [d.embedding for d in resp.data]
            resp = await self._gemini().models.embed_content(
                model=model or settings.GEMINI_EMBEDDING_MODEL, contents=texts
            )
            return [embedding.values for embedding in resp.embeddings]
        except Exception as exc:
            logger.error("embedding_batch_call_failed", provider=provider, error=str(exc))
            message, details = self._provider_error_message(provider, exc, "embedding")
            raise LLMProviderError(message, details) from exc

    @staticmethod
    def _gemini_contents(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, Any]]]:
        """Translate the app's OpenAI-style messages into Gemini contents."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = message["role"]
            if role == "system":
                system_parts.append(message["content"])
                continue
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": message["content"]}],
                }
            )
        return "\n\n".join(system_parts), contents


llm_client = LLMClient()
