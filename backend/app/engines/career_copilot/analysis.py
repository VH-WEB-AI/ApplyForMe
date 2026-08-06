"""Deterministic intent detection for the Career Copilot: a cheap keyword heuristic
that avoids spending an LLM call just to classify what the user is asking about."""

_INTENT_KEYWORDS = {
    "explain_resume_score": ["resume score", "ats score", "ats compatib", "resume rating"],
    "explain_career_health": ["career health", "readiness", "overall score"],
    "explain_job_match": ["match score", "why this job", "job match", "fit for"],
    "application_status": ["application", "applied", "applications"],
    "interview_prep": ["interview"],
    "recommendation": ["recommend", "suggest", "should i", "advice", "improve"],
}

RESUME_VERSION_CHUNK_RANGE_SIZE = 1000


def resume_chunk_owner_id(resume_version_id: int, chunk_index: int) -> int:
    return resume_version_id * RESUME_VERSION_CHUNK_RANGE_SIZE + chunk_index


def resume_chunk_owner_id_range(resume_version_id: int) -> tuple[int, int]:
    base = resume_version_id * RESUME_VERSION_CHUNK_RANGE_SIZE
    return base, base + RESUME_VERSION_CHUNK_RANGE_SIZE - 1


def detect_intent(question: str) -> str:
    lower = question.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            return intent
    return "general_advice"
