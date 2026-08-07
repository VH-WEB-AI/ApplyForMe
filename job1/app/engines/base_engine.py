"""
Base class all four AI engines implement. Standardizes: input -> prompt ->
LLM call -> JSON parse -> schema validation -> JSON output, with audit
logging baked in via the orchestrator (engines stay stateless & focused).
"""
from abc import ABC, abstractmethod
from typing import Any

from app.orchestrator.context_manager import CandidateContext


class BaseEngine(ABC):
    name: str

    @abstractmethod
    async def run(self, payload: dict[str, Any], context: CandidateContext | None = None) -> dict[str, Any]:
        """Execute the engine's task and return a JSON-serializable dict."""
        raise NotImplementedError
