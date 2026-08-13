// Types mirror the structured JSON envelopes each engine returns,
// per the README: every engine response is validated (Pydantic) before
// it reaches the frontend, so these shapes should track the API schemas.

export type EngineName =
  | "resume_intelligence"
  | "job_match"
  | "career_health"
  | "career_copilot";

export interface User {
  id: string;
  email: string;
  name: string;
  headline?: string;
}

export interface ResumeSuggestion {
  id: string;
  category: "structure" | "keywords" | "impact" | "clarity";
  message: string;
}

export interface Resume {
  id: string;
  filename: string;
  status: "uploading" | "uploaded" | "parsing" | "parsed" | "processing" | "scored" | "failed";
  atsScore?: number; // 0-100
  resumeScore?: number; // 0-100
  skills: string[];
  structuredData: {
    work_history?: Record<string, unknown>[];
    education?: Record<string, unknown>[];
    [key: string]: unknown;
  };
  suggestions: ResumeSuggestion[];
  uploadedAt: string;
}

export interface JobMatch {
  id: string;
  title: string;
  company: string;
  location: string;
  matchScore: number; // 0-100 cosine-derived
  visaFriendly: boolean;
  salaryFit: boolean;
  explanation: string;
  matchedSkills: string[];
  missingSkills: string[];
  recommendation: "strong_fit" | "possible_fit" | "weak_fit" | "stretch" | string;
  hardConstraintsSatisfied: boolean;
}

export interface CareerHealth {
  score: number; // 0-100 aggregate
  trend: "up" | "down" | "flat";
  trendDeltaPct: number;
  weakAreas: string[];
  benchmarks: Record<string, unknown>;
  recommendations: string[];
  history: { label: string; score: number }[];
}

export interface CopilotMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  conversationId?: string;
  suggestedActions?: string[];
}

export interface ApplicationStage {
  id: string;
  jobTitle: string;
  company: string;
  stage: "applied" | "screening" | "interview" | "offer" | "rejected";
  updatedAt: string;
}
