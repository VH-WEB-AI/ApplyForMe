"""Infers the employer's industry — the third axis of the taxonomy.

Industry is a property of the COMPANY, not of the job title: a payroll analyst
at Walmart is Retail, the identical role at Pfizer is Pharmaceuticals. So the
signal order is company name first, then industry words in the posting, and only
then a fallback derived from the job's own category.

Resolution order:
  1. COMPANY_INDUSTRY — known employers, matched on a name fragment.
  2. INDUSTRY_KEYWORDS — sector words in the company name or the opening of the
     description ("our hospital", "dealership", "underwriting").
  3. CATEGORY_DEFAULT_INDUSTRY — the industry a category implies when the
     employer is unknown; a Nursing job is Healthcare wherever it was posted.
  4. "Other" — industry 48, which accepts every category by design.
"""
import re

from taxonomy_master import INDUSTRIES

# Fragment -> industry. Matched against a normalised company name, longest
# fragment first, so "liberty mutual insurance" beats a bare "mutual".
COMPANY_INDUSTRY = {
    # Retail / grocery / e-commerce
    "walmart": "Retail", "sam's club": "Retail", "kroger": "Retail",
    "tjx": "Retail", "t.j. maxx": "Retail", "marshalls": "Retail",
    "kohl's": "Retail", "target": "Retail", "costco": "Retail",
    "dollar general": "Retail", "dollar tree": "Retail", "best buy": "Retail",
    "home depot": "Retail", "lowe's": "Retail", "gap inc": "Retail",
    "nordstrom": "Retail", "macy's": "Retail", "publix": "Retail",
    "albertsons": "Retail", "safeway": "Retail", "aldi": "Retail",
    "trader joe": "Retail", "whole foods": "Retail", "wegmans": "Retail",
    "amazon": "E-commerce", "ebay": "E-commerce", "shopify": "E-commerce",
    "etsy": "E-commerce", "wayfair": "E-commerce", "instacart": "E-commerce",
    "doordash": "E-commerce", "chewy": "E-commerce",
    # Healthcare / pharmacy / labs
    "cvs health": "Healthcare & Medical", "walgreens": "Healthcare & Medical",
    "rite aid": "Healthcare & Medical", "labcorp": "Healthcare & Medical",
    "quest diagnostics": "Healthcare & Medical", "davita": "Healthcare & Medical",
    "hca healthcare": "Healthcare & Medical", "kaiser": "Healthcare & Medical",
    "cleveland clinic": "Healthcare & Medical", "mayo clinic": "Healthcare & Medical",
    "unitedhealth": "Healthcare & Medical", "optum": "Healthcare & Medical",
    "elevance": "Healthcare & Medical", "cigna": "Healthcare & Medical",
    "humana": "Healthcare & Medical", "encompass health": "Healthcare & Medical",
    "bd": "Healthcare & Medical", "becton": "Healthcare & Medical",
    "medtronic": "Healthcare & Medical", "baxter": "Healthcare & Medical",
    "stryker": "Healthcare & Medical", "boston scientific": "Healthcare & Medical",
    # Pharma / biotech
    "pfizer": "Pharmaceuticals", "merck": "Pharmaceuticals",
    "johnson & johnson": "Pharmaceuticals", "abbvie": "Pharmaceuticals",
    "eli lilly": "Pharmaceuticals", "novartis": "Pharmaceuticals",
    "astrazeneca": "Pharmaceuticals", "gsk": "Pharmaceuticals",
    "sanofi": "Pharmaceuticals", "bristol myers": "Pharmaceuticals",
    "teva": "Pharmaceuticals", "viatris": "Pharmaceuticals",
    "amgen": "Biotechnology", "genentech": "Biotechnology",
    "moderna": "Biotechnology", "regeneron": "Biotechnology",
    "gilead": "Biotechnology", "thermo fisher": "Biotechnology",
    "illumina": "Biotechnology", "biogen": "Biotechnology",
    # Banking / financial services / insurance / fintech
    "jpmorgan": "Banking & Financial Services", "chase": "Banking & Financial Services",
    "bank of america": "Banking & Financial Services", "wells fargo": "Banking & Financial Services",
    "citi": "Banking & Financial Services", "td bank": "Banking & Financial Services",
    "pnc": "Banking & Financial Services", "capital one": "Banking & Financial Services",
    "u.s. bank": "Banking & Financial Services", "truist": "Banking & Financial Services",
    "goldman sachs": "Banking & Financial Services", "morgan stanley": "Banking & Financial Services",
    "fidelity investments": "Banking & Financial Services", "charles schwab": "Banking & Financial Services",
    "blackrock": "Banking & Financial Services", "vanguard": "Banking & Financial Services",
    "fisher investments": "Banking & Financial Services", "icapital": "Banking & Financial Services",
    "american express": "Banking & Financial Services", "discover financial": "Banking & Financial Services",
    "allstate": "Insurance", "liberty mutual": "Insurance", "state farm": "Insurance",
    "geico": "Insurance", "progressive insurance": "Insurance", "farmers insurance": "Insurance",
    "nationwide": "Insurance", "travelers": "Insurance", "aflac": "Insurance",
    "metlife": "Insurance", "prudential": "Insurance", "aon": "Insurance",
    "marsh": "Insurance", "insurance broker": "Insurance",
    "adyen": "FinTech", "stripe": "FinTech", "paypal": "FinTech",
    "block, inc": "FinTech", "visa": "FinTech", "mastercard": "FinTech",
    "fiserv": "FinTech", "fis": "FinTech", "plaid": "FinTech", "coinbase": "FinTech",
    # Technology
    "apple": "Information Technology & Software", "microsoft": "Information Technology & Software",
    "google": "Information Technology & Software", "alphabet": "Information Technology & Software",
    "ibm": "Information Technology & Software", "oracle": "Information Technology & Software",
    "salesforce": "Information Technology & Software", "sap": "Information Technology & Software",
    "adobe": "Information Technology & Software", "intuit": "Information Technology & Software",
    "workday": "Information Technology & Software", "servicenow": "Information Technology & Software",
    "infosys": "Information Technology & Software", "tcs": "Information Technology & Software",
    "tata consultancy": "Information Technology & Software", "wipro": "Information Technology & Software",
    "cognizant": "Information Technology & Software", "accenture": "Consulting",
    "hcl": "Information Technology & Software", "tech mahindra": "Information Technology & Software",
    "siemens digital industries software": "Information Technology & Software",
    "dell": "Electronics & Hardware", "hp inc": "Electronics & Hardware",
    "hewlett packard": "Electronics & Hardware", "cisco": "Electronics & Hardware",
    "intel": "Electronics & Hardware", "nvidia": "Electronics & Hardware",
    "amd": "Electronics & Hardware", "qualcomm": "Electronics & Hardware",
    "texas instruments": "Electronics & Hardware", "applied materials": "Electronics & Hardware",
    "jabil": "Electronics & Hardware", "foxconn": "Electronics & Hardware",
    "meta": "Media & Entertainment", "tiktok": "Media & Entertainment",
    "bytedance": "Media & Entertainment", "netflix": "Media & Entertainment",
    "disney": "Media & Entertainment", "warner bros": "Media & Entertainment",
    "paramount": "Media & Entertainment", "comcast": "Media & Entertainment",
    "nbcuniversal": "Media & Entertainment", "spotify": "Media & Entertainment",
    "cinemark": "Media & Entertainment", "amc theatres": "Media & Entertainment",
    "snap inc": "Media & Entertainment", "pinterest": "Media & Entertainment",
    "electronic arts": "Gaming", "activision": "Gaming", "ubisoft": "Gaming",
    "riot games": "Gaming", "epic games": "Gaming", "roblox": "Gaming",
    "crowdstrike": "Cybersecurity", "palo alto networks": "Cybersecurity",
    "fortinet": "Cybersecurity", "okta": "Cybersecurity", "zscaler": "Cybersecurity",
    "openai": "Artificial Intelligence & Machine Learning",
    "anthropic": "Artificial Intelligence & Machine Learning",
    "databricks": "Artificial Intelligence & Machine Learning",
    "scale ai": "Artificial Intelligence & Machine Learning",
    "verizon": "Telecommunications", "at&t": "Telecommunications",
    "t-mobile": "Telecommunications", "spectrum": "Telecommunications",
    "charter communications": "Telecommunications", "lumen": "Telecommunications",
    # Industrial / energy / auto / aero
    "northrop grumman": "Aviation & Aerospace", "lockheed martin": "Aviation & Aerospace",
    "raytheon": "Aviation & Aerospace", "rtx": "Aviation & Aerospace",
    "boeing": "Aviation & Aerospace", "airbus": "Aviation & Aerospace",
    "general dynamics": "Defence & Security", "l3harris": "Defence & Security",
    "leidos": "Defence & Security", "booz allen": "Defence & Security",
    "delta air lines": "Aviation & Aerospace", "united airlines": "Aviation & Aerospace",
    "american airlines": "Aviation & Aerospace", "southwest airlines": "Aviation & Aerospace",
    "ford motor": "Automotive", "general motors": "Automotive", "tesla": "Automotive",
    "toyota": "Automotive", "honda": "Automotive", "stellantis": "Automotive",
    "rivian": "Automotive", "autozone": "Automotive", "o'reilly auto": "Automotive",
    "penske": "Automotive", "carmax": "Automotive", "enterprise mobility": "Transportation",
    "hertz": "Transportation", "avis": "Transportation", "uber": "Transportation",
    "lyft": "Transportation", "ups": "Logistics & Supply Chain",
    "fedex": "Logistics & Supply Chain", "xpo": "Logistics & Supply Chain",
    "c.h. robinson": "Logistics & Supply Chain", "dhl": "Logistics & Supply Chain",
    "ryder": "Logistics & Supply Chain", "mclane": "Logistics & Supply Chain",
    "sonepar": "Logistics & Supply Chain", "ferguson": "Logistics & Supply Chain",
    "caterpillar": "Manufacturing", "deere": "Manufacturing", "3m": "Manufacturing",
    "honeywell": "Manufacturing", "general electric": "Manufacturing",
    "ge vernova": "Manufacturing", "eaton": "Manufacturing", "cummins": "Manufacturing",
    "emerson": "Manufacturing", "parker hannifin": "Manufacturing",
    "schneider electric": "Manufacturing", "siemens": "Manufacturing",
    "abb": "Manufacturing", "whirlpool": "Manufacturing", "bosch": "Manufacturing",
    "dow": "Chemicals", "dupont": "Chemicals", "basf": "Chemicals",
    "linde": "Chemicals", "ppg": "Chemicals", "sherwin-williams": "Chemicals",
    "exxonmobil": "Oil, Gas & Energy", "chevron": "Oil, Gas & Energy",
    "shell": "Oil, Gas & Energy", "bp": "Oil, Gas & Energy",
    "conocophillips": "Oil, Gas & Energy", "halliburton": "Oil, Gas & Energy",
    "schlumberger": "Oil, Gas & Energy", "slb": "Oil, Gas & Energy",
    "duke energy": "Oil, Gas & Energy", "nextera": "Renewable Energy",
    "first solar": "Renewable Energy", "vestas": "Renewable Energy",
    "sunrun": "Renewable Energy", "freeport-mcmoran": "Mining & Metals",
    "nucor": "Mining & Metals", "alcoa": "Mining & Metals",
    "crh": "Construction", "vulcan materials": "Construction",
    "martin marietta": "Construction", "turner construction": "Construction",
    "bechtel": "Construction", "fluor": "Construction", "aecom": "Engineering",
    "jacobs": "Engineering", "wsp": "Engineering", "stantec": "Engineering",
    # Consumer goods / food / hospitality
    "procter & gamble": "Consumer Goods / FMCG", "unilever": "Consumer Goods / FMCG",
    "nestle": "Consumer Goods / FMCG", "pepsico": "Consumer Goods / FMCG",
    "coca-cola": "Consumer Goods / FMCG", "kraft heinz": "Consumer Goods / FMCG",
    "general mills": "Consumer Goods / FMCG", "kellogg": "Consumer Goods / FMCG",
    "mondelez": "Consumer Goods / FMCG", "colgate": "Consumer Goods / FMCG",
    "kimberly-clark": "Consumer Goods / FMCG", "cargill": "Food & Beverage",
    "tyson foods": "Food & Beverage", "sysco": "Food & Beverage",
    "us foods": "Food & Beverage", "sodexo": "Food & Beverage",
    "aramark": "Food & Beverage", "compass group": "Food & Beverage",
    "mcdonald": "Food & Beverage", "starbucks": "Food & Beverage",
    "chipotle": "Food & Beverage", "yum! brands": "Food & Beverage",
    "darden": "Food & Beverage", "panera": "Food & Beverage",
    "marriott": "Hospitality", "hilton": "Hospitality", "hyatt": "Hospitality",
    "ihg": "Hospitality", "wyndham": "Hospitality", "mgm resorts": "Hospitality",
    "caesars": "Hospitality", "airbnb": "Travel & Tourism",
    "booking.com": "Travel & Tourism", "expedia": "Travel & Tourism",
    "carnival": "Travel & Tourism", "royal caribbean": "Travel & Tourism",
    "nike": "Fashion & Apparel", "adidas": "Fashion & Apparel",
    "lululemon": "Fashion & Apparel", "levi": "Fashion & Apparel",
    "vf corporation": "Fashion & Apparel", "ralph lauren": "Fashion & Apparel",
    "sephora": "Beauty & Wellness", "ulta": "Beauty & Wellness",
    "l'oreal": "Beauty & Wellness", "estee lauder": "Beauty & Wellness",
    "planet fitness": "Sports & Fitness", "equinox": "Sports & Fitness",
    # Services / professional / public / education
    "deloitte": "Consulting", "pwc": "Consulting", "kpmg": "Consulting",
    "ernst & young": "Consulting", "mckinsey": "Consulting", "bain": "Consulting",
    "boston consulting": "Consulting", "capgemini": "Consulting",
    "robert half": "Human Resources & Recruitment", "randstad": "Human Resources & Recruitment",
    "adecco": "Human Resources & Recruitment", "manpower": "Human Resources & Recruitment",
    "kelly services": "Human Resources & Recruitment", "aerotek": "Human Resources & Recruitment",
    "adp": "Human Resources & Recruitment", "paychex": "Human Resources & Recruitment",
    "kindercare": "Education & EdTech", "bright horizons": "Education & EdTech",
    "chegg": "Education & EdTech", "coursera": "Education & EdTech",
    "pearson": "Education & EdTech", "2u": "Education & EdTech",
    "realtor.com": "Real Estate", "zillow": "Real Estate", "cbre": "Real Estate",
    "jll": "Real Estate", "keller williams": "Real Estate", "re/max": "Real Estate",
    "rentokil": "Environmental Services", "terminix": "Environmental Services",
    "waste management": "Environmental Services", "republic services": "Environmental Services",
    "veolia": "Environmental Services", "ecolab": "Environmental Services",
    "allied universal": "Defence & Security", "securitas": "Defence & Security",
    "red cross": "Non-Profit & Social Services", "goodwill": "Non-Profit & Social Services",
    "united way": "Non-Profit & Social Services", "ymca": "Non-Profit & Social Services",
    "habitat for humanity": "Non-Profit & Social Services",
}

# Sector words, checked in the company name and the opening of the description
# when the employer isn't in the map above.
INDUSTRY_KEYWORDS = {
    "Healthcare & Medical": ["hospital", "health system", "clinic", "medical center", "nursing home", "home health", "healthcare provider", "patient care", "dental practice"],
    "Pharmaceuticals": ["pharmaceutical company", "drug development", "pharma"],
    "Biotechnology": ["biotech", "life sciences company", "genomics"],
    "Banking & Financial Services": ["bank", "credit union", "wealth management firm", "brokerage"],
    "Insurance": ["insurance", "underwriting", "policyholder", "claims department"],
    "FinTech": ["fintech", "payments platform", "digital wallet"],
    "Retail": ["retail chain", "our stores", "store team", "supermarket", "grocery store", "department store"],
    "E-commerce": ["e-commerce", "ecommerce", "online marketplace", "fulfillment center"],
    "Education & EdTech": ["school district", "university", "college", "academy", "edtech", "learning center", "childcare center"],
    "Manufacturing": ["manufacturing plant", "our factory", "production facility", "industrial manufacturer"],
    "Automotive": ["dealership", "automotive group", "auto service center", "vehicle service"],
    "Construction": ["construction company", "general contractor", "building contractor", "job site"],
    "Real Estate": ["real estate", "property management company", "brokerage firm real estate"],
    "Engineering": ["engineering firm", "engineering services"],
    "Telecommunications": ["telecom", "wireless carrier", "broadband provider"],
    "Media & Entertainment": ["media company", "broadcast network", "streaming service", "film studio", "newsroom", "theatre", "theater"],
    "Advertising & Marketing": ["advertising agency", "marketing agency", "creative agency", "media agency"],
    "Travel & Tourism": ["travel agency", "tour operator", "cruise line"],
    "Hospitality": ["hotel", "resort", "casino", "hospitality group"],
    "Food & Beverage": ["restaurant", "food service", "catering", "brewery", "food manufacturer", "quick service"],
    "Agriculture & Agribusiness": ["farm", "agriculture", "agribusiness", "crop", "livestock"],
    "Logistics & Supply Chain": ["logistics", "freight", "distribution center", "3pl", "supply chain company"],
    "Transportation": ["trucking", "transit authority", "rental car", "rideshare", "fleet operator"],
    "Aviation & Aerospace": ["airline", "airport", "aerospace", "aircraft"],
    "Oil, Gas & Energy": ["oil and gas", "refinery", "utility company", "power plant", "pipeline"],
    "Renewable Energy": ["solar", "wind farm", "renewable energy"],
    "Government & Public Administration": ["city of", "county of", "state agency", "federal agency", "public sector", "municipality"],
    "Legal Services": ["law firm", "legal practice", "attorneys at law"],
    "Consulting": ["consulting firm", "advisory firm", "professional services firm"],
    "Human Resources & Recruitment": ["staffing agency", "recruitment firm", "talent solutions"],
    "Architecture & Interior Design": ["architecture firm", "design studio", "interior design firm"],
    "Fashion & Apparel": ["apparel", "fashion brand", "clothing brand"],
    "Beauty & Wellness": ["salon", "spa", "beauty brand", "cosmetics"],
    "Consumer Goods / FMCG": ["consumer packaged goods", "cpg", "fmcg"],
    "Chemicals": ["chemical company", "specialty chemicals"],
    "Mining & Metals": ["mining", "steel mill", "smelter", "quarry"],
    "Electronics & Hardware": ["semiconductor", "electronics manufacturer", "hardware company"],
    "Cybersecurity": ["cybersecurity company", "security software"],
    "Research & Development": ["research institute", "national laboratory", "r&d center"],
    "Non-Profit & Social Services": ["nonprofit", "non-profit", "ngo", "charity", "social services"],
    "Defence & Security": ["defense contractor", "defence", "security services company", "armed forces"],
    "Sports & Fitness": ["fitness center", "gym", "sports team", "athletic club"],
    "Gaming": ["game studio", "video game"],
    "Events & Entertainment": ["event management company", "entertainment venue", "live events"],
    "Environmental Services": ["waste management", "recycling", "environmental services", "pest control"],
}

# The industry a category implies when the employer is unknown. Only categories
# that genuinely pin down a sector appear here — Finance, HR and Sales exist
# everywhere and are deliberately absent.
CATEGORY_DEFAULT_INDUSTRY = {
    "Information Technology & Software": "Information Technology & Software",
    "Healthcare & Medical": "Healthcare & Medical",
    "Pharmaceutical & Biotechnology": "Pharmaceuticals",
    "Education & Training": "Education & EdTech",
    "Retail & E-commerce": "Retail",
    "Manufacturing & Production": "Manufacturing",
    "Engineering": "Engineering",
    "Construction & Real Estate": "Construction",
    "Architecture & Planning": "Architecture & Interior Design",
    "Hospitality & Travel": "Hospitality",
    "Supply Chain & Logistics": "Logistics & Supply Chain",
    "Automotive": "Automotive",
    "Aviation & Aerospace": "Aviation & Aerospace",
    "Energy & Utilities": "Oil, Gas & Energy",
    "Banking & Financial Services": "Banking & Financial Services",
    "Media & Entertainment": "Media & Entertainment",
    "Marketing & Advertising": "Advertising & Marketing",
    "Design & Creative": "Advertising & Marketing",
    "Agriculture & Farming": "Agriculture & Agribusiness",
    "Science & Research": "Research & Development",
    "Government & Public Sector": "Government & Public Administration",
    "Legal": "Legal Services",
    "Consulting": "Consulting",
    "Human Resources": "Human Resources & Recruitment",
    "Security & Safety": "Defence & Security",
}

FALLBACK_INDUSTRY = "Other"
DESCRIPTION_SCAN_CHARS = 800

# Longest fragment first so a specific employer never loses to a generic one.
_COMPANY_FRAGMENTS = sorted(COMPANY_INDUSTRY, key=len, reverse=True)

_PATTERN_CACHE = {}


def _pattern(phrase):
    """Word-boundary match. Plain substring matching filed a DevSecOps job under
    Beauty & Wellness because "spa" occurs inside "spatial" — and "bp", "3m",
    "bd" and "fis" are just as happy to fire inside longer words."""
    if phrase not in _PATTERN_CACHE:
        escaped = re.escape(phrase)
        left = r"\b" if phrase[:1].isalnum() else ""
        right = r"\b" if phrase[-1:].isalnum() else ""
        _PATTERN_CACHE[phrase] = re.compile(left + escaped + right)
    return _PATTERN_CACHE[phrase]


def _normalize(value):
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9&'.\- ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_industry(company="", description="", category=""):
    """Returns (industry, source) where source says which rule decided it —
    useful when reviewing a batch, because "company" matches are trustworthy and
    "category" matches are only a guess."""
    company_text = _normalize(company)
    if company_text:
        for fragment in _COMPANY_FRAGMENTS:
            if _pattern(fragment).search(company_text):
                return COMPANY_INDUSTRY[fragment], "company"

    haystack = company_text + " " + _normalize(description)[:DESCRIPTION_SCAN_CHARS]
    best = (0, None)
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        for keyword in keywords:
            if _pattern(keyword).search(haystack):
                # Longer phrases win: "home health" should beat "health system"
                # only when it is the phrase actually present.
                score = len(keyword.split())
                if score > best[0]:
                    best = (score, industry)
    if best[1]:
        return best[1], "keyword"

    if category in CATEGORY_DEFAULT_INDUSTRY:
        return CATEGORY_DEFAULT_INDUSTRY[category], "category"

    return FALLBACK_INDUSTRY, "fallback"


def industry_id(industry):
    entry = INDUSTRIES.get(industry)
    return entry[0] if entry else INDUSTRIES[FALLBACK_INDUSTRY][0]
