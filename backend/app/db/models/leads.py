from datetime import date

from sqlalchemy import Boolean, Date, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin


class ResearchLead(TimestampMixin, Base):
    """One researched job-seeker lead from the Web Research SOP.

    Columns mirror the SOP's master sheet. The visa/identity signal columns are
    deliberately booleans that default to False and are only ever set True from
    text the candidate publicly stated -- the SOP forbids inferring immigration
    status or nationality from a name, university, or appearance.
    """

    __tablename__ = "research_leads"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity + contact
    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(255), default=None, index=True)
    direct_number: Mapped[str | None] = mapped_column(String(50), default=None, index=True)

    # The URL as the researcher pasted it, plus the stripped form used for dedup.
    linkedin_url: Mapped[str] = mapped_column(String(500))
    linkedin_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)

    # Location + education
    city: Mapped[str | None] = mapped_column(String(120), default=None)
    state: Mapped[str | None] = mapped_column(String(120), default=None, index=True)
    university: Mapped[str | None] = mapped_column(String(255), default=None)
    degree: Mapped[str | None] = mapped_column(String(255), default=None)
    graduation_year: Mapped[int | None] = mapped_column(Integer, default=None)

    # Role
    target_role: Mapped[str] = mapped_column(String(255), default="")
    skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_software_role: Mapped[bool] = mapped_column(Boolean, default=False)

    # Work-authorization signals -- True only when publicly stated (SOP s10)
    f1_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    stem_opt_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    cpt_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    visa_statement: Mapped[str] = mapped_column(Text, default="")

    # Job-search signals (SOP s11/s12)
    open_to_work_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    job_search_evidence: Mapped[str] = mapped_column(Text, default="")
    evidence_url: Mapped[str | None] = mapped_column(String(500), default=None)
    evidence_date: Mapped[date | None] = mapped_column(Date, default=None)

    # SOP s13 -- "yes" only on a public self-statement, otherwise "unknown".
    indian_identity: Mapped[str] = mapped_column(String(20), default="unknown")

    # Provenance (SOP s11/s12/s13 require recording where contact data came from)
    email_source: Mapped[str | None] = mapped_column(String(50), default=None)
    phone_source: Mapped[str | None] = mapped_column(String(50), default=None)
    source: Mapped[str] = mapped_column(String(50), default="LinkedIn")

    # Triage
    lead_priority: Mapped[str] = mapped_column(String(5), default="C", index=True)
    contact_status: Mapped[str] = mapped_column(String(20), default="New", index=True)
    is_rejected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    rejection_reason: Mapped[str] = mapped_column(Text, default="")

    date_researched: Mapped[date | None] = mapped_column(Date, default=None)
    researcher_name: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
