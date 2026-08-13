"""
Request Classifier: decides which engine(s) an incoming orchestrator
request should be routed to, based on an explicit `intent` field or,
absent that, simple keyword/context heuristics.
"""
from enum import Enum


class EngineType(str, Enum):
    RESUME_INTELLIGENCE = "resume_intelligence"
    JOB_MATCH = "job_match"
    CAREER_HEALTH = "career_health"
    CAREER_COPILOT = "career_copilot"


_INTENT_MAP = {
    "score_resume": EngineType.RESUME_INTELLIGENCE,
    "parse_resume": EngineType.RESUME_INTELLIGENCE,
    "match_jobs": EngineType.JOB_MATCH,
    "explain_match": EngineType.JOB_MATCH,
    "career_health": EngineType.CAREER_HEALTH,
    "chat": EngineType.CAREER_COPILOT,
}


class RequestClassifier:
    def classify(self, intent: str) -> EngineType:
        engine = _INTENT_MAP.get(intent)
        if engine is None:
            raise ValueError(f"Unknown orchestrator intent: '{intent}'")
        return engine


request_classifier = RequestClassifier()
