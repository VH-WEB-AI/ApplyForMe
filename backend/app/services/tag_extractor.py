"""Tag Extractor: lightweight, open-source keyphrase extraction via YAKE
(statistical, no model download, CPU-only -- see docs/SCORING_LOGIC.md).

Tags are computed once at creation time (resume upload / job ingest) and
stored on the row, so Job Match compares small precomputed tag lists on
every match instead of re-running extraction over full text each time.
"""

import yake

_EXTRACTOR = yake.KeywordExtractor(lan="en", n=2, top=30, dedupLim=0.9)


def extract_tags(text: str, max_tags: int = 15) -> list[str]:
    """Extracts up to max_tags keyphrases, most relevant first.

    YAKE's own score is inverted (lower = more relevant) -- callers never see
    it; this returns plain lowercased keyphrase strings ready for storage/
    comparison. Returns [] for empty input or if extraction ever errors out,
    since a tagging failure should degrade matching, not break resume
    upload or job ingest.
    """
    if not text or not text.strip():
        return []
    try:
        keywords = _EXTRACTOR.extract_keywords(text)
    except Exception:
        return []
    return [keyword.lower() for keyword, _score in keywords[:max_tags]]


def tag_overlap_score(resume_tags: list[str], job_tags: list[str]) -> float:
    """Fraction of the job's tags also present in the resume's tags, in [0, 1]."""
    if not job_tags:
        return 0.0
    job_set = {t.lower() for t in job_tags}
    resume_set = {t.lower() for t in resume_tags}
    return len(job_set & resume_set) / len(job_set)
