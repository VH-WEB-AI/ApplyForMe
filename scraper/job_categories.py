"""Job category taxonomy (30 categories / ~330 sub-categories) and a rule-based
classifier that labels a job row with one `category` + `subCategory`.

Why rules and not the LLM: the admin intake runs over tens of thousands of rows
per batch and every row already carries a title, tag list and description, so a
keyword scorer gets the label for free. `llm_enrich.py` remains the escape hatch
for rows this returns "Other / General" for.

Scoring: each sub-category owns a list of keyword phrases. A phrase that matches
scores `field_weight * word_count`, so a two-word phrase ("retail sales") always
outranks the single word ("sales") it contains and specific labels beat generic
ones without hand-tuned priorities. Fields are weighted title > tags >
description because a title is what the job *is*, a description merely mentions.
"""
import re

# (category, sub-category, keyword phrases). Order matters only for exact ties.
TAXONOMY = [
    # 1. Information Technology & Software
    ("Information Technology & Software", "Software Development", ["software engineer", "software developer", "software development", "sde", "programmer", "software architect", "golang", "java developer", ".net developer", "c++"]),
    ("Information Technology & Software", "Web Development", ["web developer", "web development", "webmaster", "wordpress", "php developer"]),
    ("Information Technology & Software", "Mobile App Development", ["mobile developer", "mobile app", "android developer", "ios developer", "react native", "flutter", "swift developer", "kotlin"]),
    ("Information Technology & Software", "Frontend Development", ["frontend", "front end developer", "front-end", "react developer", "angular", "vue.js", "javascript developer", "ui developer"]),
    ("Information Technology & Software", "Backend Development", ["backend", "back end developer", "back-end", "api developer", "microservices", "node.js developer", "django", "spring boot"]),
    ("Information Technology & Software", "Full Stack Development", ["full stack", "fullstack", "full-stack"]),
    ("Information Technology & Software", "DevOps & Cloud", ["devops", "site reliability", "sre", "cloud engineer", "cloud architect", "kubernetes", "terraform", "aws engineer", "azure engineer", "platform engineer", "infrastructure engineer", "ci/cd"]),
    ("Information Technology & Software", "QA & Testing", ["qa engineer", "quality engineer", "test engineer", "software tester", "sdet", "automation testing", "manual testing", "test automation"]),
    ("Information Technology & Software", "Data Science & Analytics", ["data scientist", "data science", "data analyst", "data analytics", "business intelligence", "bi developer", "data engineer", "analytics engineer", "big data", "power bi", "tableau"]),
    ("Information Technology & Software", "Artificial Intelligence & Machine Learning", ["machine learning", "artificial intelligence", "deep learning", "ml engineer", "ai engineer", "nlp", "computer vision", "generative ai", "llm"]),
    ("Information Technology & Software", "Cybersecurity", ["cybersecurity", "cyber security", "information security", "infosec", "security engineer", "penetration test", "soc analyst", "application security", "threat intelligence"]),
    ("Information Technology & Software", "Database Administration", ["database administrator", "dba", "sql server", "oracle dba", "postgres", "database engineer"]),
    ("Information Technology & Software", "IT Support & Helpdesk", ["it support", "desktop support", "service desk", "it technician", "field technician it", "system administrator", "sysadmin"]),
    ("Information Technology & Software", "Networking", ["network engineer", "network administrator", "network technician", "ccna", "cisco", "lan/wan", "telecom engineer"]),
    ("Information Technology & Software", "Blockchain & Web3", ["blockchain", "web3", "solidity", "smart contract", "crypto engineer", "defi"]),
    ("Information Technology & Software", "UI/UX Design", ["ux designer", "ui/ux", "user experience designer", "ux researcher", "interaction designer", "product designer ux"]),
    ("Information Technology & Software", "Product Management", ["product manager", "product management", "senior product manager", "group product manager"]),
    ("Information Technology & Software", "Technical Project Management", ["technical program manager", "technical project manager", "scrum master", "agile coach", "engineering manager", "delivery manager", "product owner"]),

    # 2. Sales & Business Development
    ("Sales & Business Development", "Sales", ["sales representative", "sales associate", "salesperson", "sales executive", "sales consultant", "sales manager", "sales agent"]),
    ("Sales & Business Development", "Business Development", ["business development", "bdm", "bde", "growth manager", "partnerships manager"]),
    ("Sales & Business Development", "Inside Sales", ["inside sales", "sdr", "sales development representative", "bdr"]),
    ("Sales & Business Development", "Field Sales", ["field sales", "territory sales", "outside sales", "area sales"]),
    ("Sales & Business Development", "B2B Sales", ["b2b sales", "enterprise sales", "corporate sales"]),
    ("Sales & Business Development", "B2C Sales", ["b2c sales", "direct sales", "consumer sales"]),
    ("Sales & Business Development", "Account Management", ["account manager", "account executive", "account management"]),
    ("Sales & Business Development", "Key Account Management", ["key account", "strategic account", "national account manager"]),
    ("Sales & Business Development", "Sales Operations", ["sales operations", "sales ops", "revenue operations", "sales enablement"]),
    ("Sales & Business Development", "Pre-Sales", ["pre-sales", "presales", "solution consultant", "sales engineer", "solutions architect sales"]),
    ("Sales & Business Development", "Channel Sales", ["channel sales", "channel partner", "reseller", "distributor sales"]),
    ("Sales & Business Development", "Customer Acquisition", ["customer acquisition", "lead generation", "demand generation"]),

    # 3. Marketing & Advertising
    ("Marketing & Advertising", "Digital Marketing", ["digital marketing", "online marketing", "growth marketing", "marketing specialist", "marketing manager"]),
    ("Marketing & Advertising", "SEO", ["seo", "search engine optimization", "sem specialist", "organic search"]),
    ("Marketing & Advertising", "Social Media Marketing", ["social media", "community manager", "instagram marketing", "social media manager"]),
    ("Marketing & Advertising", "Content Marketing", ["content marketing", "content strategist", "content manager", "blog writer"]),
    ("Marketing & Advertising", "Email Marketing", ["email marketing", "crm marketing", "lifecycle marketing", "marketing automation"]),
    ("Marketing & Advertising", "Performance Marketing", ["performance marketing", "paid media", "ppc", "google ads", "paid search", "media buyer"]),
    ("Marketing & Advertising", "Brand Management", ["brand manager", "brand management", "brand strategist", "brand marketing"]),
    ("Marketing & Advertising", "Product Marketing", ["product marketing", "gtm manager", "go-to-market"]),
    ("Marketing & Advertising", "Marketing Communications", ["marketing communications", "marcom", "communications manager"]),
    ("Marketing & Advertising", "Market Research", ["market research", "consumer insights", "market analyst"]),
    ("Marketing & Advertising", "Public Relations", ["public relations", "pr manager", "media relations", "corporate communications"]),
    ("Marketing & Advertising", "Influencer Marketing", ["influencer marketing", "creator partnerships", "affiliate marketing"]),
    ("Marketing & Advertising", "Graphic Design", ["graphic designer", "graphic design", "visual designer"]),
    ("Marketing & Advertising", "Copywriting", ["copywriter", "copywriting", "content writer", "technical writer", "ux writer"]),

    # 4. Finance & Accounting
    ("Finance & Accounting", "Accounting", ["accountant", "accounting", "bookkeeper", "accounts payable", "accounts receivable", "general ledger", "cpa"]),
    ("Finance & Accounting", "Finance", ["finance manager", "financial analyst", "finance analyst", "controller", "cfo", "finance director"]),
    ("Finance & Accounting", "Auditing", ["auditor", "audit", "internal audit", "statutory audit"]),
    ("Finance & Accounting", "Taxation", ["tax analyst", "taxation", "tax manager", "indirect tax", "tax accountant"]),
    ("Finance & Accounting", "Financial Planning & Analysis", ["fp&a", "financial planning", "budgeting analyst", "forecasting analyst"]),
    ("Finance & Accounting", "Investment Banking", ["investment banking", "equity research", "m&a analyst", "capital markets"]),
    ("Finance & Accounting", "Banking", ["bank teller", "banking officer", "branch banking"]),
    ("Finance & Accounting", "Insurance", ["insurance agent", "underwriter", "claims adjuster", "actuary", "insurance sales", "policy servicing"]),
    ("Finance & Accounting", "Risk Management", ["risk manager", "risk analyst", "credit risk", "market risk", "operational risk"]),
    ("Finance & Accounting", "Credit & Collections", ["collections specialist", "credit analyst", "accounts collection", "debt recovery"]),
    ("Finance & Accounting", "Payroll", ["payroll specialist", "payroll administrator", "payroll processing"]),
    ("Finance & Accounting", "Treasury", ["treasury analyst", "treasury manager", "cash management"]),

    # 5. Human Resources
    ("Human Resources", "Recruitment", ["recruiter", "recruitment", "staffing specialist", "hiring specialist"]),
    ("Human Resources", "Talent Acquisition", ["talent acquisition", "sourcing specialist", "technical recruiter"]),
    ("Human Resources", "HR Operations", ["hr operations", "hr generalist", "hr coordinator", "hr administrator", "human resources"]),
    ("Human Resources", "HR Business Partner", ["hr business partner", "hrbp", "people partner"]),
    ("Human Resources", "Learning & Development", ["learning and development", "l&d", "training manager", "instructional designer", "talent development"]),
    ("Human Resources", "Employee Relations", ["employee relations", "labor relations", "workplace investigations"]),
    ("Human Resources", "Compensation & Benefits", ["compensation and benefits", "compensation analyst", "benefits specialist", "total rewards"]),
    ("Human Resources", "Performance Management", ["performance management", "talent management"]),
    ("Human Resources", "People Analytics", ["people analytics", "hr analytics", "workforce analytics", "hris"]),

    # 6. Engineering
    ("Engineering", "Mechanical Engineering", ["mechanical engineer", "mechanical design", "hvac engineer", "thermal engineer"]),
    ("Engineering", "Civil Engineering", ["civil engineer", "civil engineering"]),
    ("Engineering", "Electrical Engineering", ["electrical engineer", "electrician", "power systems engineer"]),
    ("Engineering", "Electronics Engineering", ["electronics engineer", "embedded engineer", "firmware engineer", "pcb design", "hardware engineer", "vlsi", "signal processing"]),
    ("Engineering", "Chemical Engineering", ["chemical engineer", "process engineer chemical", "petrochemical engineer"]),
    ("Engineering", "Industrial Engineering", ["industrial engineer", "manufacturing engineer", "process improvement engineer"]),
    ("Engineering", "Production Engineering", ["production engineer", "production engineering"]),
    ("Engineering", "Automotive Engineering", ["automotive engineer", "vehicle engineer", "powertrain engineer"]),
    ("Engineering", "Aerospace Engineering", ["aerospace engineer", "aeronautical engineer", "avionics engineer"]),
    ("Engineering", "Environmental Engineering", ["environmental engineer", "environmental compliance engineer"]),
    ("Engineering", "Structural Engineering", ["structural engineer", "structural design"]),

    # 7. Manufacturing & Production
    ("Manufacturing & Production", "Production", ["production associate", "production operator", "production worker", "assembler", "machine operator", "line operator"]),
    ("Manufacturing & Production", "Manufacturing", ["manufacturing associate", "manufacturing technician", "fabrication", "cnc", "welder", "machinist"]),
    ("Manufacturing & Production", "Operations", ["operations associate", "operations technician", "shop floor"]),
    ("Manufacturing & Production", "Quality Control", ["quality control", "qc inspector", "quality inspector"]),
    ("Manufacturing & Production", "Quality Assurance", ["quality assurance", "qa specialist", "quality technician", "quality manager"]),
    ("Manufacturing & Production", "Maintenance", ["maintenance technician", "maintenance mechanic", "maintenance associate", "facilities maintenance", "custodian", "janitorial"]),
    ("Manufacturing & Production", "Plant Management", ["plant manager", "plant supervisor", "factory manager"]),
    ("Manufacturing & Production", "Production Planning", ["production planner", "production planning", "master scheduler"]),
    ("Manufacturing & Production", "Supply Chain", ["supply chain analyst", "supply chain specialist"]),
    ("Manufacturing & Production", "Warehouse Operations", ["warehouse associate", "warehouse operations", "material handler", "forklift", "order picker", "stocker", "freight handler"]),
    ("Manufacturing & Production", "Procurement", ["procurement specialist", "sourcing manager", "buyer"]),

    # 8. Healthcare & Medical
    ("Healthcare & Medical", "Doctors", ["physician", "doctor", "surgeon", "medical officer", "consultant physician", "radiologist", "anesthesiologist", "pediatrician"]),
    ("Healthcare & Medical", "Nursing", ["registered nurse", "nurse", " rn ", "lpn", "nursing assistant", "cna", "nurse practitioner"]),
    ("Healthcare & Medical", "Pharmacy", ["pharmacist", "pharmacy technician", "pharmacy intern", "pharmacy manager", "pharmacy"]),
    ("Healthcare & Medical", "Medical Administration", ["medical receptionist", "patient access", "medical office", "patient services representative", "health unit coordinator"]),
    ("Healthcare & Medical", "Medical Research", ["medical research", "clinical trials", "biostatistician"]),
    ("Healthcare & Medical", "Healthcare Management", ["healthcare administrator", "clinical manager", "practice manager", "hospital administrator", "director of nursing"]),
    ("Healthcare & Medical", "Medical Coding", ["medical coder", "medical coding", "cpt coding", "icd-10"]),
    ("Healthcare & Medical", "Medical Billing", ["medical billing", "revenue cycle", "insurance billing"]),
    ("Healthcare & Medical", "Physiotherapy", ["physical therapist", "physiotherapist", "occupational therapist", "rehabilitation therapist"]),
    ("Healthcare & Medical", "Dentistry", ["dentist", "dental hygienist", "dental assistant"]),
    ("Healthcare & Medical", "Laboratory", ["laboratory technician", "lab technician", "medical technologist", "phlebotomist", "specimen processor", "laboratory assistant"]),
    ("Healthcare & Medical", "Healthcare Support", ["home health aide", "caregiver", "patient care technician", "medical assistant", "behavioral health technician"]),

    # 9. Education & Training
    ("Education & Training", "Teaching", ["teacher", "tutor", "lecturer", "instructor", "educator", "teaching assistant", "preschool teacher"]),
    ("Education & Training", "School Administration", ["principal", "school administrator", "assistant director school", "school counselor", "registrar"]),
    ("Education & Training", "College & University", ["professor", "faculty", "university", "adjunct", "dean"]),
    ("Education & Training", "Online Teaching", ["online teacher", "online tutor", "e-learning instructor", "virtual instructor"]),
    ("Education & Training", "Corporate Training", ["corporate trainer", "training specialist", "sales trainer", "technical trainer"]),
    ("Education & Training", "Coaching", ["coach", "mentor", "career coach"]),
    ("Education & Training", "Curriculum Development", ["curriculum developer", "curriculum design", "instructional design"]),
    ("Education & Training", "Academic Research", ["research scholar", "postdoctoral", "academic researcher", "research fellow"]),
    ("Education & Training", "Education Management", ["education manager", "academic director", "center director", "childcare director"]),

    # 10. Customer Service & Support
    ("Customer Service & Support", "Customer Support", ["customer support", "customer service representative", "customer service associate", "customer care", "support specialist", "customer service"]),
    ("Customer Service & Support", "Customer Success", ["customer success", "client success", "onboarding specialist"]),
    ("Customer Service & Support", "Technical Support", ["technical support", "tech support", "product support engineer"]),
    ("Customer Service & Support", "Call Center", ["call center", "contact center", "bpo"]),
    ("Customer Service & Support", "Helpdesk", ["help desk", "helpdesk"]),
    ("Customer Service & Support", "Client Relationship Management", ["client relationship", "relationship manager", "client servicing"]),
    ("Customer Service & Support", "Customer Experience", ["customer experience", "cx specialist", "customer experience representative"]),
    ("Customer Service & Support", "Telecalling", ["telecaller", "telecalling", "telesales", "telemarketing"]),

    # 11. Operations & Administration
    ("Operations & Administration", "Operations Management", ["operations manager", "operations director", "head of operations", "operations lead", "general manager operations"]),
    ("Operations & Administration", "Office Administration", ["office administrator", "administrative assistant", "receptionist", "clerk administrative", "office assistant"]),
    ("Operations & Administration", "Business Operations", ["business operations", "biz ops", "strategy and operations"]),
    ("Operations & Administration", "Data Entry", ["data entry", "data operator"]),
    ("Operations & Administration", "Back Office", ["back office", "backend operations"]),
    ("Operations & Administration", "Facility Management", ["facility manager", "facilities management", "building manager"]),
    ("Operations & Administration", "Executive Assistant", ["executive assistant", "personal assistant", "chief of staff"]),
    ("Operations & Administration", "Office Management", ["office manager", "workplace coordinator"]),
    ("Operations & Administration", "Process Management", ["process manager", "process excellence", "business process", "continuous improvement"]),

    # 12. Supply Chain & Logistics
    ("Supply Chain & Logistics", "Supply Chain Management", ["supply chain manager", "supply chain director", "supply chain"]),
    ("Supply Chain & Logistics", "Logistics", ["logistics", "logistics coordinator", "3pl"]),
    ("Supply Chain & Logistics", "Transportation", ["truck driver", "cdl", "driver", "delivery driver", "transportation", "dispatcher", "courier", "chauffeur"]),
    ("Supply Chain & Logistics", "Warehouse", ["warehouse", "distribution center associate"]),
    ("Supply Chain & Logistics", "Inventory Management", ["inventory", "inventory control", "stock controller", "cycle count"]),
    ("Supply Chain & Logistics", "Procurement", ["procurement", "purchase manager", "vendor management"]),
    ("Supply Chain & Logistics", "Purchasing", ["purchasing agent", "purchasing specialist"]),
    ("Supply Chain & Logistics", "Distribution", ["distribution manager", "distribution operations"]),
    ("Supply Chain & Logistics", "Import & Export", ["import export", "customs broker", "freight forwarding", "trade compliance"]),
    ("Supply Chain & Logistics", "Fleet Management", ["fleet manager", "fleet operations"]),

    # 13. Legal
    ("Legal", "Corporate Law", ["corporate counsel", "corporate lawyer", "general counsel", "attorney corporate"]),
    ("Legal", "Litigation", ["litigation", "trial attorney", "advocate litigation"]),
    ("Legal", "Legal Compliance", ["compliance officer", "regulatory compliance", "legal compliance", "compliance analyst"]),
    ("Legal", "Intellectual Property", ["intellectual property", "patent attorney", "trademark", "ip counsel"]),
    ("Legal", "Contract Management", ["contract manager", "contract administrator", "contracts specialist"]),
    ("Legal", "Legal Advisory", ["legal advisor", "legal consultant", "legal counsel"]),
    ("Legal", "Company Secretary", ["company secretary", "corporate secretary"]),
    ("Legal", "Paralegal", ["paralegal", "legal assistant"]),
    ("Legal", "Legal Operations", ["legal operations", "legal ops"]),

    # 14. Construction & Real Estate
    ("Construction & Real Estate", "Construction", ["construction worker", "construction manager", "construction laborer", "carpenter", "mason", "plumber", "hvac technician", "roofer", "concrete"]),
    ("Construction & Real Estate", "Civil Projects", ["civil project", "infrastructure project", "highway project"]),
    ("Construction & Real Estate", "Architecture", ["architect building", "architectural designer"]),
    ("Construction & Real Estate", "Real Estate Sales", ["real estate agent", "realtor", "real estate sales", "leasing consultant"]),
    ("Construction & Real Estate", "Property Management", ["property manager", "property management", "community manager property"]),
    ("Construction & Real Estate", "Site Engineering", ["site engineer", "site supervisor", "field engineer construction"]),
    ("Construction & Real Estate", "Quantity Surveying", ["quantity surveyor", "estimator construction", "cost estimator"]),
    ("Construction & Real Estate", "Interior Design", ["interior designer", "interior decoration"]),
    ("Construction & Real Estate", "Construction Project Management", ["project manager construction", "project engineer construction"]),
    ("Construction & Real Estate", "Real Estate Operations", ["real estate operations", "real estate analyst"]),

    # 15. Hospitality & Travel
    ("Hospitality & Travel", "Hotel Management", ["hotel manager", "hospitality manager", "resort manager", "general manager hotel"]),
    ("Hospitality & Travel", "Front Office", ["front desk", "front office", "guest service", "concierge"]),
    ("Hospitality & Travel", "Housekeeping", ["housekeeping", "housekeeper", "room attendant"]),
    ("Hospitality & Travel", "Food & Beverage", ["food and beverage", "f&b", "server restaurant", "barista", "bartender", "waiter", "food service", "café associate", "cafe associate", "deli associate", "prepared food", "prepared meals", "rotisserie", "bakery"]),
    ("Hospitality & Travel", "Restaurant Management", ["restaurant manager", "restaurant general manager", "shift leader restaurant"]),
    ("Hospitality & Travel", "Chefs & Kitchen", ["chef", "cook", "kitchen", "sous chef", "line cook", "dishwasher"]),
    ("Hospitality & Travel", "Travel & Tourism", ["travel agent", "tour", "tourism", "travel consultant"]),
    ("Hospitality & Travel", "Airlines", ["airline", "flight attendant"]),
    ("Hospitality & Travel", "Airport Operations", ["airport operations", "ramp agent", "baggage handler"]),
    ("Hospitality & Travel", "Event Management", ["event manager", "event coordinator", "event planner", "banquet"]),

    # 16. Retail & E-commerce
    ("Retail & E-commerce", "Retail Sales", ["retail sales", "sales consultant retail", "beauty advisor", "beauty sales", "sales floor associate"]),
    ("Retail & E-commerce", "Store Management", ["store manager", "assistant store manager", "store lead", "shift manager retail", "manager in training"]),
    ("Retail & E-commerce", "E-commerce", ["e-commerce", "ecommerce", "online store", "marketplace seller"]),
    ("Retail & E-commerce", "Merchandising", ["merchandiser", "merchandising"]),
    ("Retail & E-commerce", "Retail Operations", ["retail associate", "store associate", "retail operations", "cashier", "sales associate retail", "personal shopper", "cart attendant", "front end associate", "clerk", "produce", "grocery", "bagger", "checkout"]),
    ("Retail & E-commerce", "Visual Merchandising", ["visual merchandiser", "visual merchandising", "window display"]),
    ("Retail & E-commerce", "Inventory", ["stocking associate", "replenishment", "backroom associate"]),
    ("Retail & E-commerce", "Category Management", ["category manager", "category management"]),
    ("Retail & E-commerce", "Marketplace Operations", ["marketplace operations", "seller support", "amazon marketplace"]),

    # 17. Media & Entertainment
    ("Media & Entertainment", "Journalism", ["journalist", "reporter", "news editor", "correspondent", "news writer"]),
    ("Media & Entertainment", "Content Creation", ["content creator", "youtuber", "podcast", "social content producer"]),
    ("Media & Entertainment", "Video Production", ["video producer", "videographer", "video production"]),
    ("Media & Entertainment", "Photography", ["photographer", "photography"]),
    ("Media & Entertainment", "Film & Television", ["film", "television production", "screenwriter", "director of photography"]),
    ("Media & Entertainment", "Animation", ["animator", "animation", "2d animation"]),
    ("Media & Entertainment", "VFX", ["vfx", "visual effects", "compositor"]),
    ("Media & Entertainment", "Music", ["musician", "music producer", "sound engineer", "audio engineer"]),
    ("Media & Entertainment", "Radio", ["radio jockey", "radio producer", "broadcast radio"]),
    ("Media & Entertainment", "Editing", ["video editor", "film editor", "copy editor", "editor lifestyle"]),
    ("Media & Entertainment", "Broadcasting", ["broadcast", "broadcasting", "news anchor"]),

    # 18. Design & Creative
    ("Design & Creative", "Product Design", ["product designer", "product design"]),
    ("Design & Creative", "Fashion Design", ["fashion designer", "apparel design", "textile design"]),
    ("Design & Creative", "Industrial Design", ["industrial designer", "industrial design"]),
    ("Design & Creative", "Motion Graphics", ["motion graphics", "motion designer"]),
    ("Design & Creative", "Illustration", ["illustrator artist", "illustration"]),
    ("Design & Creative", "3D Design", ["3d designer", "3d artist", "3d modeling"]),
    ("Design & Creative", "Creative Direction", ["creative director", "art director", "design director"]),

    # 19. Pharmaceutical & Biotechnology
    ("Pharmaceutical & Biotechnology", "Pharmaceutical", ["pharmaceutical", "pharma manufacturing", "formulation"]),
    ("Pharmaceutical & Biotechnology", "Biotechnology", ["biotechnology", "bioprocess", "biotech"]),
    ("Pharmaceutical & Biotechnology", "Clinical Research", ["clinical research", "clinical trial", "cra ", "clinical data manager"]),
    ("Pharmaceutical & Biotechnology", "Drug Safety", ["drug safety", "pharmacovigilance"]),
    ("Pharmaceutical & Biotechnology", "Regulatory Affairs", ["regulatory affairs", "regulatory submissions"]),
    ("Pharmaceutical & Biotechnology", "Medical Affairs", ["medical affairs", "medical science liaison", "msl"]),
    ("Pharmaceutical & Biotechnology", "Research & Development", ["r&d scientist", "product development scientist"]),

    # 20. Science & Research
    ("Science & Research", "Physics", ["physicist", "physics"]),
    ("Science & Research", "Chemistry", ["chemist", "analytical chemistry", "chemistry"]),
    ("Science & Research", "Biology", ["biologist", "microbiologist", "molecular biology"]),
    ("Science & Research", "Mathematics", ["mathematician", "quantitative researcher"]),
    ("Science & Research", "Environmental Science", ["environmental scientist", "environmental science", "ecologist"]),
    ("Science & Research", "Scientific Research", ["research scientist", "scientific research", "principal investigator"]),
    ("Science & Research", "Laboratory Research", ["research associate lab", "laboratory research", "research technician"]),

    # 21. Architecture & Planning
    ("Architecture & Planning", "Architecture", ["architecture firm", "project architect"]),
    ("Architecture & Planning", "Urban Planning", ["urban planner", "city planner", "town planning"]),
    ("Architecture & Planning", "Landscape Architecture", ["landscape architect"]),
    ("Architecture & Planning", "Interior Architecture", ["interior architect"]),
    ("Architecture & Planning", "Building Design", ["building designer", "building design"]),
    ("Architecture & Planning", "CAD/BIM", ["autocad", "bim", "revit", "cad designer", "draftsman"]),

    # 22. Government & Public Sector
    ("Government & Public Sector", "Government Jobs", ["government job", "public sector", "federal agency"]),
    ("Government & Public Sector", "Public Administration", ["public administration", "city administrator", "county administrator"]),
    ("Government & Public Sector", "Civil Services", ["civil services", "civil servant"]),
    ("Government & Public Sector", "Defence", ["defence", "defense contractor", "military"]),
    ("Government & Public Sector", "Police", ["police officer", "law enforcement", "sheriff", "correctional officer"]),
    ("Government & Public Sector", "Public Works", ["public works", "municipal engineer"]),
    ("Government & Public Sector", "Municipal Services", ["municipal", "sanitation worker city"]),

    # 23. Agriculture & Farming
    ("Agriculture & Farming", "Agriculture", ["agriculture", "agronomist", "crop"]),
    ("Agriculture & Farming", "Agribusiness", ["agribusiness", "agri sales"]),
    ("Agriculture & Farming", "Farming", ["farm worker", "farm hand", "farming", "farm manager"]),
    ("Agriculture & Farming", "Horticulture", ["horticulture", "nursery grower", "greenhouse"]),
    ("Agriculture & Farming", "Dairy", ["dairy", "milk processing"]),
    ("Agriculture & Farming", "Fisheries", ["fisheries", "aquaculture"]),
    ("Agriculture & Farming", "Agricultural Engineering", ["agricultural engineer"]),
    ("Agriculture & Farming", "Food Processing", ["food processing", "food production", "food safety technician", "food scientist"]),
    ("Agriculture & Farming", "Veterinary", ["veterinary", "veterinarian", "vet technician"]),
    ("Agriculture & Farming", "Agricultural Research", ["agricultural research", "seed research"]),

    # 24. Security & Safety
    ("Security & Safety", "Security Services", ["security guard", "security officer", "loss prevention", "asset protection"]),
    ("Security & Safety", "Security Management", ["security manager", "head of security"]),
    ("Security & Safety", "Fire Safety", ["fire safety", "firefighter", "fire protection"]),
    ("Security & Safety", "Industrial Safety", ["industrial safety", "safety officer", "safety coordinator"]),
    ("Security & Safety", "Health & Safety", ["health and safety", "ehs", "hse", "occupational safety"]),
    ("Security & Safety", "Risk & Compliance", ["risk and compliance", "grc"]),
    ("Security & Safety", "Security Operations", ["security operations center", "security operations"]),

    # 25. Energy & Utilities
    ("Energy & Utilities", "Oil & Gas", ["oil and gas", "oil & gas", "drilling", "upstream", "refinery"]),
    ("Energy & Utilities", "Renewable Energy", ["renewable energy", "clean energy"]),
    ("Energy & Utilities", "Solar Energy", ["solar", "photovoltaic"]),
    ("Energy & Utilities", "Wind Energy", ["wind turbine", "wind energy"]),
    ("Energy & Utilities", "Electrical Utilities", ["utility lineman", "electric utility", "lineman"]),
    ("Energy & Utilities", "Power Generation", ["power plant", "power generation", "turbine operator"]),
    ("Energy & Utilities", "Power Distribution", ["power distribution", "substation", "grid operations"]),
    ("Energy & Utilities", "Energy Management", ["energy manager", "energy analyst", "energy efficiency"]),
    ("Energy & Utilities", "Utilities Operations", ["utilities operations", "water treatment operator"]),

    # 26. Automotive
    ("Automotive", "Automobile Sales", ["automobile sales", "car sales", "auto sales", "dealership sales"]),
    ("Automotive", "Vehicle Service", ["auto technician", "automotive technician", "service advisor", "auto mechanic", "tire and battery", "auto center", "lube technician"]),
    ("Automotive", "Auto Parts", ["auto parts", "parts counter", "spare parts"]),
    ("Automotive", "Dealership", ["dealership", "dealer operations"]),
    ("Automotive", "Vehicle Maintenance", ["vehicle maintenance", "fleet mechanic", "diesel mechanic"]),
    ("Automotive", "Automotive Design", ["automotive design", "vehicle design"]),

    # 27. Aviation & Aerospace
    ("Aviation & Aerospace", "Pilots", ["pilot", "first officer", "captain aircraft"]),
    ("Aviation & Aerospace", "Cabin Crew", ["cabin crew", "air hostess"]),
    ("Aviation & Aerospace", "Ground Staff", ["ground staff", "ground handling"]),
    ("Aviation & Aerospace", "Aircraft Maintenance", ["aircraft maintenance", "aircraft mechanic", "a&p mechanic", "avionics technician"]),
    ("Aviation & Aerospace", "Air Traffic Operations", ["air traffic", "flight dispatcher"]),
    ("Aviation & Aerospace", "Aviation Management", ["aviation manager", "aviation operations"]),

    # 28. Banking & Financial Services
    ("Banking & Financial Services", "Retail Banking", ["retail banking", "personal banker", "associate banker", "branch manager bank", "teller"]),
    ("Banking & Financial Services", "Corporate Banking", ["corporate banking", "commercial banking", "business banking"]),
    ("Banking & Financial Services", "FinTech", ["fintech", "payments platform", "digital banking"]),
    ("Banking & Financial Services", "Loans & Credit", ["loan officer", "mortgage", "credit officer", "underwriting loan"]),
    ("Banking & Financial Services", "Wealth Management", ["wealth management", "private banking", "portfolio manager", "financial planner"]),
    ("Banking & Financial Services", "Relationship Management", ["relationship manager bank", "client advisor bank"]),
    ("Banking & Financial Services", "Financial Advisory", ["financial advisor", "financial consultant", "investment advisor"]),
    ("Banking & Financial Services", "Compliance & KYC", ["kyc", "aml", "anti-money laundering", "bsa analyst"]),

    # 29. Consulting
    ("Consulting", "Management Consulting", ["management consultant", "management consulting"]),
    ("Consulting", "IT Consulting", ["it consultant", "technology consultant", "erp consultant", "sap consultant", "salesforce consultant", "workday consultant", "functional analyst"]),
    ("Consulting", "Financial Consulting", ["financial consulting", "advisory services finance"]),
    ("Consulting", "HR Consulting", ["hr consultant", "people consulting"]),
    ("Consulting", "Strategy Consulting", ["strategy consultant", "strategy consulting", "corporate strategy"]),
    ("Consulting", "Business Consulting", ["business consultant", "business analyst", "business advisory"]),
    ("Consulting", "Operations Consulting", ["operations consultant", "operational excellence consulting"]),

    # 30. Other / General
    ("Other / General", "Internship", ["intern", "internship", "trainee intern"]),
    ("Other / General", "Apprenticeship", ["apprentice", "apprenticeship"]),
    ("Other / General", "Freelance", ["freelance", "freelancer"]),
    ("Other / General", "Gig Jobs", ["gig work", "on-demand work"]),
    ("Other / General", "Entry-Level Jobs", ["entry level", "entry-level", "no experience required"]),
]

CATEGORIES = {}
for _cat, _sub, _ in TAXONOMY:
    CATEGORIES.setdefault(_cat, [])
    if _sub not in CATEGORIES[_cat]:
        CATEGORIES[_cat].append(_sub)

FALLBACK_CATEGORY = "Other / General"

# Catch-all sub-categories: the one a job lands in when nothing more specific in
# the same family fits. They lose ties on equal score so "Software Engineer, Full
# Stack" is filed under Full Stack Development and not the umbrella label.
GENERIC_SUBCATEGORIES = {
    ("Information Technology & Software", "Software Development"),
    ("Sales & Business Development", "Sales"),
    ("Marketing & Advertising", "Digital Marketing"),
    ("Finance & Accounting", "Finance"),
    ("Human Resources", "HR Operations"),
    ("Manufacturing & Production", "Operations"),
    ("Manufacturing & Production", "Manufacturing"),
    ("Customer Service & Support", "Customer Support"),
    ("Supply Chain & Logistics", "Logistics"),
    ("Supply Chain & Logistics", "Warehouse"),
    ("Retail & E-commerce", "Retail Operations"),
    ("Construction & Real Estate", "Construction"),
    ("Operations & Administration", "Operations Management"),
    ("Consulting", "Business Consulting"),
    ("Agriculture & Farming", "Agriculture"),
    ("Pharmaceutical & Biotechnology", "Pharmaceutical"),
}
GENERIC_PENALTY = 0.9

# Field weights. A title match is worth ~3 tag matches and ~10 description
# mentions, because descriptions name adjacent roles ("work with our engineers")
# constantly and would otherwise drag every job into Engineering.
WEIGHT_ROLE = 30
WEIGHT_TAGS = 10
WEIGHT_DESCRIPTION = 3
# Only the opening of the description is scanned: benefits/EEO boilerplate at the
# bottom is identical across postings and adds nothing but noise.
DESCRIPTION_SCAN_CHARS = 1200

# Tags the enrichment step emits from posting boilerplate — benefits blurbs, EEO
# statements, "we invest in training" — rather than from the actual work. Left in,
# they file every hourly retail job under Human Resources.
NOISE_TAGS = {
    "compensation and benefits", "training and development", "education",
    "government", "legal", "communication", "leadership", "teamwork",
    "microsoft office", "excel", "time management", "problem solving",
}

_PATTERN_CACHE = {}


def _pattern(keyword):
    """Word-boundary match, so 'rn' doesn't fire inside 'learn' and 'sap' doesn't
    fire inside 'sapphire'. \\b fails next to '&', '+', '/' and '.', so those
    keywords fall back to a plain substring search."""
    if keyword not in _PATTERN_CACHE:
        escaped = re.escape(keyword)
        left = r"\b" if keyword[:1].isalnum() else ""
        right = r"\b" if keyword[-1:].isalnum() else ""
        _PATTERN_CACHE[keyword] = re.compile(left + escaped + right)
    return _PATTERN_CACHE[keyword]


def _normalize(value):
    text = str(value or "").lower()
    # Tag lists arrive as "['customer-service', 'retail']"; hyphens and brackets
    # would otherwise stop 'customer service' from matching 'customer-service'.
    text = re.sub(r"[\[\]'\"|_/\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_tags(tags):
    """Splits the "['a', 'b']"-style tag column and drops boilerplate tags."""
    kept = []
    for raw in re.split(r"[,\[\]'\"]+", str(tags or "")):
        tag = _normalize(raw)
        if tag and tag not in NOISE_TAGS:
            kept.append(tag)
    return " , ".join(kept)


def classify(role="", tags="", description="", company="", employment_type="", remote=""):
    """Returns (category, sub_category, score). Score is 0 when nothing matched,
    which is the caller's signal that the row is worth an LLM pass."""
    role_text = " " + _normalize(role) + " "
    tag_text = " " + _clean_tags(tags) + " "
    desc_text = " " + _normalize(description)[:DESCRIPTION_SCAN_CHARS] + " "

    best = (0.0, None, None)
    for index, (category, sub_category, keywords) in enumerate(TAXONOMY):
        score = 0
        for keyword in keywords:
            pattern = _pattern(keyword)
            # A phrase is worth its length: "retail sales" (2) beats "sales" (1)
            # on the same title without any per-rule priority bookkeeping.
            specificity = len(keyword.split())
            if pattern.search(role_text):
                score += WEIGHT_ROLE * specificity
            if pattern.search(tag_text):
                score += WEIGHT_TAGS * specificity
            if pattern.search(desc_text):
                score += WEIGHT_DESCRIPTION * specificity
        # "Other / General" holds work-arrangement labels (intern, freelance),
        # not professions, so it only wins when a real field found nothing.
        if category == FALLBACK_CATEGORY:
            score = min(score, WEIGHT_ROLE)
        if (category, sub_category) in GENERIC_SUBCATEGORIES:
            score *= GENERIC_PENALTY
        if score > best[0]:
            best = (score, category, sub_category)

    if best[0]:
        return best[1], best[2], round(best[0], 1)
    return FALLBACK_CATEGORY, _arrangement_subcategory(employment_type, remote), 0


def _arrangement_subcategory(employment_type, remote):
    """Last resort: describe how the job is worked when we can't tell what it is."""
    employment = _normalize(employment_type)
    if "intern" in employment:
        return "Internship"
    if "part time" in employment or "part-time" in str(employment_type).lower():
        return "Part-time"
    if "contract" in employment:
        return "Contract Jobs"
    if "temporary" in employment or "temp" == employment:
        return "Temporary Jobs"
    if str(remote or "").strip().lower() in ("true", "1", "yes", "y"):
        return "Remote Jobs"
    return "Not Specified"
