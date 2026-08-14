from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core import CandidateProfile
from app.db.models.resume import ResumeScore, ResumeVersion
from app.engines.resume_intelligence.schemas import RESUME_LLM_JSON_SCHEMA, ResumeLLMOutput
from app.orchestrator.engine_base import Engine
from app.orchestrator.registry import register_engine
from app.services import embedding_generator
from app.services.pii_redaction import redact_pii
from app.services.prompt_builder import PromptSpec
from app.services.resume_parser import parse_resume

SYSTEM_PROMPT = (
    "You are the Resume Intelligence Engine inside ApplyForMe's Career Command Center -- "
    "in this call you act as three experts at once: a senior technical recruiter who has "
    "screened thousands of resumes across every function (engineering, blockchain, ML, "
    "sales, design, ops...), an Applicant Tracking System parsing engine that knows exactly "
    "how automated parsers succeed or fail on real-world formatting, and a career coach who "
    "gives specific, actionable advice rather than generic platitudes. "
    "You are not a general-purpose chatbot: every score, extraction, and suggestion must be "
    "grounded in the resume text and target role given below -- never invent content that "
    "isn't there, and never let a plausible-sounding guess stand in for something you "
    "couldn't actually find in the text. "
    "Read the whole resume once, fully, before producing any field -- do not evaluate it "
    "section-by-section in isolation, because context from one part (a job title, a project "
    "description) often clarifies what belongs in another (a tag, a certification). "
    "The uploaded document is frequently NOT a clean single-candidate resume: multi-page PDFs "
    "exported from staffing/recruiting platforms often prepend a sales/cover page ('Hire "
    "Developers On-Demand', 'Trusted by...', developer/client counts) and append the agency's "
    "own marketing boilerplate ('About Us', 'Why Choose Us', pricing/engagement models, the "
    "agency's own phone/email/website, 'Our Available Technology Stack' for the whole firm). "
    "Identify and completely disregard any such agency/staffing-firm content, wherever it "
    "appears in the document -- it describes the staffing company, not the candidate, and must "
    "never contribute to tags, scores, education, certifications, or any other field."
)

BUSINESS_RULES = [
    "Do not invent skills, employers, dates, or experience that are not present in the resume text.",
    "All scores are integers 0-100 and must be internally consistent with each other and with "
    "the rest of your own output -- e.g. a resume you scored 90 on skills cannot also come "
    "with a tags list that omits half of what's in its Skills section.",
    "section_scores must have exactly these 4 keys: summary, experience, education, skills. "
    "Real resumes use inconsistent, decorated, or bulleted headings ('* Summary', 'Career "
    "Objective', 'Experience Summary' meaning an overview/qualifications-summary section (NOT "
    "the chronological job history -- classify it as summary if its content is a bulleted "
    "overview of skills/achievements rather than dated roles), 'EXPERIENCE (2020-Present)') -- "
    "read for the *content* of each section, not just a clean heading match, before concluding "
    "a section is genuinely absent and scoring it 0.",
    "weak_sections must be exactly the section_scores keys whose value is below 60 -- no more, no fewer.",
    "education and certifications must each be a distinct entry literally stated in the resume, "
    "and must come only from genuine education/credential content -- never from an adjacent "
    "project/experience block. A heading like 'Project Experience', 'Project <name>', 'Role', "
    "'Technology Stack', 'Description', or 'Key Contributions' -- even without being in any "
    "fixed list of known headings -- unambiguously starts new project/experience content and "
    "ends whatever section (e.g. Education) came before it; never let such a block's text bleed "
    "into education or certifications. A sentence that merely uses the word 'certification', "
    "'certified', or 'degree' in passing (e.g. a job duty like 'verify certifications') is not "
    "itself a credential -- do not include it. If the resume genuinely names no certification "
    "anywhere, certifications must be an empty list -- do not stretch unrelated text to fill it.",
    "tags exist to make this resume matchable against job postings, so they must cover more than "
    "technology names: also include every job title/role the candidate actually held (e.g. "
    "'Technical Lead', 'Team Lead', 'Full Stack Engineer', 'Solution Architect') exactly as used "
    "in the resume's own role/position labels, plus seniority level (e.g. 'senior', 'lead', "
    "'principal') when the resume itself uses that language -- a job posting titled 'Technical "
    "Lead' must be matchable against a resume where the candidate held that exact title. This is "
    "extracting literal titles actually held, never inventing a title that isn't in the text. "
    "Also prioritize literal, canonical technology/skill names over generic filler; a candidate "
    "with skills across unrelated domains (e.g. web, mobile, blockchain) on one resume is normal "
    "and all of it belongs in tags, not just the most recent-looking domain.",
    "total_experience_years: if no explicit date ranges are stated anywhere, but the resume "
    "states an approximate total in prose (e.g. '8+ years of experience', '10+ years in...'), "
    "use that stated figure instead of defaulting to 0 -- only use 0 if neither a date range nor "
    "a stated total appears anywhere.",
    "missing_skills should reflect skills genuinely expected for the target role/industry given, "
    "not a generic checklist unrelated to what this specific resume is trying to do.",
    "Recommendations must be specific and actionable, each referencing an exact weak section and "
    "what in that section is actually weak -- never a generic 'add more detail'.",
    "rewrite_suggestions keys must be section names drawn from the weak_sections list.",
]


class ResumeIntelligenceEngine(Engine):
    name = "resume_intelligence"
    response_schema = ResumeLLMOutput

    def gather_context(self, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = payload.get("candidate_id")
        target_role = payload.get("target_role") or ""
        target_industry = payload.get("target_industry") or ""

        if candidate_id is not None and db.get(CandidateProfile, candidate_id) is None:
            raise ValueError(f"Candidate profile {candidate_id} not found.")

        parsed = parse_resume(payload["file_bytes"], payload["filename"])
        content_hash = embedding_generator.content_hash(parsed.raw_text)

        # No candidate_id -> nothing to persist (ResumeVersion.candidate_id is a
        # NOT NULL FK) or embed for future retrieval, but the LLM analysis below
        # runs identically either way.
        resume_version_id = None
        if candidate_id is not None:
            resume_version = db.scalar(
                select(ResumeVersion)
                .where(
                    ResumeVersion.candidate_id == candidate_id,
                    ResumeVersion.content_hash == content_hash,
                )
                .order_by(ResumeVersion.id.desc())
            )
            if resume_version is None:
                # tags are filled in by postprocess() once the LLM call returns --
                # they're the LLM's output now, not available yet at this point.
                resume_version = ResumeVersion(
                    candidate_id=candidate_id,
                    file_name=payload["filename"],
                    content_hash=content_hash,
                    raw_text=parsed.raw_text,
                    sections=parsed.sections,
                    parsed_data={},
                    tags=[],
                )
                db.add(resume_version)
                db.flush()
            resume_version_id = resume_version.id
            embedding_generator.get_or_create_embedding(
                db, owner_type="resume", owner_id=resume_version.id, text=parsed.raw_text
            )

        return {
            "candidate_id": candidate_id,
            "resume_version_id": resume_version_id,
            "content_hash": content_hash,
            "target_role": target_role,
            "target_industry": target_industry,
            "redacted_resume_text": redact_pii(parsed.raw_text),
        }

    def cache_key(self, context: dict[str, Any]) -> str | None:
        # Same resume + same target role + same candidate => identical analysis;
        # skip a redundant LLM call. candidate_id (or its absence) must be part of
        # the key: a cache hit skips postprocess() entirely (see orchestrator.py),
        # so without this an anonymous ats-check call and a real candidate's
        # /analyze call for the same resume+role would return each other's cached
        # response verbatim -- wrong resumeVersionId, or a real candidate silently
        # never getting their ResumeScore row persisted.
        return (
            f"{context['candidate_id']}:{context['content_hash']}:"
            f"{context['target_role']}:{context['target_industry']}"
        )

    def build_prompt_spec(self, context: dict[str, Any]) -> PromptSpec:
        instructions = (
            "Read the resume text in `resume_text` in full, then produce a complete analysis. "
            "First, mentally separate the actual candidate content from any staffing-agency "
            "cover page or trailing marketing/boilerplate pages (per the system prompt) -- every "
            "field below is about the CANDIDATE only. "
            "Think like the three experts you are before answering each part:\n\n"
            "1. tags -- these exist to match this resume against job postings, so build them in "
            "three passes, in this priority order:\n"
            "   a) Skills/technology pass (exhaustive): every specific skill, technology, tool, "
            "framework, language, or platform literally named anywhere in the resume (a labeled "
            "Skills section is ground truth: include every item in it individually, even "
            "minor-looking ones -- don't summarize several into one broader tag). Short, "
            "canonical, industry-standard spellings.\n"
            "   b) Role/title pass: every distinct job title, position, or role the candidate "
            "actually held, taken from the resume's own role/position labels for each job or "
            "project (e.g. if an entry says 'Role: Technical Lead', the tag is 'technical lead'; "
            "if another says 'Lead Full Stack Engineer', that's a separate tag too). Include "
            "seniority words ('senior', 'lead', 'principal', 'junior') only when the resume's own "
            "wording uses them. This is extracting titles literally held -- never invent one "
            "that isn't in the text, and never collapse several distinct held titles into one. "
            "Each title tag is the bare title ONLY -- never append a project name, a project "
            "description snippet, or a page number/table artifact from the surrounding line. If "
            "the SAME title is held across multiple projects/roles, output it ONCE, not once per "
            "occurrence.\n"
            "   c) Domain pass (up to 5, lowest priority): broader domain/functional keywords "
            "describing genuinely demonstrated work (e.g. 'blockchain', 'mobile development') -- "
            "not a rephrased job title.\n"
            "One concept per tag, deduplicated, up to 50 total, passes (a) then (b) then (c) if "
            "the cap is reached. Be exhaustive and complete -- do not stop early or trim genuine "
            "items just to keep the list short; return fewer than 50 only if that's truly all "
            "that appears in the resume, and never pad to reach the cap.\n\n"
            "2. resume_score -- act as the recruiter judging the document as a whole: clarity, "
            "specificity, quantified impact, and how well it would actually persuade a hiring "
            "manager, independent of ATS mechanics.\n\n"
            "3. ats_score -- act as the ATS parsing engine. Weigh: contact info parseability "
            "(is an email/phone actually extractable), whether standard sections exist in some "
            "recognizable form, keyword/skill overlap with the target role (or general skill "
            "density if no target role was given), formatting clarity (bullets over dense "
            "paragraphs, unambiguous date ranges), and whether skills are collected in one "
            "explicit, scannable place. A resume can score high on resume_score and still score "
            "low on ats_score if a real ATS would mangle it (or vice versa) -- they measure "
            "different things and are allowed to diverge.\n\n"
            "4. section_scores -- for exactly summary, experience, education, skills: judge each "
            "section's own quality/completeness on its own merits. Before scoring any section 0, "
            "actively search the whole resume for its content under an unconventional heading, "
            "bullet/icon decoration, or a merged multi-column layout -- 0 means the content is "
            "truly not there, not that a heading didn't match a template.\n\n"
            "5. weak_sections -- exactly the section_scores keys under 60.\n\n"
            "6. total_experience_years -- act as the recruiter doing quick math: sum the actual "
            "employment date ranges stated in the resume, using the current_date given below for "
            "any 'Present'/'Current' entry. If no date range exists anywhere but the resume "
            "states an approximate total in prose (e.g. '8+ years of experience'), use that "
            "stated figure. 0 only if neither is stated anywhere.\n\n"
            "7. education -- every distinct degree/qualification literally stated, stopping at "
            "the first sign of unrelated content (a new project, a role description, an agency "
            "boilerplate line) even if that content has no clean heading of its own -- never "
            "extend an education entry into the next block just because no obvious next header "
            "appeared.\n\n"
            "8. certifications -- every distinct named credential literally stated (not a "
            "sentence merely mentioning the word, and never pulled from an unrelated project "
            "description just because it contains 'certification'); empty list if genuinely "
            "none, which is common and correct for many resumes.\n\n"
            "9. missing_skills, recommendations, rewrite_suggestions -- act as the career coach: "
            "missing_skills are what this specific target role/industry would expect but this "
            "resume doesn't show; recommendations are 3-6 specific, actionable fixes each tied to "
            "one weak section and what's actually wrong with it; rewrite_suggestions gives one "
            "concretely improved example per weak section, grounded only in what the candidate "
            "actually did (never invented achievements)."
        )
        return PromptSpec(
            system_prompt=SYSTEM_PROMPT,
            business_rules=BUSINESS_RULES,
            engine_instructions=instructions,
            json_schema=RESUME_LLM_JSON_SCHEMA,
            candidate_context={
                "target_role": context["target_role"],
                "target_industry": context["target_industry"],
                "current_date": datetime.now(timezone.utc).date().isoformat(),
            },
            extra_context={"resume_text": context["redacted_resume_text"]},
        )

    def postprocess(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
        llm_output: ResumeLLMOutput,
    ) -> dict[str, Any]:
        # No resume_version_id -> no candidate_id was given, so there's no row
        # to attach tags/a ResumeScore to (same NOT NULL FK constraint as above).
        # The response below is identical either way; only persistence differs.
        if context["resume_version_id"] is not None:
            resume_version = db.get(ResumeVersion, context["resume_version_id"])
            resume_version.tags = llm_output.tags
            db.add(resume_version)

            score_row = ResumeScore(
                resume_version_id=context["resume_version_id"],
                resume_score=llm_output.resume_score,
                ats_score=llm_output.ats_score,
                section_scores=llm_output.section_scores,
                missing_skills=llm_output.missing_skills,
                weak_sections=llm_output.weak_sections,
                recommendations=llm_output.recommendations,
                rewrite_suggestions=llm_output.rewrite_suggestions,
            )
            db.add(score_row)
            db.flush()

        return {
            "resumeVersionId": context["resume_version_id"],
            "tags": llm_output.tags,
            "resumeScore": llm_output.resume_score,
            "atsScore": llm_output.ats_score,
            "sectionScores": llm_output.section_scores,
            "weakSections": llm_output.weak_sections,
            "missingSkills": llm_output.missing_skills,
            "recommendations": llm_output.recommendations,
            "rewriteSuggestions": llm_output.rewrite_suggestions,
            "totalExperienceYears": llm_output.total_experience_years,
            "education": llm_output.education,
            "certifications": llm_output.certifications,
        }


register_engine(ResumeIntelligenceEngine())
