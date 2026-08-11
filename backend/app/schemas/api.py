from pydantic import BaseModel, Field
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
    candidate_id: int
    job_posting_id: int


class CopilotAskRequest(BaseModel):
    candidate_id: int
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
