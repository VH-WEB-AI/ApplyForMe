"""Prompt Builder: assembles system prompt + business rules + candidate context +
engine instructions + JSON schema into the (system, user) prompt pair sent to the LLM."""

import json
from dataclasses import dataclass, field


@dataclass
class PromptSpec:
    system_prompt: str
    business_rules: list[str]
    engine_instructions: str
    json_schema: dict
    candidate_context: dict = field(default_factory=dict)
    extra_context: dict = field(default_factory=dict)


def build_prompt(spec: PromptSpec, *, feedback: str | None = None) -> tuple[str, str]:
    system_parts = [spec.system_prompt]
    if spec.business_rules:
        rules = "\n".join(f"- {rule}" for rule in spec.business_rules)
        system_parts.append(f"Business rules you MUST follow:\n{rules}")
    system_parts.append(
        "Never fabricate information not present in the provided context. "
        "If required context is missing, say so explicitly instead of guessing."
    )
    system_prompt = "\n\n".join(system_parts)

    user_parts = [spec.engine_instructions]
    if spec.candidate_context:
        user_parts.append(
            "Candidate context (JSON):\n" + json.dumps(spec.candidate_context, default=str)
        )
    if spec.extra_context:
        user_parts.append(
            "Additional context (JSON):\n" + json.dumps(spec.extra_context, default=str)
        )
    user_parts.append(
        "Respond with ONLY a single JSON object matching this schema (no prose, no markdown fences):\n"
        + json.dumps(spec.json_schema)
    )
    if feedback:
        user_parts.append(f"Correction needed: {feedback}")

    user_prompt = "\n\n".join(user_parts)
    return system_prompt, user_prompt
