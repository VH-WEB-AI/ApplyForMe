import type {
  ApplicationStage,
  CareerHealth,
  CopilotMessage,
  JobMatch,
  Resume,
  User,
} from "./types";

export const mockUser: User = {
  id: "usr_01",
  email: "candidate@example.com",
  name: "Shivani Rao",
  headline: "Full-stack engineer, ML pipelines & automation",
};

export const mockResume: Resume = {
  id: "res_01",
  filename: "Shivani_Rao_Resume.pdf",
  status: "scored",
  atsScore: 78,
  resumeScore: 82,
  skills: ["Python", "FastAPI", "React", "TypeScript", "Docker", "Postgres"],
  structuredData: {
    work_history: [
      { company: "Northwind Labs", title: "Full-stack Engineer", start: "2023", end: "Present", summary: "Built AI workflow tooling and backend APIs." },
    ],
    education: [
      { institution: "State University", degree: "B.Tech", field: "Computer Science", year: "2022" },
    ],
  },
  suggestions: [
    { id: "s1", category: "keywords", message: "Add measurable outcomes to your top 2 bullet points — recruiters and ATS both weight quantified impact higher." },
    { id: "s2", category: "structure", message: "Move your skills section above your earliest role — it's currently below the fold for a 6-second scan." },
    { id: "s3", category: "clarity", message: "\"Worked on backend systems\" is vague. Name the system and the scale it operated at." },
  ],
  uploadedAt: "2026-07-28T09:12:00Z",
};

export const mockJobMatches: JobMatch[] = [
  {
    id: "job_01",
    title: "Senior Backend Engineer",
    company: "Northwind Labs",
    location: "Remote (India)",
    matchScore: 91,
    visaFriendly: true,
    salaryFit: true,
    explanation: "Strong overlap on FastAPI, async Postgres, and Celery — three of your five most-used skills appear as hard requirements.",
    matchedSkills: ["FastAPI", "Postgres", "Celery", "Docker"],
    missingSkills: ["Kubernetes"],
    recommendation: "strong_fit",
    hardConstraintsSatisfied: true,
  },
  {
    id: "job_02",
    title: "AI Platform Engineer",
    company: "Verdant Systems",
    location: "Bengaluru, India",
    matchScore: 84,
    visaFriendly: true,
    salaryFit: true,
    explanation: "Good fit on RAG pipeline and embeddings experience; role also values Docker orchestration, which your resume covers.",
    matchedSkills: ["RAG", "Embeddings", "Docker", "Python"],
    missingSkills: ["Model evaluation"],
    recommendation: "strong_fit",
    hardConstraintsSatisfied: true,
  },
  {
    id: "job_03",
    title: "Full-Stack Developer",
    company: "Harborline",
    location: "Pune, India",
    matchScore: 69,
    visaFriendly: true,
    salaryFit: false,
    explanation: "Solid technical overlap on React and TypeScript, but the posted salary band sits below your stated minimum.",
    matchedSkills: ["React", "TypeScript"],
    missingSkills: ["GraphQL"],
    recommendation: "possible_fit",
    hardConstraintsSatisfied: false,
  },
];

export const mockCareerHealth: CareerHealth = {
  score: 74,
  trend: "up",
  trendDeltaPct: 6,
  weakAreas: ["Interview follow-up cadence", "Portfolio breadth outside current stack"],
  benchmarks: {
    latest_resume_score: 82,
    average_match_score: 0.81,
    total_applications: 3,
  },
  recommendations: [
    "You have 3 applications with no follow-up after 10+ days — a short nudge message tends to lift response rates.",
    "Add one project outside your primary stack; recruiters searching for 'full-stack + ML' currently see a gap.",
  ],
  history: [
    { label: "Apr", score: 58 },
    { label: "May", score: 63 },
    { label: "Jun", score: 68 },
    { label: "Jul", score: 74 },
  ],
};

export const mockCopilotThread: CopilotMessage[] = [
  {
    id: "m1",
    role: "assistant",
    content: "I've pulled your latest resume score and job match history. What do you want to work on — tightening the resume, prepping for an interview, or reviewing a specific match?",
    createdAt: "2026-07-30T10:00:00Z",
    suggestedActions: ["Review resume suggestions", "Run job match", "Create interview prep plan"],
  },
];

export const mockApplications: ApplicationStage[] = [
  { id: "app_01", jobTitle: "Senior Backend Engineer", company: "Northwind Labs", stage: "interview", updatedAt: "2026-07-29T14:00:00Z" },
  { id: "app_02", jobTitle: "AI Platform Engineer", company: "Verdant Systems", stage: "screening", updatedAt: "2026-07-27T11:00:00Z" },
  { id: "app_03", jobTitle: "Full-Stack Developer", company: "Harborline", stage: "applied", updatedAt: "2026-07-22T09:00:00Z" },
];
