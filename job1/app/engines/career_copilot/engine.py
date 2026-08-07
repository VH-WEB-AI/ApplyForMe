"""
Engine 4: Career Copilot
Intent detection, retrieve relevant context (RAG), generate personalized
answers, actionable guidance, conversation memory.
"""
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.base_engine import BaseEngine
from app.engines.career_copilot.rag import conversation_rag
from app.engines.career_copilot.resume_rag import resume_rag
from app.orchestrator.context_manager import CandidateContext
from app.shared_services.llm_client import llm_client
from app.shared_services.prompt_builder import COPILOT_SYSTEM_PROMPT


class CopilotOutput(BaseModel):
    reply: str
    suggested_actions: list[str] = Field(default_factory=list)


class CareerCopilotEngine(BaseEngine):
    name = "career_copilot"

    async def run(self, payload: dict[str, Any], context: CandidateContext | None = None) -> dict[str, Any]:
        db: AsyncSession = payload["db"]
        user_id: uuid.UUID = payload["user_id"]
        user_message: str = payload["message"]
        conversation_history: str = payload.get("conversation_history", "")

        is_resume_query = resume_rag.is_resume_query(user_message)
        resume_chunks = await resume_rag.retrieve_resume_chunks(db, user_id, user_message)
        conversation_context = (
            [] if is_resume_query else await conversation_rag.retrieve_relevant_context(db, user_id, user_message)
        )
        retrieved_context = self._format_retrieved_context(resume_chunks, conversation_context)

        candidate_context = context.as_prompt_summary() if context else "No profile on file yet."
        detailed_response = self._wants_detailed_response(user_message)

        messages = COPILOT_SYSTEM_PROMPT.render(
            candidate_context=candidate_context,
            retrieved_context=retrieved_context,
            conversation_history="(ignored for latest-resume question)" if is_resume_query else conversation_history or "(new conversation)",
            response_instructions=self._response_instructions(user_message, detailed_response),
            user_message=user_message,
        )

        response = await llm_client.chat_completion(
            messages,
            temperature=0.25,
            max_tokens=900 if detailed_response else 420,
        )
        reply = self._normalize_markdown_reply(response.content)

        return {
            "result": {"reply": reply, "suggested_actions": self._suggest_actions(user_message, reply)},
            "usage": {
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "latency_ms": response.latency_ms,
            },
        }

    def _format_retrieved_context(self, resume_chunks: list[dict[str, Any]], conversation_context: list[str]) -> str:
        sections: list[str] = []

        if resume_chunks:
            resume_lines = []
            for chunk in resume_chunks:
                skills = ", ".join(chunk.get("skills") or []) or "n/a"
                suggestions = "; ".join(chunk.get("suggestions") or []) or "n/a"
                resume_lines.append(
                    "\n".join(
                        [
                            f"Resume: {chunk.get('file_name')} ({chunk.get('resume_id')})",
                            "Priority: latest uploaded resume context",
                            f"Status: {chunk.get('status', 'n/a')}",
                            f"ATS score: {chunk.get('ats_score', 'n/a')}; Resume score: {chunk.get('resume_score', 'n/a')}",
                            f"Extracted skills: {skills}",
                            f"Stored resume suggestions: {suggestions}",
                            f"Relevant text:\n{chunk.get('text', '')}",
                        ]
                    )
                )
            sections.append("Relevant resume context:\n" + "\n\n".join(resume_lines))

        if conversation_context:
            sections.append("Relevant conversation memory:\n" + "\n".join(conversation_context))

        return "\n\n".join(sections) if sections else "No relevant resume or prior conversation context found."

    def _wants_detailed_response(self, user_message: str) -> bool:
        text = user_message.lower()
        return any(
            phrase in text
            for phrase in [
                "explain in detail",
                "detailed",
                "deep dive",
                "rewrite",
                "write bullets",
                "improve these bullets",
                "full analysis",
                "step by step",
            ]
        )

    def _response_instructions(self, user_message: str, detailed_response: bool) -> str:
        factuality_rules = (
            "Use only the retrieved resume context for factual statements. "
            "Never invent achievements, percentages, experience, companies, tools, or technologies. "
            "If a requested fact is unavailable, write exactly: 'Information not found in the uploaded resume.' "
            "For resume weakness or improvement questions, prioritize issues supported by the resume text, "
            "scores, extracted skills, or stored resume suggestions. Do not claim common sections are missing "
            "unless the retrieved context is sufficient to verify the absence across the resume."
        )
        markdown_rules = (
            "IMPORTANT: Your response MUST be valid Markdown. "
            "Use plain section titles without # or ## characters. "
            "After every section title, insert one blank line. "
            "Every bullet starts with '- '. Every bullet must be on its own line. "
            "Never put a section title and the first bullet on the same line. "
            "Never write multiple bullets in one paragraph. Never use inline bullets. "
            "Do not output any introductory sentence. "
        )
        if self._is_weak_area_query(user_message):
            return (
                f"{markdown_rules}"
                "Required response format exactly:\n"
                "Resume Weak Areas\n\n"
                "- Missing professional summary.\n"
                "- Contact information is incomplete.\n\n"
                "Recommendations\n\n"
                "- Add a professional summary.\n"
                "- Include measurable achievements.\n\n"
                "Use the section headings 'Resume Weak Areas' and 'Recommendations'. "
                "Use 3-5 bullets per section when supported by resume context. "
                f"{factuality_rules}"
            )
        if detailed_response:
            return (
                "The user asked for detail or rewriting, but still keep the response concise and resume-grounded. "
                f"{markdown_rules}"
                f"{factuality_rules}"
            )
        return (
            f"{markdown_rules}"
            "Keep each bullet under 20 words; "
            "do not use numbered lists unless explicitly requested; never return JSON unless the user asks for it. "
            "Required response format:\n"
            "<Section Title>\n\n"
            "- Point 1\n"
            "- Point 2\n"
            "- Point 3\n\n"
            "<Next Section>\n\n"
            "- Point 1\n"
            "- Point 2\n"
            "- Point 3\n\n"
            "Rules: no introduction; no conclusion; no unnecessary explanations; highlight only actionable points. "
            f"{factuality_rules}"
        )

    def _is_weak_area_query(self, user_message: str) -> bool:
        terms = set(re.findall(r"[a-zA-Z0-9+#.]+", user_message.lower()))
        return bool(terms & {"weak", "weakness", "weaknesses", "gap", "gaps", "improve", "improvement"})

    def _normalize_markdown_reply(self, reply: str) -> str:
        normalized = (reply or "").strip()
        if not normalized:
            return normalized

        normalized = re.sub(r"\s+(##\s+)", r"\n\n\1", normalized)
        normalized = re.sub(r"(?m)^(##\s+[^-\n]+?)\s+-\s+", r"\1\n\n- ", normalized)
        normalized = re.sub(r"(?<!\n)\s+-\s+", "\n- ", normalized)
        normalized = re.sub(r"(?m)^(##\s+.+)\n(?!\n)", r"\1\n\n", normalized)
        normalized = re.sub(r"(?m)^#{1,6}\s*", "", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def _suggest_actions(self, user_message: str, reply: str) -> list[str]:
        text = f"{user_message} {reply}".lower()
        actions: list[str] = []

        if any(term in text for term in ["resume", "ats", "keyword", "bullet"]):
            actions.append("Review resume suggestions")
        if any(term in text for term in ["job", "match", "role", "description"]):
            actions.append("Run job match")
        if any(term in text for term in ["interview", "prep", "question"]):
            actions.append("Create interview prep plan")
        if any(term in text for term in ["follow", "application", "progress"]):
            actions.append("Track application progress")

        if not actions:
            actions.append("Update candidate context")
        return actions[:3]


career_copilot_engine = CareerCopilotEngine()
