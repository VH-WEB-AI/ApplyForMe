"""Seed a handful of sample job postings for local testing of the Job Match engine.

Usage (inside the backend container or the local venv):
    python scripts/seed_job_postings.py
"""

from app.db.base import SessionLocal
from app.db.models.jobs import JobPosting
from app.services.tag_extractor import extract_tags

SAMPLE_JOBS = [
    dict(
        title="Backend Engineer, Python",
        company="Northwind Analytics",
        description=(
            "Own core API services powering our data platform. You'll design "
            "PostgreSQL schemas, build FastAPI endpoints, and work closely with "
            "the data science team to ship ML-backed features."
        ),
        location="Austin, TX",
        remote=True,
        seniority="mid",
        salary_min=110000,
        salary_max=140000,
        visa_sponsorship=True,
        required_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs"],
        min_experience_years=3,
    ),
    dict(
        title="Senior Full-Stack Engineer",
        company="Brightloop",
        description=(
            "Lead development across our React frontend and Node.js backend. "
            "You'll mentor junior engineers and drive architecture decisions for "
            "a fast-growing fintech product."
        ),
        location="New York, NY",
        remote=False,
        seniority="senior",
        salary_min=150000,
        salary_max=190000,
        visa_sponsorship=False,
        required_skills=["React", "TypeScript", "Node.js", "AWS", "GraphQL"],
        min_experience_years=6,
    ),
    dict(
        title="Machine Learning Engineer",
        company="Vertex AI Labs",
        description=(
            "Build and deploy NLP models for our recommendation engine. Strong "
            "background in PyTorch, embeddings, and vector search required. "
            "You'll work with pgvector-backed retrieval pipelines in production."
        ),
        location="Remote",
        remote=True,
        seniority="mid",
        salary_min=130000,
        salary_max=165000,
        visa_sponsorship=True,
        required_skills=["Python", "PyTorch", "NLP", "Embeddings", "SQL"],
        min_experience_years=4,
    ),
    dict(
        title="Junior Software Developer",
        company="Cedar & Co",
        description=(
            "Great first role for someone with internship or bootcamp experience. "
            "You'll work on internal tooling in Python and JavaScript with close "
            "mentorship from senior staff."
        ),
        location="Chicago, IL",
        remote=False,
        seniority="junior",
        salary_min=70000,
        salary_max=85000,
        visa_sponsorship=False,
        required_skills=["Python", "JavaScript", "Git", "SQL"],
        min_experience_years=0,
    ),
    dict(
        title="DevOps / Platform Engineer",
        company="Hollow Peak Systems",
        description=(
            "Own our Kubernetes infrastructure and CI/CD pipelines. Experience "
            "with Terraform, AWS, and observability tooling (Prometheus/Grafana) "
            "is a must."
        ),
        location="Remote",
        remote=True,
        seniority="senior",
        salary_min=140000,
        salary_max=175000,
        visa_sponsorship=True,
        required_skills=["Kubernetes", "Terraform", "AWS", "CI/CD", "Docker"],
        min_experience_years=5,
    ),
    dict(
        title="Product Manager, AI Platform",
        company="Northwind Analytics",
        description=(
            "Drive the roadmap for our AI orchestration platform. You'll work "
            "cross-functionally with engineering, design, and data science to "
            "ship LLM-powered features."
        ),
        location="Austin, TX",
        remote=True,
        seniority="senior",
        salary_min=135000,
        salary_max=160000,
        visa_sponsorship=False,
        required_skills=["Product Strategy", "SQL", "A/B Testing", "AI/ML Literacy"],
        min_experience_years=5,
    ),
]


def seed() -> None:
    db = SessionLocal()
    try:
        existing_titles = {
            (jp.title, jp.company) for jp in db.query(JobPosting).all()
        }
        created = 0
        for job in SAMPLE_JOBS:
            key = (job["title"], job["company"])
            if key in existing_titles:
                continue
            db.add(JobPosting(**job, is_active=True, tags=extract_tags(job["description"])))
            created += 1
        db.commit()
        total = db.query(JobPosting).count()
        print(f"Inserted {created} new job posting(s). Total active+inactive rows: {total}.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
