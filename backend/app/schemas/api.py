from datetime import date

from pydantic import BaseModel, Field, field_validator
from pydantic.alias_generators import to_camel


class CreateCandidateRequest(BaseModel):
    email: str
    full_name: str = ""
    target_role: str | None = None
    target_industry: str | None = None
    experience_level: str | None = None
    visa_status: str | None = None
    location: str | None = None
    desired_salary_min: int | None = None
    desired_salary_max: int | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None


class CreateCandidateResponse(BaseModel):
    userId: int
    candidateId: int


class JobMatchRequest(BaseModel):
    candidate_id: int | None = None
    job_posting_id: int


class CopilotAskRequest(BaseModel):
    candidate_id: int | None = None
    question: str
    conversation_id: int | None = None


class JobPostingResponse(BaseModel):
    id: int
    title: str
    company: str
    location: str | None
    remote: bool
    seniority: str | None
    salary_min: int | None
    salary_max: int | None
    visa_sponsorship: bool
    required_skills: list[str]
    tags: list[str]

    model_config = {"from_attributes": True, "alias_generator": to_camel, "populate_by_name": True}


class ScrapedJobRow(BaseModel):
    """One row of the scraper's output CSV, as posted to /jobs/ingest."""

    portal: str = Field("", alias="Portal")
    job_title: str = Field("", alias="Job Title")
    company: str = Field("", alias="Company")
    url: str = Field("", alias="URL")
    about_the_job: str = Field("", alias="About the job")
    pay: str = Field("", alias="Pay")
    location: str = Field("", alias="Location")
    summary: str = Field("", alias="Summary")
    requirements: str = Field("", alias="Requirements")
    responsibilities: str = Field("", alias="Responsibilities")
    benefits: str = Field("", alias="Benefits")
    scraped_at: str = Field("", alias="Scraped At")

    model_config = {"populate_by_name": True}


class JobIngestRequest(BaseModel):
    source: str = "scraper"
    jobs: list[ScrapedJobRow]


_TRUTHY = {"yes", "y", "true", "1", "t"}


class ResearchLeadRow(BaseModel):
    """One row of the SOP's master lead sheet, as posted to /leads/ingest.

    Field aliases are the exact master-sheet column headers (SOP s20), so a CSV
    export of the sheet can be posted straight through without renaming.
    """

    first_name: str = Field("", alias="First Name")
    last_name: str = Field("", alias="Last Name")
    email: str | None = Field(None, alias="Email")
    direct_number: str | None = Field(None, alias="Direct Number")
    linkedin_url: str = Field("", alias="LinkedIn URL")

    city: str | None = Field(None, alias="City")
    state: str | None = Field(None, alias="State")
    university: str | None = Field(None, alias="University")
    degree: str | None = Field(None, alias="Degree")
    graduation_year: int | None = Field(None, alias="Graduation Year")

    target_role: str = Field("", alias="Current/Target Role")
    skills: list[str] = Field(default_factory=list, alias="Skills")

    # The candidate's own wording. Visa signals are derived from this rather
    # than trusted from the researcher's checkbox (SOP s10 / s23 rule 3).
    visa_statement: str = Field("", alias="Visa Statement")
    f1_signal: bool = Field(False, alias="F-1 Signal")
    opt_signal: bool = Field(False, alias="OPT Signal")
    stem_opt_signal: bool = Field(False, alias="STEM OPT Signal")
    cpt_signal: bool = Field(False, alias="CPT Signal")

    open_to_work_signal: bool = Field(False, alias="Open-to-Work Signal")
    job_search_evidence: str = Field("", alias="Job Search Evidence")
    evidence_url: str | None = Field(None, alias="Evidence/Post URL")
    evidence_date: date | None = Field(None, alias="Evidence Date")

    indian_identity: str = Field("unknown", alias="Indian Identity Publicly Stated")

    email_source: str | None = Field(None, alias="Email Source")
    phone_source: str | None = Field(None, alias="Phone Source")
    source: str = Field("LinkedIn", alias="Source")

    date_researched: date | None = Field(None, alias="Date Researched")
    researcher_name: str = Field("", alias="Researcher Name")
    contact_status: str = Field("New", alias="Contact Status")
    notes: str = Field("", alias="Notes")

    model_config = {"populate_by_name": True}

    @field_validator(
        "f1_signal",
        "opt_signal",
        "stem_opt_signal",
        "cpt_signal",
        "open_to_work_signal",
        mode="before",
    )
    @classmethod
    def _coerce_sheet_boolean(cls, value):
        """Sheet cells arrive as "Yes"/"No"/"" rather than JSON booleans."""
        if isinstance(value, str):
            return value.strip().lower() in _TRUTHY
        return bool(value) if value is not None else False

    @field_validator("indian_identity", mode="before")
    @classmethod
    def _coerce_identity(cls, value):
        """SOP s13: anything that is not an explicit public statement is "unknown"."""
        if not value:
            return "unknown"
        return "yes" if str(value).strip().lower() in _TRUTHY else "unknown"

    @field_validator("skills", mode="before")
    @classmethod
    def _split_skills(cls, value):
        if isinstance(value, str):
            return [s.strip() for s in value.split(",") if s.strip()]
        return value or []

    @field_validator("graduation_year", mode="before")
    @classmethod
    def _blank_year_is_none(cls, value):
        if value in ("", None):
            return None
        return value

    @field_validator("evidence_date", "date_researched", mode="before")
    @classmethod
    def _blank_date_is_none(cls, value):
        return None if value in ("", None) else value


class LeadIngestRequest(BaseModel):
    source: str = "web-research"
    leads: list[ResearchLeadRow]


class ResearchLeadResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str | None
    direct_number: str | None
    linkedin_url: str
    city: str | None
    state: str | None
    university: str | None
    degree: str | None
    graduation_year: int | None
    target_role: str
    skills: list[str]
    is_software_role: bool
    f1_signal: bool
    opt_signal: bool
    stem_opt_signal: bool
    cpt_signal: bool
    open_to_work_signal: bool
    indian_identity: str
    evidence_url: str | None
    evidence_date: date | None
    email_source: str | None
    phone_source: str | None
    lead_priority: str
    contact_status: str
    is_rejected: bool
    rejection_reason: str
    date_researched: date | None
    researcher_name: str

    model_config = {"from_attributes": True, "alias_generator": to_camel, "populate_by_name": True}


class LeadBulkEnrichRequest(BaseModel):
    """Lead ids to enrich in one pass. Apollo batches ten per call internally."""

    lead_ids: list[int]


class LeadStatusUpdateRequest(BaseModel):
    contact_status: str
    notes: str | None = None
