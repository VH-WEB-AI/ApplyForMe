"""Skill Extractor: identifies and normalises skills from free text against a taxonomy."""

import json
import re
from functools import lru_cache
from pathlib import Path

_TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "data" / "skills_taxonomy.json"


@lru_cache
def _load_taxonomy() -> dict[str, str]:
    """Returns a lookup of {normalised_alias: canonical_skill_name}."""
    raw: dict[str, list[str]] = json.loads(_TAXONOMY_PATH.read_text())
    lookup: dict[str, str] = {}
    for canonical, aliases in raw.items():
        # Deliberately NOT auto-adding canonical.lower() as an alias: some canonical
        # names (e.g. "Go") are common English words, and the taxonomy's alias list
        # (e.g. "golang") is the only safe way to match them unambiguously.
        for alias in aliases:
            lookup[alias.lower()] = canonical
    return lookup


def normalize_skill(text: str) -> str | None:
    """Maps free-text skill mention to its canonical taxonomy name, if known."""
    key = text.strip().lower()
    return _load_taxonomy().get(key)


def extract_skills(text: str) -> list[str]:
    """Finds every taxonomy skill mentioned in `text`, returning canonical names, de-duplicated."""
    lookup = _load_taxonomy()
    lower_text = text.lower()
    found: set[str] = set()

    for alias, canonical in lookup.items():
        pattern = r"(?<![a-z0-9+#.])" + re.escape(alias) + r"(?![a-z0-9+#])"
        if re.search(pattern, lower_text):
            found.add(canonical)

    return sorted(found)


def skill_gap(candidate_skills: list[str], required_skills: list[str]) -> list[str]:
    """Required skills not present among the candidate's skills (canonical comparison)."""
    candidate_set = {s.lower() for s in candidate_skills}
    missing = []
    for skill in required_skills:
        canonical = normalize_skill(skill) or skill
        if canonical.lower() not in candidate_set:
            missing.append(canonical)
    return missing
