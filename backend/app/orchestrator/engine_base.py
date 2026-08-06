"""Engine base contract every AI engine implements, so the AI Orchestrator can drive
all of them through the same lifecycle: gather context -> build prompt -> invoke LLM
-> validate -> postprocess/persist. See spec section 6 (AI Orchestrator)."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.services.prompt_builder import PromptSpec


class Engine(ABC):
    name: str
    response_schema: type[BaseModel]

    @abstractmethod
    def gather_context(self, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        """Deterministic step: parse input, compute business-rule scores, retrieve
        candidate/vector data. Must not call the LLM."""

    @abstractmethod
    def build_prompt_spec(self, context: dict[str, Any]) -> PromptSpec:
        """Only the reasoning/explanation portion goes to the LLM (see design principle:
        LLMs reason and explain, they don't own critical calculations)."""

    @abstractmethod
    def postprocess(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
        llm_output: BaseModel,
    ) -> dict[str, Any]:
        """Merges deterministic context with the validated LLM output, persists rows,
        and returns the final JSON-serialisable response."""

    def cache_key(self, context: dict[str, Any]) -> str | None:
        """Optional: return a stable hash to skip the LLM call when nothing changed."""
        return None
