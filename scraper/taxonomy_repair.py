"""Rule-based repair for rows whose industry/category/subCategory failed the check.

categorize_jobs.py / prepare_jobs.py are keyword scorers; on federal and finance
titles they leave rows in "Other / General", or pick an industry that doesn't list
the chosen category (the full triple must exist in job_taxonomy_master.csv).
This module re-labels exactly those rows by role-title rules and guarantees that
whatever it writes is a valid triple, or leaves the row untouched for the gate to hold.

    repair_row(row) -> "ok" | "repaired" | "unresolved"

No network, no API keys. Rules are checked against the master at import time, so a
typo in a rule fails loudly instead of posting a bad label.
"""
import re

from build_taxonomy_csv import build_rows
from job_categories import classify
from job_industries import classify_industry

VALID_TRIPLES = {(r["industry"], r["category"], r["sub_category"]) for r in build_rows()}
VALID_PAIRS = {(c, s) for _, c, s in VALID_TRIPLES}
FALLBACK_INDUSTRY = "Other"      # accepts all 30 categories by design

# (title regex, category, sub-category). First match wins, so specific rules go first.
ROLE_RULES = [
    (r"\bgrc\b|security risk|risk (&|and) compliance", "Security & Safety", "Risk & Compliance"),
    (r"fire (chief|fighter|protection|department|inspector)|firefight", "Security & Safety", "Fire Safety"),
    (r"telecommunications", "Information Technology & Software", "Telecom Engineering"),
    (r"medical (technician|assistant|support)|health technician", "Healthcare & Medical", "Healthcare Support"),
    (r"^deputy director", "Operations & Administration", "General Management"),
    (r"dietitian|nutritionist", "Healthcare & Medical", "Nutrition & Dietetics"),
    (r"clinical psycholog", "Healthcare & Medical", "Behavioral Health"),
    (r"optometr|podiatr|audiolog", "Healthcare & Medical", "General Medicine"),
    (r"dentist", "Healthcare & Medical", "Dentistry"),
    (r"pharmac", "Healthcare & Medical", "Pharmacy"),
    (r"medical administration|medical records", "Healthcare & Medical", "Medical Administration"),
    (r"health system|pain (management|medicine)|health (care )?(specialist|administrator)", "Healthcare & Medical", "Healthcare Management"),
    (r"occupational health", "Security & Safety", "Occupational Health"),
    (r"\bsafety\b", "Security & Safety", "Health & Safety"),
    (r"child (and|&) youth", "Education & Training", "Early Childhood Education"),
    (r"athletic", "Education & Training", "Athletics Coaching"),
    (r"materials? handler", "Supply Chain & Logistics", "Material Management"),
    (r"data cent(er|re)", "Information Technology & Software", "Infrastructure Engineering"),
    (r"general engineer", "Engineering", "Project Engineering"),
    (r"manufacturing (test )?engineer", "Engineering", "Validation Engineering"),
    (r"quality engineer", "Manufacturing & Production", "Quality Engineering"),
    (r"personnel (services|management|specialist)", "Human Resources", "HR Operations"),
    (r"program (officer|applications?)", "Operations & Administration", "Program Management"),
    (r"\b(?<!system )(?<!database )(?<!network )(?<!systems )administrator", "Operations & Administration", "Administrative Support"),
    (r"\bsales\b", "Sales & Business Development", "Sales"),
    # --- federal / public-sector and trade titles (USAJOBS) -----------------------------------
    (r"advanced practice provider|nurse practitioner", "Healthcare & Medical", "Nurse Practitioner"),
    (r"infection prevention|\bnurse\b", "Healthcare & Medical", "Nursing"),
    (r"cytotechnolog|clinical laboratory|laboratory scientist|medical technolog", "Healthcare & Medical", "Laboratory"),
    (r"social work|social services|family therapist|mental health counselor|recreation therapist|psychiatr|behavioral health|prevention specialist|substance abuse",
     "Healthcare & Medical", "Behavioral Health"),
    (r"medical supply|sterile processing", "Healthcare & Medical", "Healthcare Support"),
    (r"archeolog|archaeolog", "Science & Research", "Social Science Research"),
    (r"geolog", "Science & Research", "Geology"),
    (r"biolog|wildlife|ecolog|botan|zoolog", "Science & Research", "Biology"),
    (r"forester|natural resource|hydrolog|science branch|environmental", "Science & Research", "Environmental Science"),
    (r"social science", "Science & Research", "Social Science Research"),
    (r"park ranger|\branger\b", "Government & Public Sector", "Government Jobs"),
    (r"recreation fee|revenue officer|contact representative|tax examiner", "Government & Public Sector", "Revenue Administration"),
    (r"passport|immigration services|citizenship|adjudicat", "Government & Public Sector", "Civil Services"),
    (r"inspector general|exam manager|\bexaminer\b|land law", "Government & Public Sector", "Regulatory Bodies"),
    (r"congressional affairs|international issues|policy analyst", "Government & Public Sector", "Public Policy"),
    (r"government information|information specialist", "Government & Public Sector", "Public Administration"),
    (r"police|containment and emergency|capitol police", "Government & Public Sector", "Police"),
    (r"force development|human intelligence|\bintelligence\b", "Government & Public Sector", "Defence"),
    (r"correctional|special investigative", "Security & Safety", "Security Operations"),
    (r"security (specialist|strategy|officer|manager)", "Security & Safety", "Security Management"),
    (r"privacy", "Legal", "Data Privacy"),
    (r"budget analyst", "Finance & Accounting", "Budgeting & Forecasting"),
    (r"accounting technician", "Finance & Accounting", "Accounting"),
    (r"m&a|mergers", "Finance & Accounting", "Mergers & Acquisitions"),
    (r"sales compensation", "Sales & Business Development", "Sales Operations"),
    (r"customer engineer|solutions? consulting", "Sales & Business Development", "Solution Consulting"),
    (r"commercial sponsorship", "Marketing & Advertising", "Brand Partnerships"),
    (r"exhibits? specialist", "Design & Creative", "Exhibition Design"),
    (r"\bcyp\b.*(child care|care assistant)|child care", "Education & Training", "Early Childhood Education"),
    (r"educational aid", "Education & Training", "Teaching"),
    (r"training and curriculum|curriculum", "Education & Training", "Curriculum Development"),
    (r"facility training|training officer", "Education & Training", "Corporate Training"),
    (r"youth director", "Education & Training", "Education Management"),
    (r"it specialist.*network|network", "Information Technology & Software", "Network Administration"),
    (r"cyber|forensic", "Information Technology & Software", "Cybersecurity"),
    (r"it specialist", "Information Technology & Software", "IT Operations"),
    (r"\bai\b|machine learning|applied scientist", "Information Technology & Software", "Artificial Intelligence & Machine Learning"),
    (r"platform (&|and) automation|av engineer", "Information Technology & Software", "Platform Engineering"),
    (r"analytics (lead|manager)|decisions (&|and) insights", "Information Technology & Software", "Data Science & Analytics"),
    (r"director of product|head of product", "Information Technology & Software", "Product Management"),
    (r"motorsport|connectivity engineer", "Engineering", "Automotive Engineering"),
    (r"waterways|civil works", "Engineering", "Civil Engineering"),
    (r"electronics technician", "Engineering", "Electronics Engineering"),
    (r"controls mechanic|industrial controls", "Manufacturing & Production", "Industrial Automation"),
    (r"air cond|air-cond|hvac", "Construction & Real Estate", "HVAC Installation"),
    (r"electrical worker|electrician", "Construction & Real Estate", "Electrical Contracting"),
    (r"equipment operator", "Construction & Real Estate", "Heavy Equipment Operation"),
    (r"waste ?water|water treatment", "Energy & Utilities", "Water Treatment"),
    (r"utility systems|utilities operator", "Energy & Utilities", "Utilities Operations"),
    (r"transmission asset|power transmission", "Energy & Utilities", "Power Transmission"),
    (r"meat ?cut", "Manufacturing & Production", "Food Production"),
    (r"laundry", "Operations & Administration", "Service Operations"),
    (r"maintenance worker|laborer|facility services|golf course|facility operations|facilities", "Operations & Administration", "Facility Management"),
    (r"administrative officer|administrative support|secretary|staff assistant|office automation", "Operations & Administration", "Administrative Support"),
    (r"operations officer|process design|operations process", "Operations & Administration", "Operations Management"),
    (r"program (analyst|manager|specialist)|management and program", "Operations & Administration", "Program Management"),
    # --- finance / tech / general --------------------------------------------------------------
    (r"broker.?dealer|securities (operations|trader)|\btrad(er|ing) operations", "Banking & Financial Services", "Broking & Trading"),
    (r"risk (officer|strateg|analyst|manager)", "Finance & Accounting", "Risk Management"),
    (r"assistant engineer|marine engineer|deckhand|\bvessel\b|\bboatswain\b", "Engineering", "Marine Engineering"),
    (r"psycholog", "Science & Research", "Social Science Research"),
    (r"counter.?intelligence|intelligence (officer|analyst|specialist)", "Government & Public Sector", "Defence"),
    (r"public affairs|public information|communications? (officer|specialist)", "Marketing & Advertising", "Public Relations"),
    (r"medical records|health information|credentialing", "Healthcare & Medical", "Medical Administration"),
    (r"veterans? service rep|claims (examiner|representative)|benefits (specialist|representative)|vocational rehabilitation",
     "Government & Public Sector", "Social Welfare"),
    (r"recreation (specialist|assistant|aid|center|manager)|youth (activities|program)|lifeguard", "Government & Public Sector", "Municipal Services"),
    (r"director.*operations|operations (director|manager|lead)", "Operations & Administration", "Operations Management"),
    (r"executive business partner|executive assistant|chief of staff", "Operations & Administration", "Executive Assistant"),
    (r"creative (director|producer)|executive producer|brand creative", "Design & Creative", "Creative Direction"),
    (r"design manager|head of design|design lead|product designer", "Design & Creative", "Product Design"),
    (r"head of product|product (manager|lead|director)", "Information Technology & Software", "Product Management"),
    (r"full.?stack", "Information Technology & Software", "Full Stack Development"),
    (r"back.?end", "Information Technology & Software", "Backend Development"),
    (r"front.?end", "Information Technology & Software", "Frontend Development"),
    (r"data scien", "Information Technology & Software", "Data Science & Analytics"),
    (r"(registered|staff|charge) nurse|\brn\b", "Healthcare & Medical", "Registered Nursing"),
    (r"nurse practitioner", "Healthcare & Medical", "Nurse Practitioner"),
    (r"physician|surgeon", "Healthcare & Medical", "Doctors"),
    (r"paralegal", "Legal", "Paralegal"),
    (r"attorney|counsel\b|lawyer", "Legal", "Legal Advisory"),
    (r"accountant|accounting", "Finance & Accounting", "Accounting"),
    (r"human resources|hr (generalist|specialist|manager)", "Human Resources", "HR Generalist"),
    (r"recruit", "Human Resources", "Recruitment"),
    (r"customer (support|service)", "Customer Service & Support", "Customer Support"),
    (r"(supply|logistics) (specialist|technician|clerk)|supply specialist", "Supply Chain & Logistics", None),
]
ROLE_RULES = [(re.compile(p, re.I), c, s) for p, c, s in ROLE_RULES]

for _, _cat, _sub in ROLE_RULES:
    if _sub is not None and (_cat, _sub) not in VALID_PAIRS:
        raise ValueError(f"taxonomy_repair rule names an unknown pair: {_cat} > {_sub}")


def _triple(row):
    return (row.get("industry", "").strip(), row.get("category", "").strip(),
            row.get("subCategory", "").strip())


def is_good(triple):
    industry, category, sub = triple
    return (triple in VALID_TRIPLES and not category.lower().startswith("other")
            and "not specified" not in sub.lower())


def _pick_label(row):
    """(category, sub) from the title rules, else the scorer on the title alone,
    else the scorer on title + description. None when nothing fits."""
    role = row.get("role") or ""
    for pattern, category, sub in ROLE_RULES:
        if pattern.search(role):
            return category, sub
    for kwargs in ({"role": role}, {"role": role, "tags": row.get("tags"),
                                    "description": (row.get("description") or "")[:1500]}):
        category, sub, _score = classify(company=row.get("company"), **kwargs)
        if not category.lower().startswith("other"):
            return category, sub
    if re.search(r"deputy director|supervisory interdisciplinary|\bdirector\b|\bchief\b", role, re.I):
        return "Operations & Administration", "General Management"
    if (row.get("platform") or "").strip() == "USAJOBS":
        return "Government & Public Sector", "Government Jobs"   # last resort: it is a federal job
    return None


def _pick_industry(row, category, sub):
    """First industry that makes the triple valid, most specific first: the
    current one (unless it is the catch-all), the company's, Government for a
    federal posting, the category's own industry when it has the same name, and
    finally the catch-all 'Other'. A category's loose default (Design ->
    Advertising) is never used: it would mislabel the employer."""
    current = row.get("industry", "").strip()
    candidates = [
        current if current != FALLBACK_INDUSTRY else None,
        classify_industry(row.get("company"), row.get("description"), category)[0],
        "Government & Public Administration" if row.get("platform") == "USAJOBS" else None,
        category if category in {i for i, _, _ in VALID_TRIPLES} else None,
        FALLBACK_INDUSTRY,
    ]
    for industry in candidates:
        if industry and (industry, category, sub) in VALID_TRIPLES:
            return industry
    return None


def repair_row(row):
    """Fixes the row in place when its triple is bad. Returns 'ok', 'repaired'
    or 'unresolved' (row untouched, so the gate holds it)."""
    if is_good(_triple(row)):
        return "ok"
    label = _pick_label(row)
    if not label:
        return "unresolved"
    category, sub = label
    if sub is None:     # rule gave only a category: take the scorer's sub under it, else skip
        scored = classify(role=row.get("role"), tags=row.get("tags"),
                          description=(row.get("description") or "")[:1500], company=row.get("company"))
        if scored[0] != category:
            return "unresolved"
        sub = scored[1]
    industry = _pick_industry(row, category, sub)
    if not industry or not is_good((industry, category, sub)):
        return "unresolved"
    row["industry"], row["category"], row["subCategory"] = industry, category, sub
    return "repaired"
